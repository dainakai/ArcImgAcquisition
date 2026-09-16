"""CPU-only angular spectrum, GS and incremental autofocus; no file writes."""
from collections import OrderedDict
from dataclasses import dataclass, field
import math
import threading

import numpy as np
from scipy import fft

from optical_padding import mean_pad, PROPAGATION_SHAPE
from optical_bandlimit import NumpyAngularSpectrumBandlimit


class Cancelled(Exception):
    pass


class Cancellation:
    def __init__(self):
        self.event = threading.Event()

    def cancel(self):
        self.event.set()

    def check(self):
        if self.event.is_set():
            raise Cancelled()


def intensity_input(image):
    image = np.asarray(image)
    if image.ndim != 2 or not image.size or max(image.shape) > 4096:
        raise ValueError("Expected a nonempty monochrome image no larger than 4096 × 4096")
    if not np.isfinite(image).all() or np.iscomplexobj(image) or image.min() < 0:
        raise ValueError("Intensities must be finite, real and nonnegative")
    return image.astype(np.float32)


def depths(minimum, maximum, step):
    if not all(math.isfinite(x) for x in (minimum, maximum, step)) or maximum < minimum or step <= 0:
        raise ValueError("Use finite depths, min ≤ max and a positive step")
    count = int(math.floor((maximum-minimum)/step + 1e-9)) + 1
    if count > 10001:
        raise ValueError("At most 10001 depths per scan")
    # Keep the requested interval exactly; max is included only if it lies on this grid.
    return minimum + np.arange(count, dtype=np.float64)*step


def tamura(intensity):
    """Intensity contrast std(I)/mean(I), matching this repository's focus scans."""
    mean = float(np.mean(intensity, dtype=np.float64))
    return float(np.std(intensity, dtype=np.float64)/mean) if mean > 1e-20 else 0.0


class Propagator:
    def __init__(self, config):
        self.band = NumpyAngularSpectrumBandlimit(4096, config.pixel_pitch_um, config.wavelength_nm/1000)

    def transfer(self, z_mm, cancel, filtered=True):
        if not math.isfinite(z_mm):
            raise ValueError("Nonfinite depth")
        out = np.empty(PROPAGATION_SHAPE, dtype=np.complex64)
        fx2 = self.band.f[None, :]**2
        k = 1/self.band.wavelength_um
        for start in range(0, 4096, 128):
            cancel.check()
            rows = slice(start, start+128)
            radius2 = fx2 + self.band.f[rows, None]**2
            # Remove uniform piston and avoid catastrophic cancellation of kz-k.
            phase = -2*np.pi*(z_mm*1000)*radius2/(np.sqrt(k*k-radius2)+k)
            out[rows] = np.exp(1j*phase)
            if filtered:
                out[rows] *= self.band.window(z_mm, rows)
        return out

    @staticmethod
    def spectrum(field, cancel):
        cancel.check()
        padded, crop = mean_pad(field)
        result = fft.fft2(padded, workers=1, overwrite_x=True)
        cancel.check()
        return result, crop

    @staticmethod
    def from_spectrum(spectrum, transfer, crop, cancel):
        cancel.check()
        result = fft.ifft2(spectrum*transfer, workers=1, overwrite_x=True)[crop].copy()
        cancel.check()
        return result

    def propagate(self, field, transfer, cancel):
        spectrum, crop = self.spectrum(field, cancel)
        return self.from_spectrum(spectrum, transfer, crop, cancel)


@dataclass
class Render:
    z_mm: float
    filtered: np.ndarray
    unfiltered: np.ndarray

    @property
    def nbytes(self):
        return self.filtered.nbytes+self.unfiltered.nbytes


class Reconstruction:
    """One immutable input/config/calibration revision and a bounded depth cache."""
    def __init__(self, field, config, cancel):
        self.propagator = Propagator(config)
        self.spectrum, self.crop = self.propagator.spectrum(field, cancel)
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.cache_limit = config.cache_megabytes*1024*1024

    def render(self, z_mm, cancel):
        key = float(z_mm)
        cancel.check()
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        transfer = self.propagator.transfer(key, cancel, filtered=False)
        u = self.propagator.from_spectrum(self.spectrum, transfer, self.crop, cancel)
        unfiltered = np.abs(u)**2
        del u
        for start in range(0, 4096, 128):
            cancel.check()
            rows = slice(start, start+128)
            transfer[rows] *= self.propagator.band.window(key, rows)
        u = self.propagator.from_spectrum(self.spectrum, transfer, self.crop, cancel)
        result = Render(key, (np.abs(u)**2).astype(np.float32), unfiltered.astype(np.float32))
        del u, transfer
        # Cache only cropped intensities, never a depth stack or 4k complex fields.
        if result.nbytes <= self.cache_limit:
            while self.cache and self.cache_bytes+result.nbytes > self.cache_limit:
                _, old = self.cache.popitem(last=False)
                self.cache_bytes -= old.nbytes
            self.cache[key] = result
            self.cache_bytes += result.nbytes
        return result


def phase_recover(pair, calibration, config, iterations, cancel, progress):
    pair.require_both()
    calibration.validate_for(pair, config)
    if config.plane_separation_mm is None:
        raise ValueError("Set the signed cam0 → cam1 plane_separation_mm in the YAML config")
    if iterations < 1:
        raise ValueError("GS iterations must be positive")
    a0 = intensity_input(pair.frames[0].image)
    a1 = calibration.apply(pair.frames[1].image)  # original image, exactly one Lanczos4 resampling
    valid = calibration.calibrated_mask & calibration.valid_mask
    # Normalize optical throughput only on the measured, geometrically supported area.
    m0, m1 = float(a0[valid].mean()), float(a1[valid].mean())
    if min(m0, m1) <= 0:
        raise ValueError("Both calibrated intensities must have a positive mean")
    amplitude0 = np.sqrt(a0)
    amplitude1 = np.sqrt(np.maximum(a1*(m0/m1), 0))
    prop = Propagator(config)
    forward = prop.transfer(config.plane_separation_mm, cancel, filtered=True)
    backward = forward.conj()  # same |z|-dependent filter on every return trip
    current = amplitude0.astype(np.complex64)
    for iteration in range(iterations):
        cancel.check()
        other = prop.propagate(current, forward, cancel)
        other[valid] = amplitude1[valid]*np.exp(1j*np.angle(other[valid]))
        current = prop.propagate(other, backward, cancel)
        current = amplitude0*np.exp(1j*np.angle(current))
        progress("GS", iteration+1, iterations, None)
    cancel.check()
    return current.astype(np.complex64)


@dataclass
class Analysis:
    reconstruction: Reconstruction
    curve: list = field(default_factory=list)
    best_filtered: Render | None = None
    best_unfiltered: Render | None = None
    stopped: bool = False


def analyze(pair, mode, config, calibration, iterations, scan, cancel, progress):
    cancel.check()
    if mode == "phase":
        if calibration is None:
            raise ValueError("Phase recovery requires a compatible calibration")
        field = phase_recover(pair, calibration, config, iterations, cancel, progress)
    else:
        index = 0 if mode == "gabor_cam0" else 1
        if pair.frames[index] is None:
            raise ValueError(f"Load cam{index} first")
        field = np.sqrt(intensity_input(pair.frames[index].image))
    progress("Preparing 4096 × 4096 spectrum", 0, 0, None)
    result = Analysis(Reconstruction(field, config, cancel))
    del field
    best_f = best_u = -math.inf
    try:
        for i, z in enumerate(scan):
            rendered = result.reconstruction.render(float(z), cancel)
            f, u = tamura(rendered.filtered), tamura(rendered.unfiltered)
            result.curve.append((float(z), f, u))
            if f > best_f:
                best_f, result.best_filtered = f, rendered
            if u > best_u:
                best_u, result.best_unfiltered = u, rendered
            progress("Focus scan", i+1, len(scan), result.curve[-1])
    except Cancelled:
        if not result.curve:
            raise
        result.stopped = True
    return result
