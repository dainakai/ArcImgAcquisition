"""Synthetic known-depth glass -> focus -> map -> Apply -> two-plane GS in Qt."""
from dataclasses import replace
import cv2
import numpy as np
import pytest
from holoanalyze.calibration import Calibration
from holoanalyze.config import Config
from holoanalyze.data import Frame, ImagePair
from holoanalyze.engine import Cancellation, Propagator
from holoanalyze.window import MainWindow
from test_gui import wait, close


def glass_pair(config):
    rng = np.random.default_rng(47)
    intensity = 100+cv2.GaussianBlur(rng.normal(0, 35, (512, 512)).astype(np.float32), (0, 0), 1.4)
    prop, cancel = Propagator(config), Cancellation()
    raw = [abs(prop.propagate(np.sqrt(intensity).astype(np.complex64), prop.transfer(-z, cancel, False), cancel))**2
           for z in (3, 2)]
    raw[1] = cv2.warpAffine(raw[1], np.array([[1, 0, 2.4], [0, 1, -1.6]], np.float32), (512, 512),
                           flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
    return ImagePair(tuple(Frame(im, getattr(config, f'serial{i}'), path=f'/input/glass/cam{i}.tiff') for i, im in enumerate(raw)))


def test_complete_calibration_and_phase_workflow(app, tmp_path):
    config = replace(Config(), output_dir=str(tmp_path), padding_size=512, scan_min_mm=1, scan_max_mm=4,
        scan_step_mm=.1, gs_iterations=3, calibration_window_px=48, calibration_step_px=48, calibration_search_px=8,
        cache_megabytes=1)
    pair = glass_pair(config)
    w = MainWindow(config, tmp_path/'config.yaml')
    w.show()
    c, a = w.calibration_tab, w.acquisition
    c.set_pair(pair)
    a.set_pair(pair)
    assert not a.mode.model().item(2).isEnabled()
    c.scan_focus()
    wait(app, lambda: w.worker is None, timeout=60)
    assert c.viewers[0].rendered.z_mm == pytest.approx(3, abs=.1)
    assert c.viewers[1].rendered.z_mm == pytest.approx(2, abs=.1)
    for viewer in c.viewers:
        assert len(viewer.analysis.reconstruction.cache) == 31
        assert viewer.analysis.reconstruction.cache.disk_count > 0
    assert c.map_button.isEnabled()
    c.build_map()
    wait(app, lambda: w.worker is None, timeout=60)
    assert c.candidate is not None, w.message.text()
    metadata = c.candidate.metadata
    assert metadata['plane_separation_mm'] == pytest.approx(1, abs=.1)
    assert metadata['post_rms_px'] < .35
    assert len(c.vectors_before.vectors) >= 30 and len(c.vectors_after.vectors) >= 30
    assert not c.corrected_panel.view.item.pixmap().isNull()
    assert not a.mode.model().item(2).isEnabled(), 'Review must not implicitly apply a map'
    c.iterations.setValue(4)
    c.apply_candidate()
    assert a.mode.model().item(2).isEnabled() and w.config.gs_iterations == 4
    path = tmp_path/'calibration.npz'
    c.candidate.save(path)
    restored = Calibration.load(path)
    restored.validate_for(pair, w.config)
    a.mode.setCurrentIndex(2)
    a.start_analysis()
    wait(app, lambda: w.worker is None, timeout=60)
    assert a.viewer.analysis is not None, w.message.text()
    assert len(a.viewer.analysis.curve) == 31
    a.viewer.select_depth(2.5)
    assert w.worker is None, 'Entering an existing decimal depth must use the scan cache'
    assert a.viewer.rendered.z_mm == pytest.approx(2.5)
    # A changed calibration focus must not alter the already applied transform.
    c.viewers[0].select_depth(2.9)
    assert c.candidate is None and w.calibration is not None
    assert not c.apply_button.isEnabled()
    close(app, w)
