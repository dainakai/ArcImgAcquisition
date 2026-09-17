from dataclasses import replace
import time
import numpy as np
import pytest
from PySide6.QtCore import QTimer, Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QDoubleSpinBox, QSpinBox, QComboBox, QSlider, QStyleOptionSpinBox, QStyle
from holoanalyze.config import Config
from holoanalyze.data import Frame, ImagePair
from holoanalyze.engine import Analysis, Render
from holoanalyze.settings import SettingsDialog
from holoanalyze.window import MainWindow
from test_calibration import identity_calibration


def wait(app, condition, timeout=15):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return
        # qWait can retain the Python GIL in PySide builds and starve the
        # QThread's Python work. Pump Qt explicitly, then yield the GIL.
        time.sleep(.01)
    raise AssertionError('Qt condition timed out')


def close(app, window):
    window.close()
    wait(app, lambda: window.allow_close and not window.isVisible())


def test_offline_window_really_closes(app, tmp_path):
    w = MainWindow(Config(output_dir=str(tmp_path)), tmp_path/'config.yaml')
    w.show()
    app.processEvents()
    assert w.isVisible() and w.camera is None
    assert w.tabs.count() == 2
    assert not any('Rec' in b.text() for b in w.findChildren(QPushButton))
    for cls in (QPushButton, QDoubleSpinBox, QSpinBox, QComboBox, QSlider):
        assert all(widget.toolTip() for widget in w.findChildren(cls))
    close(app, w)


def test_simulation_tabs_keep_independent_capture_and_resume(app, tmp_path):
    w = MainWindow(Config(output_dir=str(tmp_path)), tmp_path/'config.yaml')
    w.show()
    a, c = w.acquisition, w.calibration_tab
    assert not a.analyze_button.isEnabled() and not a.mode.model().item(2).isEnabled()
    w.start_simulation()
    wait(app, lambda: a.capture_button.isEnabled())
    a.capture()
    frozen, image = a.pair, a.pair.frames[0].image.copy()
    w.tabs.setCurrentWidget(c)
    wait(app, lambda: w.live_pair.frames[0].frame_id > frozen.frames[0].frame_id)
    c.capture()
    assert c.pair.frames[0].frame_id != frozen.frames[0].frame_id
    np.testing.assert_array_equal(a.pair.frames[0].image, image)
    c.resume()
    assert c.pair is None and a.pair is frozen
    assert not list(tmp_path.rglob('*.tiff')), 'Capture must not save automatically'
    a.resume()
    assert a.pair is None and a.viewer.analysis is None
    close(app, w)


def test_calibration_apply_gate_and_settings_invalidate_optics(app, tmp_path):
    config = Config(output_dir=str(tmp_path))
    w = MainWindow(config, tmp_path/'config.yaml')
    image = np.ones((32, 40), np.uint8)
    pair = ImagePair((Frame(image, config.serial0, path='/data/cam0.tiff'), Frame(image, config.serial1)))
    w.acquisition.set_pair(pair)
    cal = identity_calibration(image.shape, config)
    cal.metadata.update(focus_depths_mm=[50, 38], gs_iterations=17)
    w.calibration_tab.candidate_ready(cal, source='/data/cal.npz')
    assert not w.acquisition.mode.model().item(2).isEnabled()
    w.calibration_tab.apply_candidate()
    assert w.acquisition.mode.model().item(2).isEnabled()
    assert w.config.plane_separation_mm == 12 and w.acquisition.iterations.value() == 17
    assert '/data/cam0.tiff' in w.acquisition.camera_panels[0].source.toPlainText()
    w.apply_settings(replace(w.config, compute_threads=2))
    assert w.calibration is cal
    w.apply_settings(replace(w.config, wavelength_nm=532))
    assert w.calibration is None and not w.acquisition.mode.model().item(2).isEnabled()
    close(app, w)


def test_connection_failure_keeps_gui_usable(app, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('SDK is missing (test)')
    monkeypatch.setattr('holoanalyze.window.NativeCamera', fail)
    w = MainWindow(Config(output_dir=str(tmp_path)), tmp_path/'config.yaml')
    w.show()
    w.connect_camera()
    wait(app, lambda: w.worker is None)
    assert w.isVisible() and w.camera is None and w.acquisition.load_button.isEnabled()
    assert 'SDK is missing' in w.message.text()
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
        result = Render(z, np.full((40, 50), z, np.float32), np.full((40, 50), z+1, np.float32)).prepare_preview()
        return result


def test_latest_depth_wins_recompute_plot_toggle_clipboard_and_resume(app, tmp_path):
    w = MainWindow(Config(output_dir=str(tmp_path)), tmp_path/'config.yaml')
    w.show()
    a, viewer = w.acquisition, w.acquisition.viewer
    reconstruction = FakeReconstruction()
    viewer.set_analysis(Analysis(reconstruction))
    viewer.select_depth(40)
    viewer.select_depth(41)
    viewer.select_depth(42)
    wait(app, lambda: w.worker is None and viewer.rendered is not None)
    assert viewer.rendered.z_mm == 42
    before = reconstruction.calls[:]
    viewer.select_depth(42)
    viewer.filtered.setChecked(False)
    app.processEvents()
    assert reconstruction.calls == before
    a.copy_image()
    assert not QApplication.clipboard().image().isNull()
    viewer.panel.view.original_size()
    viewer.panel.view.zoom(2)
    assert viewer.panel.view.transform().m11() == 2
    viewer.select_depth(40)
    wait(app, lambda: w.worker is None)
    viewer.analysis.curve = [(40, 1, 1), (42, 2, 2)]
    viewer.set_analysis(viewer.analysis)
    wait(app, lambda: w.worker is None)
    viewer.select_depth(40)
    wait(app, lambda: w.worker is None)
    before = reconstruction.calls[:]
    rect = viewer.plot.plot_rect()
    QTest.mouseClick(viewer.plot, Qt.MouseButton.LeftButton, pos=QPoint(int(rect.right()-1), int(rect.center().y())))
    wait(app, lambda: w.worker is None)
    assert viewer.rendered.z_mm == 42 and len(reconstruction.calls) == len(before)+1
    viewer.slider.setValue(0)
    wait(app, lambda: w.worker is None)
    assert viewer.rendered.z_mm == 40 and len(reconstruction.calls) == len(before)+2
    viewer.depth_step.setCurrentIndex(0)
    option = QStyleOptionSpinBox()
    viewer.depth.initStyleOption(option)
    up = viewer.depth.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, QStyle.SubControl.SC_SpinBoxUp, viewer.depth)
    QTest.mouseClick(viewer.depth, Qt.MouseButton.LeftButton, pos=up.center())
    assert viewer.pending_depth == 40.1
    wait(app, lambda: w.worker is None)
    assert viewer.rendered.z_mm == 40.1
    viewer.depth_step.setCurrentIndex(viewer.depth_step.findData(1.))
    QTest.keyClick(viewer.depth, Qt.Key.Key_Down)
    assert viewer.pending_depth == 39.1
    wait(app, lambda: w.worker is None)
    assert viewer.rendered.z_mm == 39.1
    viewer.select_depth(43)
    a.resume()
    wait(app, lambda: w.worker is None)
    assert viewer.rendered is None and viewer.analysis is None
    close(app, w)


def test_stop_and_close_during_work_are_responsive(app, tmp_path):
    w = MainWindow(Config(output_dir=str(tmp_path)), tmp_path/'config.yaml')
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    def operation(cancel, progress):
        while True:
            cancel.check()
            time.sleep(.01)
    w.submit(w.acquisition, 'Analyze', operation, lambda r: None)
    wait(app, lambda: len(ticks) >= 5)
    w.stop()
    wait(app, lambda: w.worker is None)
    w.submit(w.acquisition, 'Analyze', operation, lambda r: None)
    close(app, w)
    timer.stop()


def test_settings_controls_apply_explicit_optical_values(app):
    dialog = SettingsDialog(Config())
    dialog.fields['padding_size'].setValue(2048)
    dialog.fields['compute_threads'].setValue(3)
    dialog.fields['wavelength_nm'].setValue(532)
    dialog.accept_validated()
    assert dialog.result_config.padding_size == 2048
    assert dialog.result_config.compute_threads == 3
    assert dialog.result_config.wavelength_nm == 532


def test_first_save_dialog_uses_session_directory(app, tmp_path, monkeypatch):
    from pathlib import Path
    from PySide6.QtWidgets import QFileDialog
    w = MainWindow(Config(output_dir=str(tmp_path)), tmp_path/'config.yaml')
    image = np.ones((32, 40), np.float32)
    a, c = w.acquisition, w.calibration_tab
    a.set_pair(ImagePair((Frame(image, w.config.serial0), Frame(image, w.config.serial1))))
    a.analysis_metadata = {'mode': 'gabor_cam0'}
    a.viewer.rendered = Render(50, image, image)
    c.candidate = identity_calibration(image.shape, w.config)
    selected = []
    def inspect_dialog(parent, title, path, filters):
        assert Path(path).parent.is_dir(), 'Qt falls back to the working directory for nonexistent parents'
        dialog = QFileDialog(parent, title, path, filters)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        assert Path(dialog.selectedFiles()[0]) == Path(path)
        selected.append(Path(path))
        return '', ''
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', inspect_dialog)
    a.save_image()
    c.save_calibration()
    assert len(selected) == 2 and all(path.is_relative_to(w.session.path) for path in selected)
    assert selected[0].parent.name == 'reconstructions'
    close(app, w)


def test_stopped_scan_does_not_start_display_work(app, tmp_path):
    w = MainWindow(Config(output_dir=str(tmp_path)), tmp_path/'config.yaml')
    reconstruction = FakeReconstruction()
    w.acquisition.viewer.set_analysis(Analysis(reconstruction, curve=[(1, 1, 1), (2, 2, 2)], stopped=True))
    app.processEvents()
    assert w.worker is None and not reconstruction.calls
    assert w.acquisition.viewer.depth.isEnabled()
    close(app, w)


def test_depth_step_row_is_separated_and_all_four_arrow_steps_render_immediately(app, tmp_path):
    w = MainWindow(Config(output_dir=str(tmp_path)), tmp_path/'config.yaml')
    w.resize(1280, 800)
    w.show()
    a, viewer = w.acquisition, w.acquisition.viewer
    a.control_tabs.setCurrentIndex(1)
    reconstruction = FakeReconstruction()
    viewer.set_analysis(Analysis(reconstruction))
    viewer.select_depth(40)
    try:
        wait(app, lambda: w.worker is None)
        for v in (viewer, *w.calibration_tab.viewers):
            assert [v.depth_step.itemData(i) for i in range(v.depth_step.count())] == [.1, .2, 1., 2.]
        lower = viewer.depth.mapToGlobal(viewer.depth.rect().bottomLeft()).y()
        upper = viewer.depth_step.mapToGlobal(viewer.depth_step.rect().topLeft()).y()
        assert upper-lower >= 8, 'The step selector must be below, separated from the depth arrows'
        value = 40
        for step in (.1, .2, 1., 2.):
            viewer.depth_step.setCurrentIndex(viewer.depth_step.findData(step))
            option = QStyleOptionSpinBox()
            viewer.depth.initStyleOption(option)
            up = viewer.depth.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option,
                                                     QStyle.SubControl.SC_SpinBoxUp, viewer.depth)
            QTest.mouseClick(viewer.depth, Qt.MouseButton.LeftButton, pos=up.center())
            value += step
            assert viewer.pending_depth == pytest.approx(value) and w.worker is not None
            wait(app, lambda: w.worker is None)
            assert viewer.rendered.z_mm == pytest.approx(value)
    finally:
        close(app, w)
