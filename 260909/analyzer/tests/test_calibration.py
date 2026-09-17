from dataclasses import replace
import cv2
import numpy as np
import pytest

from holoanalyze.calibration import Calibration, fit_maps
from holoanalyze.config import Config
from holoanalyze.data import Frame, ImagePair
from holoanalyze.engine import Cancellation


def identity_calibration(shape, config):
    yy, xx = np.indices(shape, dtype=np.float32)
    valid = (xx >= 3) & (xx < shape[1]-4) & (yy >= 3) & (yy < shape[0]-4)
    return Calibration(xx, yy, valid, valid.copy(), dict(version=1, direction="cam0_output_to_cam1_source",
                        quality_passed=True, serials=[config.serial0, config.serial1], wavelength_nm=config.wavelength_nm,
                        pixel_pitch_um=config.pixel_pitch_um, inliers=100, rms_px=0, holdout_rms_px=0))


def glass_texture(shape=(512, 512), seed=47):
    rng = np.random.default_rng(seed)
    image = np.full(shape, 150, np.float32)
    for _ in range(int(np.prod(shape)/700)):
        x, y = rng.integers([0, 0], [shape[1], shape[0]])
        cv2.circle(image, (int(x), int(y)), int(rng.integers(3, 9)), float(rng.uniform(10, 45)), -1)
    return cv2.GaussianBlur(image, (0, 0), .8)


def test_subpixel_map_and_lanczos_direction(tmp_path):
    original = glass_texture()
    dx, dy = 2.4, -1.6
    moved = cv2.warpAffine(original, np.array([[1, 0, dx], [0, 1, dy]], np.float32), (512, 512),
                           flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
    config = replace(Config(), calibration_window_px=48, calibration_step_px=48, calibration_search_px=8)
    mx, my, valid, calibrated, stats = fit_maps([original, moved], config, Cancellation(), lambda *a: None)
    yy, xx = np.indices(original.shape)
    assert abs(np.median((mx-xx)[calibrated])-dx) < .12
    assert abs(np.median((my-yy)[calibrated])-dy) < .12
    calibration = Calibration(mx, my, valid, calibrated,
                              dict(identity_calibration(original.shape, config).metadata, **stats))
    corrected = calibration.apply(moved)
    assert np.sqrt(np.mean((original[calibrated]-corrected[calibrated])**2)) < 1
    path = tmp_path / "calibration.npz"
    calibration.save(path)
    restored = Calibration.load(path)
    np.testing.assert_array_equal(restored.map_x, mx)
    pair = ImagePair((Frame(original, config.serial0), Frame(moved, config.serial1)))
    restored.validate_for(pair, config)
    with pytest.raises(ValueError):
        restored.validate_for(pair, replace(config, pixel_pitch_um=3))
    with pytest.raises(ValueError):
        restored.validate_for(ImagePair(pair.frames[::-1]), config)


def test_featureless_calibration_rejected():
    images = [np.ones((512, 512), np.float32)]*2
    with pytest.raises(ValueError):
        fit_maps(images, Config(), Cancellation(), lambda *a: None)


def test_prior_piv_method_recovers_large_offset_and_affine_warp(monkeypatch):
    # Phase correlation must not decide the coarse correspondence of glass dots.
    monkeypatch.setattr(cv2, 'phaseCorrelate', lambda *a: (_ for _ in ()).throw(AssertionError('single-peak guess')))
    shape = (1536, 1792)
    original = glass_texture(shape)
    affine = np.array([[1.001, .002, 220.4], [-.001, .999, -26.6]], np.float32)
    moved = cv2.warpAffine(original, affine, shape[::-1], flags=cv2.INTER_LANCZOS4, borderValue=150)
    mx, my, valid, support, stats = fit_maps([original, moved], Config(), Cancellation(), lambda *a: None)
    yy, xx = np.indices(shape)
    expected_x = affine[0, 0]*xx+affine[0, 1]*yy+affine[0, 2]
    expected_y = affine[1, 0]*xx+affine[1, 1]*yy+affine[1, 2]
    rms = np.sqrt(np.mean(((mx-expected_x)**2+(my-expected_y)**2)[support]))
    assert rms < .15, rms
    assert 18 <= stats['inliers'] < 70
    assert stats['holdout_rms_px'] < .15
    assert stats['registration_settings']['window_px'] == 128
