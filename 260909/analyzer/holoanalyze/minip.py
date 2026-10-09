"""Minimum intensity projections; retain two cropped minima, never a depth stack."""
from dataclasses import dataclass
import numpy as np
from .cache import display_pixels
from .engine import Reconstruction, depths, intensity_input, phase_recover, render_intensities


@dataclass
class MinIP:
    depths_mm: np.ndarray
    filtered: np.ndarray
    unfiltered: np.ndarray
    padding_size: int
    limits: tuple
    linear_limits: tuple

    def pixels(self, filtered=True, normalize=True):
        return display_pixels(self.filtered if filtered else self.unfiltered,
                              self.limits if normalize else self.linear_limits)


def create_minip(pair, mode, config, calibration, iterations, minimum, maximum, step,
                 padding_size, cancel, progress):
    scan = depths(minimum, maximum, step)
    cancel.check()
    if mode == "phase":
        if calibration is None:
            raise ValueError("MinIPの位相回復には適用済みキャリブレーションが必要です")
        field = phase_recover(pair, calibration, config, iterations, cancel, progress)
    elif mode in ("gabor_cam0", "gabor_cam1"):
        camera = 1 if mode == "gabor_cam1" else 0
        if pair.frames[camera] is None:
            raise ValueError(f"cam{camera} の画像を読み込んでください")
        image = pair.frames[camera].image
        field = np.sqrt(intensity_input(image, padding_size or max(image.shape)))
    else:
        raise ValueError(f"Unknown MinIP mode: {mode}")
    reconstruction = Reconstruction(field, config, cancel)
    progress("MinIP スペクトルを準備", 0, len(scan), None)
    reconstruction.prepare(cancel, padding_size)
    minimum_filtered = minimum_unfiltered = None
    for index, z in enumerate(scan):
        filtered, unfiltered = render_intensities(reconstruction.propagator,
            reconstruction.spectrum, reconstruction.crop, float(z), cancel)
        if minimum_filtered is None:
            # Copies must be independent even when the bandlimit fully passes.
            minimum_filtered, minimum_unfiltered = filtered.copy(), unfiltered.copy()
        else:
            np.minimum(minimum_filtered, filtered, out=minimum_filtered)
            np.minimum(minimum_unfiltered, unfiltered, out=minimum_unfiltered)
        del filtered, unfiltered
        cancel.check()
        progress(f"MinIP · z = {z:.4f} mm", index+1, len(scan), None)
    # Quantize only after projecting floating-point intensities across depth.
    limits = tuple(float(x) for x in np.percentile(minimum_filtered, [1, 99]))
    linear = (0., max(float(minimum_filtered.max()), float(minimum_unfiltered.max())))
    cancel.check()
    return MinIP(scan, minimum_filtered, minimum_unfiltered, padding_size, limits, linear)
