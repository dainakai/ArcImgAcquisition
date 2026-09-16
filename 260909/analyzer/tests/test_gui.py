from dataclasses import replace
from pathlib import Path
import time

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from holoanalyze.config import Config
from holoanalyze.data import Frame, ImagePair
from holoanalyze.engine import Analysis, Render
from holoanalyze.window import MainWindow
from test_calibration import identity_calibration


def wait(app, condition, timeout=10):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return
        QTest.qWait(10)
    raise AssertionError("Qt condition timed out")


def close(app, window):
    window.close()
    wait(app, lambda: window.allow_close and not window.isVisible())


def test_offline_window_really_closes(app, tmp_path):
    window = MainWindow(Config(output_dir=str(tmp_path)), tmp_path / "config.yaml")
    window.show()
    app.processEvents()
    assert window.isVisible()
    close(app, window)


def test_offline_simulation_freeze_resume_and_close(app, tmp_path):
    window = MainWindow(Config(output_dir=str(tmp_path)), tmp_path / "config.yaml")
    window.show()
    app.processEvents()
    assert window.camera is None and not window.analyze_button.isEnabled()
    assert not window.mode.model().item(2).isEnabled()
    assert not list(tmp_path.rglob("*.tiff"))
    window.start_simulation()
    wait(app, lambda: window.capture_button.isEnabled())
    window.capture()
    token = window.frozen_pair.token
    assert window.analyze_button.isEnabled()
    first = window.frozen_pair.frames[0].image.copy()
    wait(app, lambda: window.live_pair.token != token)
    np.testing.assert_array_equal(first, window.frozen_pair.frames[0].image)
    assert not list(tmp_path.rglob("*.tiff")), "Capture must not save automatically"
    window.toggle_record()
    wait(app, lambda: len(list(tmp_path.rglob("*.tiff"))) >= 4)
    window.toggle_record()
    wait(app, lambda: not list(tmp_path.rglob("IN_PROGRESS")))
    window.resume()
    assert window.frozen_pair is None and window.analysis is None
    close(app, window)


def test_phase_gate_missing_wrong_and_valid_calibration(app, tmp_path):
    config = replace(Config(output_dir=str(tmp_path)), plane_separation_mm=12)
    w = MainWindow(config, tmp_path / "config.yaml")
    image = np.ones((32, 40), np.uint8)
    w.frozen_pair = ImagePair((Frame(image, config.serial0), Frame(image, config.serial1)))
    w.update_controls()
    assert not w.mode.model().item(2).isEnabled()
    w.calibration = identity_calibration(image.shape, config)
    w.update_controls()
    assert w.mode.model().item(2).isEnabled()
    w.config = replace(config, wavelength_nm=532)
    w.update_controls()
    assert not w.mode.model().item(2).isEnabled()
    close(app, w)


def test_connection_failure_keeps_gui_usable(app, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("SDK is missing (test)")
    monkeypatch.setattr("holoanalyze.window.NativeCamera", fail)
    w = MainWindow(Config(output_dir=str(tmp_path)), tmp_path / "config.yaml")
    w.show()
    w.connect_camera()
    wait(app, lambda: w.worker is None)
    assert w.isVisible() and w.camera is None and w.load_button.isEnabled()
    assert "SDK is missing" in w.message.text()
    w.start_simulation()
    wait(app, lambda: w.live_pair is not None)
    close(app, w)


class FakeReconstruction:
    def __init__(self):
        self.calls = []

    def render(self, z, cancel):
        self.calls.append(z)
        for _ in range(10):
            cancel.check()
            time.sleep(.005)
        return Render(z, np.full((40, 50), z, np.float32), np.full((40, 50), z+1, np.float32))


def test_latest_depth_wins_toggle_no_compute_clipboard_and_resume(app, tmp_path):
    w = MainWindow(Config(output_dir=str(tmp_path)), tmp_path / "config.yaml")
    w.show()
    reconstruction = FakeReconstruction()
    w.analysis = Analysis(reconstruction)
    w.depth.setValue(40)
    w.request_depth()
    w.depth.setValue(41)
    w.request_depth()
    w.depth.setValue(42)
    w.request_depth()
    wait(app, lambda: w.worker is None and w.rendered is not None)
    assert w.rendered.z_mm == 42
    before = reconstruction.calls[:]
    w.filtered.setChecked(False)
    app.processEvents()
    assert reconstruction.calls == before
    w.copy_image()
    assert not QApplication.clipboard().image().isNull()
    w.result_view.original_size()
    w.result_view.zoom(2)
    assert w.result_view.transform().m11() == 2
    w.depth.setValue(43)
    w.request_depth()
    w.resume()
    wait(app, lambda: w.worker is None)
    assert w.rendered is None and w.analysis is None
    close(app, w)


def test_stop_and_close_during_long_work_stay_responsive(app, tmp_path):
    w = MainWindow(Config(output_dir=str(tmp_path)), tmp_path / "config.yaml")
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    def operation(cancel, progress):
        while True:
            cancel.check()
            time.sleep(.01)
    w.submit("Analyze", operation, lambda r: None)
    wait(app, lambda: len(ticks) >= 5)
    w.stop()
    wait(app, lambda: w.worker is None)
    w.submit("Analyze", operation, lambda r: None)
    close(app, w)
    timer.stop()
