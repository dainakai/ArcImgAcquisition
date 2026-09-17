"""Subpixel PIV quadratic sampling maps from a focused glass-plate pair."""
from dataclasses import dataclass
import json
from pathlib import Path

import cv2
import numpy as np

from .data import fingerprint, stamp
from .engine import Reconstruction, intensity_input, analyze


@dataclass
class Calibration:
    map_x: np.ndarray
    map_y: np.ndarray
    valid_mask: np.ndarray
    calibrated_mask: np.ndarray
    metadata: dict

    def validate_for(self, pair, config):
        pair.require_both()
        shape = pair.frames[0].image.shape
        for a in (self.map_x, self.map_y, self.valid_mask, self.calibrated_mask):
            if a.shape != shape:
                raise ValueError("Calibration dimensions do not match these images")
        if self.metadata.get("version") not in (1, 2) or self.metadata.get("direction") != "cam0_output_to_cam1_source":
            raise ValueError("Unsupported calibration version or map direction")
        if self.metadata.get("serials") != [f.serial for f in pair.frames]:
            raise ValueError("Calibration camera serials/order do not match this pair")
        for key in ("pixel_pitch_um", "wavelength_nm"):
            if not np.isclose(self.metadata.get(key, 0), getattr(config, key), rtol=1e-8, atol=0):
                raise ValueError(f"Calibration {key} does not match the config")
        if not self.metadata.get("quality_passed") or not np.any(self.calibrated_mask):
            raise ValueError("Calibration has not passed quality checks")
        if self.metadata.get("version") == 2:
            if self.metadata.get("padding_size") != config.padding_size:
                raise ValueError("パディング設定がキャリブレーションと一致しません")
            gap = self.metadata.get("plane_separation_mm")
            if config.plane_separation_mm is not None and not np.isclose(gap, config.plane_separation_mm, rtol=0, atol=1e-8):
                raise ValueError("面間距離が適用したキャリブレーションと一致しません")
        return self

    def apply(self, original):
        if original.shape != self.map_x.shape:
            raise ValueError("Calibration dimensions do not match the original hologram")
        result = cv2.remap(original.astype(np.float32), self.map_x, self.map_y, cv2.INTER_LANCZOS4,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=float(original.mean()))
        # Outside full Lanczos support GS will not impose a measured constraint.
        return result

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".partial")
        try:
            with temporary.open("wb") as f:
                np.savez_compressed(f, map_x=self.map_x.astype(np.float32), map_y=self.map_y.astype(np.float32),
                                    valid_mask=self.valid_mask, calibrated_mask=self.calibrated_mask,
                                    metadata=np.array(json.dumps(self.metadata)))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def load(cls, path):
        import zipfile
        # Bound decompression before loading an accidentally selected array archive.
        with zipfile.ZipFile(path) as archive:
            if sum(f.file_size for f in archive.infolist()) > 800_000_000:
                raise ValueError("Calibration archive exceeds the 8192 × 8192 map limit")
        with np.load(path, allow_pickle=False) as d:
            maps = [d[n].copy() for n in ("map_x", "map_y", "valid_mask", "calibrated_mask")]
            metadata = json.loads(str(d["metadata"].item()))
        shape = maps[0].shape
        if len(shape) != 2 or not all(0 < n <= 8192 for n in shape) or any(a.shape != shape for a in maps):
            raise ValueError("Invalid calibration map dimensions")
        if any(a.dtype != np.float32 or not np.isfinite(a).all() for a in maps[:2]):
            raise ValueError("Sampling maps must be finite float32 coordinates")
        if any(a.dtype != np.bool_ for a in maps[2:]):
            raise ValueError("Calibration masks must be boolean")
        h, w = shape
        bounds = (maps[0] >= 3) & (maps[0] < w-4) & (maps[1] >= 3) & (maps[1] < h-4)
        if np.any(maps[2] & ~bounds) or np.any(maps[3] & ~maps[2]):
            raise ValueError("Calibration claims unsupported sampling coordinates")
        if metadata.get("version") not in (1, 2) or not metadata.get("quality_passed"):
            raise ValueError("Calibration is incompatible or failed quality checks")
        return cls(*maps, metadata)


def signal(image):
    f = image.astype(np.float32)
    low = cv2.GaussianBlur(f, (0, 0), 24)
    ratio = f/np.maximum(low, max(float(f.mean())*.02, 1e-9))
    return cv2.GaussianBlur(ratio-1, (0, 0), 1)


def design(xy, shape):
    h, w = shape
    x = (xy[:, 0]-(w-1)/2)/(w/2)
    y = (xy[:, 1]-(h-1)/2)/(h/2)
    return np.column_stack([np.ones(len(x)), x, y, x*x, x*y, y*y])


def subpixel_peak(correlation, x, y):
    if x < 1 or y < 1 or x >= correlation.shape[1]-1 or y >= correlation.shape[0]-1:
        return None
    yy, xx = np.mgrid[-1:2, -1:2]
    model = np.column_stack([xx.ravel()**2, xx.ravel()*yy.ravel(), yy.ravel()**2,
                             xx.ravel(), yy.ravel(), np.ones(9)])
    b = np.linalg.lstsq(model, correlation[y-1:y+2, x-1:x+2].ravel(), rcond=None)[0]
    hessian = np.array([[2*b[0], b[1]], [b[1], 2*b[2]]])
    if np.max(np.linalg.eigvalsh(hessian)) >= -1e-7:
        return None
    offset = -np.linalg.solve(hessian, b[3:5])
    return offset if np.max(np.abs(offset)) <= 1 else None


def match(a, b, point, width, radius, guess):
    x, y = point
    half = (width-1)/2
    if x-half < 0 or y-half < 0 or x+half >= a.shape[1] or y+half >= a.shape[0]:
        return None
    sx, sy = np.rint(np.array([x-half, y-half])+guess).astype(int)-radius
    size = width+2*radius
    if sx < 0 or sy < 0 or sx+size > b.shape[1] or sy+size > b.shape[0]:
        return None
    patch = cv2.getRectSubPix(a, (width, width), (float(x), float(y)))
    if float(patch.std()) < 1e-5:
        return None
    corr = cv2.matchTemplate(b[sy:sy+size, sx:sx+size], patch, cv2.TM_CCOEFF_NORMED)
    _, score, _, (px, py) = cv2.minMaxLoc(corr)
    offset = subpixel_peak(corr, px, py)
    if offset is None or score < .50:
        return None
    yy, xx = np.indices(corr.shape)
    distant = corr[(xx-px)**2+(yy-py)**2 > 25]
    if distant.size and score-float(distant.max()) < .025:
        return None
    return np.array([sx+px+offset[0]+half-x, sy+py+offset[1]+half-y]), score


def fit(xy, displacements, confidence, shape):
    x = design(xy, shape)
    if len(xy) < 9 or np.linalg.matrix_rank(x) < 6:
        raise ValueError("Insufficient spatially distributed calibration points")
    weights = confidence**2
    for _ in range(15):
        b = np.linalg.lstsq(x*np.sqrt(weights[:, None]), displacements*np.sqrt(weights[:, None]), rcond=None)[0]
        residual = np.linalg.norm(displacements-x@b, axis=1)
        scale = max(.05, 1.4826*np.median(np.abs(residual-np.median(residual))))
        weights = confidence**2*np.minimum(1, 1.5*scale/np.maximum(residual, 1e-9))
    good = residual <= max(.5, np.median(residual)+3*scale)
    return b, good, residual


def measure_vectors(images, config, cancel, progress):
    a, b = [signal(im) for im in images]
    h, w = a.shape
    if min(h, w) < 2*config.calibration_window_px:
        raise ValueError("Calibration image is too small for the configured interrogation window")
    cancel.check()
    guess, response = cv2.phaseCorrelate(a.copy(), b.copy(), cv2.createHanningWindow((w, h), cv2.CV_32F))
    if response < .02:
        raise ValueError("No trustworthy coarse correspondence; check glass-plate focus and orientation")
    radius, width, step = config.calibration_search_px, config.calibration_window_px, config.calibration_step_px
    half = (width-1)/2
    points = [(x, y) for y in np.arange(half+radius, h-half-radius, step)
              for x in np.arange(half+radius, w-half-radius, step)]
    xy, disp, conf = [], [], []
    for n, point in enumerate(points):
        cancel.check()
        result = match(a, b, point, width, radius, guess)
        if result is not None:
            delta, quality = result
            reverse = match(b, a, np.array(point)+delta, width, 4, -delta)
            if reverse is not None and np.linalg.norm(reverse[0]+delta) < .75:
                xy.append(point)
                disp.append(delta)
                conf.append(quality)
        progress("Subpixel calibration", n+1, len(points), None)
    if len(xy) < config.calibration_min_matches:
        raise ValueError(f"Only {len(xy)} reliable correspondences; need {config.calibration_min_matches}")
    return np.array(xy), np.array(disp), np.array(conf)


def fit_maps(images, config, cancel, progress):
    xy, disp, conf = measure_vectors(images, config, cancel, progress)
    h, w = images[0].shape
    radius, width, step = config.calibration_search_px, config.calibration_window_px, config.calibration_step_px
    half = (width-1)/2
    a = images[0]
    coefficients, good, residual = fit(xy, disp, conf, a.shape)
    rms = float(np.sqrt(np.mean(residual[good]**2)))
    if good.sum() < config.calibration_min_matches or rms > config.calibration_max_rms_px:
        raise ValueError(f"Calibration rejected: {good.sum()} inliers, RMS {rms:.3f} px")
    # Leave out interleaved spatial points; fit residual alone is insufficient.
    grid = np.rint((xy-[half+radius, half+radius])/step).astype(int)
    train = ((grid[:, 0]+grid[:, 1]) % 2 == 0) & good
    test = ~train & good
    if min(train.sum(), test.sum()) < 9:
        raise ValueError("Too few spatial holdout points; use a smaller calibration_step_px")
    validation, _, _ = fit(xy[train], disp[train], conf[train], a.shape)
    holdout = float(np.sqrt(np.mean(np.sum((disp[test]-design(xy[test], a.shape)@validation)**2, axis=1))))
    if holdout > config.calibration_max_holdout_px:
        raise ValueError(f"Spatial holdout RMS {holdout:.3f} px exceeds the configured limit")
    cancel.check()
    x = (np.arange(w, dtype=np.float32)-(w-1)/2)/(w/2)
    y = (np.arange(h, dtype=np.float32)-(h-1)/2)/(h/2)
    xx, yy = x[None, :], y[:, None]
    maps = []
    for c, base in zip(coefficients.T, (np.arange(w)[None, :], np.arange(h)[:, None])):
        maps.append((base+c[0]+c[1]*xx+c[2]*yy+c[3]*xx*xx+c[4]*xx*yy+c[5]*yy*yy).astype(np.float32))
    mx, my = maps
    valid = (mx >= 3) & (mx < w-4) & (my >= 3) & (my < h-4)
    support = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(support, np.rint(cv2.convexHull(xy[good].astype(np.float32))).astype(np.int32), 1)
    calibrated = valid & support.astype(bool)
    if calibrated.mean() < .1:
        raise ValueError("Calibrated support covers less than 10% of the image")
    stats = dict(matches=len(xy), inliers=int(good.sum()), rms_px=rms, holdout_rms_px=holdout,
                 calibrated_fraction=float(calibrated.mean()), coefficients=coefficients.tolist(),
                 vectors_before=np.column_stack([xy[good], disp[good]]).tolist())
    return mx, my, valid, calibrated, stats


def separation_from_focus(focus_depths):
    z0, z1 = map(float, focus_depths)
    if not np.isfinite([z0, z1]).all() or abs(z0-z1) < 1e-6:
        raise ValueError("2面の焦点位置が同じです。各カメラの焦点を確認して面間距離を確定してください。")
    # P(d) U0 = U1 and P(z0) U0 = P(z1) U1 => d = z0 - z1.
    return z0-z1


@dataclass
class CalibrationPreview:
    calibration: Calibration
    corrected_raw: np.ndarray
    corrected_focus: np.ndarray | None


def build_from_focused(pair, config, focus_depths, focused, cancel, progress):
    pair.require_both()
    gap = separation_from_focus(focus_depths)
    mx, my, valid, calibrated, stats = fit_maps(focused, config, cancel, progress)
    metadata = dict(version=2, direction="cam0_output_to_cam1_source", quality_passed=True,
                    created_at=stamp(), serials=[f.serial for f in pair.frames], shape=list(mx.shape),
                    wavelength_nm=config.wavelength_nm, pixel_pitch_um=config.pixel_pitch_um,
                    padding_size=config.padding_size, plane_separation_mm=gap,
                    focus_depths_mm=list(map(float, focus_depths)), input_sha256=[fingerprint(f.image) for f in pair.frames],
                    input_paths=[f.path for f in pair.frames], gs_iterations=config.gs_iterations,
                    interpolation="Lanczos4", additional_flip=False, **stats)
    calibration = Calibration(mx, my, valid, calibrated, metadata)
    corrected_focus = calibration.apply(focused[1])
    xy, delta, confidence = measure_vectors([focused[0], corrected_focus], config, cancel,
        lambda stage, i, n, row: progress("補正後の残差を検証", i, n, row))
    positions = np.rint(xy).astype(int)
    supported = calibrated[positions[:, 1], positions[:, 0]]
    xy, delta = xy[supported], delta[supported]
    if len(xy) < config.calibration_min_matches:
        raise ValueError("補正後の有効な対応点が不足しています。焦点位置とベクトルマップ条件を確認してください。")
    post_rms = float(np.sqrt(np.mean(np.sum(delta**2, axis=1))))
    if post_rms > config.calibration_max_holdout_px:
        raise ValueError(f"補正後の残差 RMS {post_rms:.3f} px が許容値を超えています")
    metadata.update(vectors_after=np.column_stack([xy, delta]).tolist(), post_rms_px=post_rms)
    corrected_raw = calibration.apply(pair.frames[1].image)
    cancel.check()
    return CalibrationPreview(calibration, corrected_raw, corrected_focus)


def build_calibration(pair, config, focus_depths, scan, cancel, progress):
    """Noninteractive entry point; the GUI reviews focus scans before fitting."""
    focused, distances = [], []
    for camera in range(2):
        if focus_depths is None:
            result = analyze(pair, f"gabor_cam{camera}", config, None, 1, scan, cancel, progress)
            if result.best_filtered is None:
                raise ValueError(f"cam{camera} に山型のピークがありません。範囲を変更するか焦点を手動指定してください。")
            best = result.best_filtered
        else:
            reconstruction = Reconstruction(np.sqrt(intensity_input(pair.frames[camera].image, config.padding_size)), config, cancel)
            best = reconstruction.render(focus_depths[camera], cancel)
        focused.append(best.filtered.copy())
        distances.append(best.z_mm)
    return build_from_focused(pair, config, distances, focused, cancel, progress).calibration
