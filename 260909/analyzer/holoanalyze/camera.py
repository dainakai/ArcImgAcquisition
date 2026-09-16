"""SDK loading is deferred until Connect; simulation needs no native library."""
import ctypes as ct
from pathlib import Path
import os
import sys
import threading
import time

import numpy as np

from .data import Frame, ImagePair, Session


class FrameView(ct.Structure):
    _fields_ = [("data", ct.c_void_p), ("width", ct.c_int), ("height", ct.c_int), ("bits", ct.c_int),
                ("id", ct.c_uint64), ("camera_ns", ct.c_uint64), ("host_ns", ct.c_int64),
                ("exposure_ns", ct.c_int64), ("uncertainty_ms", ct.c_double), ("serial", ct.c_char*64)]


class Status(ct.Structure):
    _fields_ = [(n, ct.c_uint64*2) for n in ("received", "saved", "gaps", "incomplete")] + [
        ("pending", ct.c_uint64), ("waits", ct.c_uint64), ("recording", ct.c_int), ("stopped", ct.c_int)]


def library_path(config):
    if config.camera_library:
        return Path(config.camera_library)
    if os.environ.get("HOLO_CAPTURE_LIBRARY"):
        return Path(os.environ["HOLO_CAPTURE_LIBRARY"])
    name = "holo_capture.dll" if sys.platform == "win32" else "libholo_capture.dylib" if sys.platform == "darwin" else "libholo_capture.so"
    root = Path(__file__).resolve().parents[1]
    roots = [root / "native", Path(getattr(sys, "_MEIPASS", root)) / "native",
             root.parent / "build" / "analyzer", root.parent / "build" / "analyzer" / "Release"]
    for directory in roots:
        if (directory / name).is_file():
            return directory / name
    raise FileNotFoundError("Camera bridge is not installed. Build target holo_capture and set camera_library in YAML. Offline and Simulate remain available.")


class NativeCamera:
    def __init__(self, config, session, simulate=False):
        self.handle = None
        self.library = ct.CDLL(str(library_path(config)))
        l = self.library
        l.holo_open.argtypes = [ct.c_char_p]*3 + [ct.c_double, ct.c_double, ct.c_int, ct.c_char_p, ct.c_size_t]
        l.holo_open.restype = ct.c_void_p
        l.holo_close.argtypes = [ct.c_void_p]
        l.holo_close.restype = None
        l.holo_snapshot.argtypes = [ct.c_void_p, ct.c_uint64, ct.POINTER(ct.c_uint64), ct.POINTER(ct.c_double)]
        l.holo_snapshot.restype = ct.c_void_p
        l.holo_frame.argtypes = [ct.c_void_p, ct.c_int, ct.POINTER(FrameView)]
        l.holo_frame.restype = ct.c_int
        l.holo_release.argtypes = [ct.c_void_p]
        l.holo_release.restype = None
        l.holo_record.argtypes = [ct.c_void_p, ct.c_int]
        l.holo_record.restype = ct.c_int
        l.holo_status.argtypes = [ct.c_void_p, ct.POINTER(Status), ct.c_char_p, ct.c_size_t]
        l.holo_status.restype = None
        error = ct.create_string_buffer(8192)
        self.handle = l.holo_open(config.serial0.encode(), config.serial1.encode(),
                                  str(session.ensure(config)).encode("utf-8"), config.pair_tolerance_ms,
                                  config.max_clock_uncertainty_ms, int(simulate), error, len(error))
        if not self.handle:
            raise RuntimeError(error.value.decode("utf-8", errors="replace"))
        self.sequence = 0

    def poll(self):
        sequence, age = ct.c_uint64(), ct.c_double()
        snap = self.library.holo_snapshot(self.handle, self.sequence, ct.byref(sequence), ct.byref(age))
        if not snap:
            return None
        try:
            self.sequence = sequence.value
            if age.value > 300:  # stale trigger must never be captured as a fresh pair
                return None
            frames = []
            for camera in range(2):
                view = FrameView()
                if not self.library.holo_frame(snap, camera, ct.byref(view)):
                    raise RuntimeError("Could not read paired camera frame")
                if view.bits not in (8, 16) or not 0 < view.width <= 4096 or not 0 < view.height <= 4096:
                    raise ValueError("Unsupported camera dimensions or pixel type")
                dtype = ct.c_uint8 if view.bits == 8 else ct.c_uint16
                array = np.ctypeslib.as_array(ct.cast(view.data, ct.POINTER(dtype)),
                                              shape=(view.height, view.width)).copy()
                frames.append(Frame(array, view.serial.decode(), view.id, view.exposure_ns,
                                    view.host_ns, view.camera_ns, view.uncertainty_ms))
            return ImagePair(tuple(frames), origin="camera")
        finally:
            self.library.holo_release(snap)

    def status(self):
        value, error = Status(), ct.create_string_buffer(8192)
        self.library.holo_status(self.handle, ct.byref(value), error, len(error))
        return dict(recording=bool(value.recording), stopped=bool(value.stopped), saved=list(value.saved),
                    pending=value.pending, gaps=list(value.gaps), waits=value.waits,
                    error=error.value.decode("utf-8", errors="replace"))

    def record(self, enabled):
        self.library.holo_record(self.handle, enabled)

    def close(self):
        if self.handle:
            self.library.holo_close(self.handle)
            self.handle = None


class SimulatedCamera:
    """Analytic moving fringes (no optical propagation). One CPU/I/O worker."""
    def __init__(self, config, session):
        self.config, self.session = config, session
        self.lock = threading.Lock()
        self.latest = None
        self.sequence = 0
        self.recording = False
        self.saved = [0, 0]
        self.error = ""
        self.stop_event = threading.Event()
        self.writer = None
        self.thread = threading.Thread(target=self._run, name="holo-simulator", daemon=False)
        self.thread.start()

    def _run(self):
        import csv
        import tifffile
        from .data import stamp
        y, x = np.indices((480, 640), dtype=np.float32)
        file = None
        previous_recording = False
        try:
            while not self.stop_event.is_set():
                start = time.monotonic()
                ns = time.monotonic_ns()
                self.sequence += 1
                frames = []
                for camera in range(2):
                    radius = (x-320-30*np.sin(self.sequence*.05)-camera*8)**2 + (y-240-camera*4)**2
                    im = np.clip(150+60*np.cos(radius*.0015)*np.exp(-radius/60000), 0, 255).astype(np.uint8)
                    frames.append(Frame(im, getattr(self.config, f"serial{camera}"), self.sequence, ns, ns, ns))
                with self.lock:
                    self.latest = ImagePair(tuple(frames), origin="simulation")
                    recording = self.recording
                    if recording and not previous_recording:
                        directory = self.session.ensure(self.config) / ("recording_" + stamp())
                        directory.mkdir()
                        (directory / "IN_PROGRESS").write_text("simulation recording", encoding="utf-8")
                        file = (directory / "frames.csv").open("w", newline="", encoding="utf-8")
                        writer = csv.writer(file)
                        writer.writerow(["file", "camera", "index", "serial", "frame_id", "camera_ns", "host_received_ns",
                                         "estimated_exposure_host_ns", "clock_uncertainty_ms", "source_format", "stored_bits", "full_scale", "record_admitted_ns"])
                        index = 0
                    if recording:
                        for camera, frame in enumerate(frames):
                            rel = f"cam{camera}_{frame.serial}/frame_{index:06d}_id{frame.frame_id}.tiff"
                            path = directory / rel
                            path.parent.mkdir(exist_ok=True)
                            tifffile.imwrite(path, frame.image, compression=None)
                            writer.writerow([rel, camera, index, frame.serial, frame.frame_id, ns, ns, ns, 0, "Mono8", 8, 255, ns])
                            self.saved[camera] += 1
                        file.flush()
                        index += 1
                    if previous_recording and not recording:
                        file.close()
                        file = None
                        (directory / "IN_PROGRESS").unlink()
                    previous_recording = recording
                self.stop_event.wait(max(0, .1-(time.monotonic()-start)))
        except Exception as exc:
            self.error = str(exc)
        finally:
            if file:
                file.close()
                if not self.error:
                    (directory / "IN_PROGRESS").unlink(missing_ok=True)

    def poll(self):
        with self.lock:
            pair = self.latest
            self.latest = None
            return pair

    def status(self):
        return dict(recording=self.recording, saved=self.saved[:], pending=0, waits=0, gaps=[0, 0],
                    stopped=self.stop_event.is_set() or bool(self.error), error=self.error)

    def record(self, enabled):
        with self.lock:
            self.recording = enabled

    def close(self):
        self.stop_event.set()
        self.thread.join()
