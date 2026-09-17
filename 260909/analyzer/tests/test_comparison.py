"""Same-depth Gabor/GS comparison, fixed viewport and correctly identified exports."""
from dataclasses import replace
import json
import numpy as np
import pytest
import tifffile
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog
from holoanalyze import engine
from holoanalyze.config import Config
from holoanalyze.data import Frame, ImagePair
from holoanalyze.engine import Cancellation, Cancelled, Reconstruction, analyze
from holoanalyze.window import MainWindow
from test_calibration import identity_calibration
from test_gui import wait, close


def comparison_input(config, shape=(80, 96)):
    image = np.random.default_rng(90).uniform(60, 140, shape).astype(np.float32)
    pair = ImagePair((Frame(image, config.serial0), Frame(np.roll(image, 1, axis=1), config.serial1)))
    cal = identity_calibration(shape, config)
    cal.metadata.update(padding_size=config.padding_size, focus_depths_mm=[3, 2])
    return pair, cal


@pytest.mark.parametrize('mode', ['gabor_cam0', 'phase'])
def test_comparison_matches_independent_reconstructions_and_recovers_phase_once(mode, monkeypatch):
    config = replace(Config(), padding_size=128, plane_separation_mm=1)
    pair, cal = comparison_input(config)
    recover = engine.phase_recover
    calls = []
    def observed(*args, **kwargs):
        calls.append(1)
        return recover(*args, **kwargs)
    monkeypatch.setattr(engine, 'phase_recover', observed)
    cancel = Cancellation()
    result = analyze(pair, mode, config, cal, 2, [0, 1, 2], cancel, lambda *a: None, compare=True)
    comparison = result.comparison
    inputs = {key: rec.field for key, rec in comparison.reconstructions.items()}
    for field in inputs.values():
        np.testing.assert_allclose(abs(field)**2, pair.frames[0].image, rtol=1e-5)
    assert np.max(abs(np.angle(inputs['phase']))) > .001
    assert result.reconstruction is comparison.reconstructions[mode]
    assert not comparison.reconstructions[mode].propagator.band.full_pass(5)
    rendered = comparison.render(5, cancel)
    for key, field in inputs.items():
        reference = Reconstruction(field, config, cancel).render(5, cancel)
        for variant in ('filtered', 'unfiltered'):
            np.testing.assert_allclose(getattr(rendered[key], variant), getattr(reference, variant), rtol=1e-5)
    # One display range across both modes and filter states; neither view
    # silently renormalizes itself when toggled.
    limits = (min(np.percentile(r.filtered.astype(np.float64), 1) for r in rendered.values()),
              max(np.percentile(r.filtered.astype(np.float64), 99) for r in rendered.values()))
    linear_max = max(a.max() for r in rendered.values() for a in (r.filtered, r.unfiltered))
    for r in rendered.values():
        for filtered in (False, True):
            source = r.filtered if filtered else r.unfiltered
            for normalized, (lo, hi) in ((True, limits), (False, (0, linear_max))):
                expected = np.clip((source.astype(np.float64)-lo)/(hi-lo), 0, 1)*255
                # Check nearest-level quantization against a float64 reference.
                # A float32 scaling step can land on either side of n + 0.5;
                # bit equality between independently rounded arrays is invalid.
                np.testing.assert_allclose(r.pixels(filtered, normalized), expected, rtol=0, atol=.5001)
    curve = list(result.curve)
    comparison.render(2, cancel)
    assert calls == [1] and result.curve == curve
    # Cancellation during either image must not return a half-populated pair.
    original = comparison.reconstructions['gabor_cam0'].propagator.from_spectrum
    def interrupt(*args):
        output = original(*args)
        cancel.cancel()
        return output
    monkeypatch.setattr(comparison.reconstructions['gabor_cam0'].propagator, 'from_spectrum', interrupt)
    with pytest.raises(Cancelled):
        comparison.render(3, cancel)


def test_comparison_requires_calibration_and_keeps_uncalibrated_gabor_available():
    config = replace(Config(), padding_size=128, plane_separation_mm=1)
    pair, cal = comparison_input(config)
    with pytest.raises(ValueError, match='キャリブレーション'):
        analyze(pair, 'gabor_cam0', config, None, 1, [0], Cancellation(), lambda *a: None, compare=True)
    result = analyze(pair, 'gabor_cam0', config, None, 1, [0], Cancellation(), lambda *a: None)
    assert result.comparison is None and len(result.curve) == 1
    with pytest.raises(ValueError):
        analyze(pair, 'gabor_cam0', replace(config, wavelength_nm=532), cal,
                1, [0], Cancellation(), lambda *a: None, compare=True)


@pytest.mark.parametrize('mode', ['gabor_cam0', 'phase'])
def test_toggle_preserves_zoom_pan_depth_and_curve_and_saves_displayed_mode(app, tmp_path, monkeypatch, mode):
    config = replace(Config(), output_dir=str(tmp_path), padding_size=512,
                     cam0_scan_min_mm=1, cam0_scan_max_mm=3, scan_step_mm=1, gs_iterations=2)
    pair, cal = comparison_input(config, (192, 256))
    w = MainWindow(config, tmp_path/'config.yaml')
    w.resize(1280, 800)
    w.show()
    a, c = w.acquisition, w.calibration_tab
    viewer, view = a.viewer, a.viewer.panel.view
    try:
        a.set_pair(pair)
        assert not any(b.isEnabled() for b in a.comparison_buttons.values())
        c.candidate_ready(cal)
        assert not any(b.isEnabled() for b in a.comparison_buttons.values())
        c.apply_candidate()
        assert w.calibration is cal, w.message.text()
        assert not any(b.isEnabled() for b in a.comparison_buttons.values())
        a.mode.setCurrentIndex(a.mode.findData(mode))
        a.start_analysis()
        wait(app, lambda: w.worker is None and viewer.rendered is not None)
        assert all(b.isEnabled() for b in a.comparison_buttons.values())
        assert viewer.analysis.comparison is not None
        view.original_size()
        view.zoom(8)
        app.processEvents()
        view.horizontalScrollBar().setValue(170)
        view.verticalScrollBar().setValue(240)
        viewport = (view.transform(), view.mapToScene(QPoint(45, 60)))
        assert view.horizontalScrollBar().value() == 170 and view.verticalScrollBar().value() == 240
        curve = list(viewer.analysis.curve)
        depth = viewer.rendered.z_mm
        def forbidden(*args, **kwargs):
            raise AssertionError('Toggling must not execute any FFT or GS')
        with monkeypatch.context() as context:
            context.setattr(engine.fft, 'fft2', forbidden)
            context.setattr(engine.fft, 'ifft2', forbidden)
            context.setattr(engine, 'phase_recover', forbidden)
            for target in ('phase', 'gabor_cam0', 'phase', 'gabor_cam0'):
                QTest.mouseClick(a.comparison_buttons[target], Qt.MouseButton.LeftButton)
                for filtered in (False, True):
                    viewer.filtered.setChecked(filtered)
                    app.processEvents()
                    assert w.worker is None
                    assert view.transform() == viewport[0] and view.mapToScene(QPoint(45, 60)) == viewport[1]
                    np.testing.assert_array_equal(view.pixels, a.comparison_renders[target].pixels(filtered))
                    assert viewer.rendered.z_mm == depth and viewer.depth.value() == depth
                    assert viewer.analysis.curve == curve and viewer.plot.rows == curve
        target = 'phase' if mode == 'gabor_cam0' else 'gabor_cam0'
        a.comparison_buttons[target].click()
        # A new depth computes both images and keeps the chosen display mode.
        previous = a.comparison_renders
        viewer.select_depth(2.2)
        viewer.select_depth(2.4)
        wait(app, lambda: w.worker is None)
        assert a.comparison_renders is not previous and a.display_mode == target
        assert all(r.z_mm == 2.4 for r in a.comparison_renders.values())
        assert view.transform() == viewport[0] and view.mapToScene(QPoint(45, 60)) == viewport[1]
        saved = tmp_path/'comparison.tiff'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(saved), 'TIFF 強度 (*.tiff)'))
        a.save_image()
        wait(app, lambda: w.worker is None)
        np.testing.assert_array_equal(tifffile.imread(saved), a.comparison_renders[target].filtered)
        metadata = json.loads(saved.with_suffix('.tiff.json').read_text(encoding='utf-8'))
        assert metadata['mode'] == target and metadata['tamura_mode'] == mode
        assert metadata['calibration']['focus_depths_mm'] == [3, 2]
        w.apply_settings(replace(w.config, wavelength_nm=532))
        assert w.calibration is None and a.comparison_renders is None
        assert not any(b.isEnabled() for b in a.comparison_buttons.values())
    finally:
        close(app, w)
