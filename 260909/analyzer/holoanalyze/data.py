"""Raw pairs, exact integer timestamp matching, and explicit-only saving."""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
import csv
import hashlib
import json
import re
import uuid

import numpy as np
import tifffile


def stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def read_image(path):
    path = Path(path)
    if path.suffix.lower() in (".tif", ".tiff"):
        im = tifffile.imread(path)
    else:
        import cv2
        im = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if im is None or im.ndim != 2 or im.size == 0:
        raise ValueError(f"A single monochrome image is required: {path}")
    if max(im.shape) > 4096:
        raise ValueError("Images larger than 4096 pixels are not supported; no automatic resizing")
    if not np.issubdtype(im.dtype, np.number) or np.iscomplexobj(im) or not np.isfinite(im).all() or im.min() < 0:
        raise ValueError("Image intensities must be real, finite and nonnegative")
    return np.ascontiguousarray(im)


@dataclass
class Frame:
    image: np.ndarray
    serial: str
    frame_id: int = 0
    exposure_ns: int = 0
    host_ns: int = 0
    camera_ns: int = 0
    uncertainty_ms: float = 0.0
    path: str | None = None


@dataclass
class ImagePair:
    frames: tuple[Frame | None, Frame | None]
    token: str = field(default_factory=lambda: uuid.uuid4().hex)
    captured_at: str = field(default_factory=stamp)
    origin: str = "files"

    def require_both(self):
        if any(f is None for f in self.frames):
            raise ValueError("Both camera images are required")
        if self.frames[0].image.shape != self.frames[1].image.shape:
            raise ValueError("The two images must have identical dimensions")
        return self


def _manifest_path(root, relative):
    # DualHolo writes portable forward-slash paths. Reject paths leaving the recording.
    part = PurePosixPath(relative.replace("\\", "/"))
    p = (root / Path(*part.parts)).resolve()
    if part.is_absolute() or not p.is_relative_to(root.resolve()):
        raise ValueError("Invalid path in frames.csv")
    return p


def find_partner(selected: Path, tolerance_ms=8.0, max_uncertainty_ms=3.0):
    """Return cam-indexed paths. Never pair by frame IDs or recording ordinals."""
    selected = selected.resolve()
    camera_match = re.match(r"cam([01])_", selected.parent.name) or re.match(r"cam([01])_", selected.name)
    if not camera_match:
        return (selected, None), "Camera name unknown; assigned to cam0. Use Load cam1 to supply the other image."
    camera = int(camera_match[1])
    paths = [None, None]
    paths[camera] = selected
    for root in (selected.parent, selected.parent.parent):
        manifest = root / "frames.csv"
        if not manifest.is_file():
            continue
        with manifest.open(newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            if "file" not in (reader.fieldnames or []):
                if "pair_dir" in (reader.fieldnames or []):
                    continue  # legacy event manifest; the images are in pair directories
                raise ValueError("Unrecognized frames.csv schema")
            rows = list(reader)
        own = [r for r in rows if _manifest_path(root, r["file"]) == selected]
        if len(own) != 1:
            return tuple(paths), "Selected image is missing or duplicated in frames.csv; no automatic pairing."
        row = own[0]
        if int(row["camera"]) != camera:
            raise ValueError("Camera directory disagrees with frames.csv")
        instant = int(row["estimated_exposure_host_ns"])  # not float: preserve all 64 bits
        if float(row["clock_uncertainty_ms"]) > max_uncertainty_ms:
            return tuple(paths), "Selected frame's clock uncertainty exceeds the configured limit."
        candidates = [r for r in rows if int(r["camera"]) == 1-camera
                      and float(r["clock_uncertainty_ms"]) <= max_uncertainty_ms
                      and abs(int(r["estimated_exposure_host_ns"])-instant) <= tolerance_ms*1e6]
        if len(candidates) != 1:
            return tuple(paths), "No unique exposure-time partner within tolerance; load the other image explicitly."
        partner = candidates[0]
        other_instant = int(partner["estimated_exposure_host_ns"])
        reverse = [r for r in rows if int(r["camera"]) == camera
                   and abs(int(r["estimated_exposure_host_ns"])-other_instant) <= tolerance_ms*1e6]
        if len(reverse) != 1:
            return tuple(paths), "Ambiguous reverse timestamp match; no automatic pairing."
        p = _manifest_path(root, partner["file"])
        if not p.is_file():
            return tuple(paths), "Partner is listed in frames.csv but its image file is missing."
        paths[1-camera] = p
        return tuple(paths), f"Paired by exposure time (difference {abs(other_instant-instant)/1e6:.3f} ms)."
    # Old DualHolo: event_.../frame_000019/cam0_serial.tiff + cam1_serial.tiff.
    if re.match(r"cam[01]_", selected.name):
        partners = sorted(p for p in selected.parent.glob(f"cam{1-camera}_*")
                          if p.suffix.lower() in (".tif", ".tiff", ".png"))
        if len(partners) == 1:
            paths[1-camera] = partners[0]
            return tuple(paths), "Paired using the legacy per-pair directory."
    return tuple(paths), "No frames.csv found; frame sequence numbers are not used as synchronization."


def load_pair(selected, config):
    paths, note = find_partner(Path(selected), config.pair_tolerance_ms, config.max_clock_uncertainty_ms)
    frames = []
    for i, path in enumerate(paths):
        if path is None:
            frames.append(None)
            continue
        match = re.match(r"cam[01]_([^/.]+)", path.parent.name) or re.match(r"cam[01]_([^/.]+)", path.name)
        serial = match[1] if match else getattr(config, f"serial{i}")
        frame = Frame(read_image(path), serial, path=str(path))
        # Preserve acquisition provenance when a loaded pair is explicitly saved.
        for directory in (path.parent, path.parent.parent):
            manifest = directory / "frames.csv"
            if not manifest.is_file():
                continue
            with manifest.open(newline="", encoding="utf-8-sig") as file:
                reader = csv.DictReader(file)
                for row in reader:
                    modern = "file" in row
                    matching = (_manifest_path(directory, row["file"]) == path if modern else
                                row.get("pair_dir") == path.parent.name)
                    if not matching:
                        continue
                    prefix = "" if modern else f"cam{i}_"
                    for name, key in (("frame_id", "frame_id"), ("exposure_ns", "estimated_exposure_host_ns"),
                                      ("host_ns", "host_received_ns"), ("camera_ns", "camera_ns")):
                        if prefix+key in row:
                            setattr(frame, name, int(row[prefix+key]))
                    frame.uncertainty_ms = float(row.get(prefix+"clock_uncertainty_ms", 0))
                    frame.serial = row.get(prefix+"serial", serial)
                    break
        frames.append(frame)
    return ImagePair(tuple(frames)), note


def fingerprint(array):
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


class Session:
    def __init__(self, output):
        self.path = Path(output) / ("session_" + stamp())

    def ensure(self, config):
        from .config import save_config
        self.path.mkdir(parents=True, exist_ok=True)
        if not (self.path / "config.yaml").exists():
            save_config(config, self.path / "config.yaml")
        return self.path

    def default_result(self, pair, mode, z, filtered):
        return self.path / ("recording_" + pair.captured_at) / "reconstructions" / (
            f"{mode}_z{z:+.6f}mm_{'filtered' if filtered else 'unfiltered'}.tiff")

    def save_raw(self, pair, config):
        self.ensure(config)
        target = self.path / ("recording_" + stamp())
        target.mkdir(exist_ok=False)
        rows = []
        for i, frame in enumerate(pair.frames):
            if frame is None:
                continue
            name = f"cam{i}_{frame.serial}/frame_000000_id{frame.frame_id}.tiff"
            path = target / name
            path.parent.mkdir()
            tifffile.imwrite(path, frame.image, compression=None, photometric="minisblack")
            rows.append(dict(file=name, camera=i, index=0, serial=frame.serial, frame_id=frame.frame_id,
                             camera_ns=frame.camera_ns, host_received_ns=frame.host_ns,
                             estimated_exposure_host_ns=frame.exposure_ns, clock_uncertainty_ms=frame.uncertainty_ms,
                             source_format=str(frame.image.dtype), stored_bits=frame.image.itemsize*8,
                             full_scale=int(np.iinfo(frame.image.dtype).max) if frame.image.dtype.kind == "u" else 1,
                             record_admitted_ns=frame.host_ns))
        if rows:
            with (target / "frames.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
        return target


def save_result(path, intensity, preview, metadata, curve):
    """Float TIFF preserves intensity; PNG copies the explicitly scaled display."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".partial" + path.suffix)
    try:
        if path.suffix.lower() in (".tif", ".tiff"):
            tifffile.imwrite(temporary, intensity.astype(np.float32), photometric="minisblack", compression=None)
        elif path.suffix.lower() == ".png":
            import cv2
            ok, data = cv2.imencode(".png", preview)
            if not ok:
                raise OSError("PNG encoding failed")
            data.tofile(temporary)
        else:
            raise ValueError("Choose TIFF (float32 intensity) or PNG (display contrast)")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    path.with_suffix(path.suffix + ".json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    with path.with_suffix(path.suffix + ".csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["z_mm", "tamura_filtered_std_over_mean", "tamura_unfiltered_std_over_mean"])
        writer.writerows(curve)
