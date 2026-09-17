"""CPU optics: native-size Tamura scans and mean-padded display reconstruction."""
from dataclasses import dataclass, field
import math
import threading
import numpy as np
from scipy import fft, signal
from optical_padding import mean_pad
from optical_bandlimit import NumpyAngularSpectrumBandlimit
from .cache import Render


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


def intensity_input(image, side=4096):
    image = np.asarray(image)
    if image.ndim != 2 or not image.size or max(image.shape) > side:
        raise ValueError(f"画像が指定パディング {side} × {side} を超えています。縮小は行いません。")
    if not np.isfinite(image).all() or np.iscomplexobj(image) or image.min() < 0:
        raise ValueError("Intensities must be finite, real and nonnegative")
    return image.astype(np.float32)


def depths(minimum, maximum, step):
    if not all(math.isfinite(x) for x in (minimum, maximum, step)) or maximum < minimum or step <= 0:
        raise ValueError("有限な深度、min ≤ max、正の間隔を指定してください")
    count = int(math.floor((maximum-minimum)/step+1e-9))+1
    if count > 10001:
        raise ValueError("探索は10001深度以内にしてください")
    return minimum+np.arange(count, dtype=np.float64)*step


def tamura(intensity):
    mean = float(np.mean(intensity, dtype=np.float64))
    return float(np.std(intensity, dtype=np.float64)/mean) if mean > 1e-20 else 0.0


def focus_peaks(curve, config, column=1):
    """Interior local peaks ranked by prominence, never an endpoint maximum."""
    if len(curve) < 3:
        return []
    values = np.asarray(curve, dtype=np.float64)[:, column]
    indices, props = signal.find_peaks(values, prominence=0, width=config.peak_min_width_samples)
    accepted = []
    for i, prominence in zip(indices, props["prominences"]):
        threshold = max(abs(float(values[i]))*config.peak_prominence_fraction, 1e-12)
        if prominence >= threshold:
            accepted.append((int(i), float(prominence)))
    return [i for i, _ in sorted(accepted, key=lambda v: v[1], reverse=True)]


class Propagator:
    def __init__(self, config, native_shape=None):
        self.side, self.threads = config.padding_size, config.compute_threads
        self.shape = native_shape or (self.side, self.side)
        self.native = native_shape is not None
        self.band = NumpyAngularSpectrumBandlimit(self.shape, config.pixel_pitch_um, config.wavelength_nm/1000)
        self.phase_per_mm = None

    def transfer(self, z_mm, cancel, filtered=True):
        if not math.isfinite(z_mm):
            raise ValueError("Nonfinite depth")
        height = self.shape[0]
        if self.phase_per_mm is None:
            phase = np.empty(self.shape, np.float64)
            fx2, k = self.band.fx[None, :]**2, 1/self.band.wavelength_um
            for start in range(0, height, 128):
                cancel.check()
                rows = slice(start, start+128)
                radius2 = fx2+self.band.fy[rows, None]**2
                phase[rows] = -2*np.pi*1000*radius2/(np.sqrt(k*k-radius2)+k)
            self.phase_per_mm = phase
        out = np.empty(self.shape, dtype=np.complex64)
        attenuate = filtered and not self.band.full_pass(z_mm)
        for start in range(0, height, 128):
            cancel.check()
            rows = slice(start, start+128)
            out[rows] = np.exp(1j*(self.phase_per_mm[rows]*z_mm))
            if attenuate:
                out[rows] *= self.band.window(z_mm, rows)
        return out

    def spectrum(self, field, cancel):
        cancel.check()
        if self.native:
            if field.shape != self.shape:
                raise ValueError("Native propagation must preserve the input dimensions")
            padded, crop = field.copy(), (slice(0, field.shape[0]), slice(0, field.shape[1]))
        else:
            padded, crop = mean_pad(field, side=self.side)
        result = fft.fft2(padded, workers=self.threads, overwrite_x=True)
        cancel.check()
        return result, crop

    def from_spectrum(self, spectrum, transfer, crop, cancel):
        cancel.check()
        result = fft.ifft2(spectrum*transfer, workers=self.threads, overwrite_x=True)[crop].copy()
        cancel.check()
        return result

    def propagate(self, field, transfer, cancel):
        spectrum, crop = self.spectrum(field, cancel)
        return self.from_spectrum(spectrum, transfer, crop, cancel)


class Reconstruction:
    def __init__(self, field, config, cancel):
        cancel.check()
        self.propagator = Propagator(config)
        self.field = field
        self.spectrum = self.crop = None

    def render(self, z_mm, cancel):
        key = float(z_mm)
        cancel.check()
        prop = self.propagator
        # Prepare the display FFT lazily, after the inexpensive native scan.
        # Retain the input spectrum, never a stack of reconstructed images.
        if self.spectrum is None:
            self.spectrum, self.crop = prop.spectrum(self.field, cancel)
        filtered, unfiltered = render_intensities(prop, self.spectrum, self.crop, key, cancel)
        result = Render(key, filtered, unfiltered).prepare_preview()
        cancel.check()
        return result


def render_intensities(prop, spectrum, crop, key, cancel):
    transfer = prop.transfer(key, cancel, filtered=False)
    u = prop.from_spectrum(spectrum, transfer, crop, cancel)
    unfiltered = (np.abs(u)**2).astype(np.float32)
    del u
    if prop.band.full_pass(key):
        filtered = unfiltered
    else:
        for start in range(0, prop.shape[0], 128):
            cancel.check()
            rows = slice(start, start+128)
            transfer[rows] *= prop.band.window(key, rows)
        u = prop.from_spectrum(spectrum, transfer, crop, cancel)
        filtered = (np.abs(u)**2).astype(np.float32)
    return filtered, unfiltered


def phase_recover(pair, calibration, config, iterations, cancel, progress):
    pair.require_both()
    calibration.validate_for(pair, config)
    if config.plane_separation_mm is None:
        raise ValueError("キャリブレーションタブで面間距離と画像変換を適用してください")
    if iterations < 1:
        raise ValueError("GS iterations must be positive")
    a0 = intensity_input(pair.frames[0].image, config.padding_size)
    a1 = calibration.apply(pair.frames[1].image)
    valid = calibration.calibrated_mask & calibration.valid_mask
    m0, m1 = float(a0[valid].mean()), float(a1[valid].mean())
    if min(m0, m1) <= 0:
        raise ValueError("Both calibrated intensities must have a positive mean")
    amplitude0 = np.sqrt(a0)
    amplitude1 = np.sqrt(np.maximum(a1*(m0/m1), 0))
    prop = Propagator(config)
    # User choice: GS never attenuates the spectrum. The post-recovery
    # reconstruction filter remains independently selectable.
    forward = prop.transfer(config.plane_separation_mm, cancel, filtered=False)
    backward = forward.conj()
    current = amplitude0.astype(np.complex64)
    for iteration in range(iterations):
        cancel.check()
        other = prop.propagate(current, forward, cancel)
        other[valid] = amplitude1[valid]*np.exp(1j*np.angle(other[valid]))
        current = prop.propagate(other, backward, cancel)
        current = amplitude0*np.exp(1j*np.angle(current))
        progress("GS 位相回復", iteration+1, iterations, None)
    cancel.check()
    return current.astype(np.complex64)


@dataclass
class Analysis:
    reconstruction: Reconstruction
    curve: list = field(default_factory=list)
    best_filtered: float | None = None
    best_unfiltered: float | None = None
    stopped: bool = False
    peaks_filtered: list = field(default_factory=list)
    peaks_unfiltered: list = field(default_factory=list)


def analyze(pair, mode, config, calibration, iterations, scan, cancel, progress):
    cancel.check()
    if mode == "phase":
        if calibration is None:
            raise ValueError("Phase recovery requires a compatible calibration")
        field = phase_recover(pair, calibration, config, iterations, cancel, progress)
    else:
        index = 0 if mode == "gabor_cam0" else 1
        if pair.frames[index] is None:
            raise ValueError(f"cam{index} の画像を読み込んでください")
        field = np.sqrt(intensity_input(pair.frames[index].image, config.padding_size))
    progress(f"Tamura用 {field.shape[1]} × {field.shape[0]} スペクトルを準備（パディングなし）", 0, 0, None)
    result = Analysis(Reconstruction(field, config, cancel))
    prop = Propagator(config, native_shape=field.shape)
    spectrum, crop = prop.spectrum(field, cancel)
    try:
        for i, z in enumerate(scan):
            filtered, unfiltered = render_intensities(prop, spectrum, crop, float(z), cancel)
            result.curve.append((float(z), tamura(filtered), tamura(unfiltered)))
            del filtered, unfiltered
            progress("Tamura 深度探索（パディングなし）", i+1, len(scan), result.curve[-1])
    except Cancelled:
        if not result.curve:
            raise
        result.stopped = True
    result.peaks_filtered = focus_peaks(result.curve, config, 1)
    result.peaks_unfiltered = focus_peaks(result.curve, config, 2)
    if result.peaks_filtered:
        result.best_filtered = result.curve[result.peaks_filtered[0]][0]
    if result.peaks_unfiltered:
        result.best_unfiltered = result.curve[result.peaks_unfiltered[0]][0]
    return result
