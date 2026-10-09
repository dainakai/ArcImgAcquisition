from dataclasses import replace
import json
import struct
import threading
import time
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog
from holoanalyze import minip
from holoanalyze.config import Config, load_config
from holoanalyze.data import Frame, ImagePair, Session, load_pair, read_image, save_gray8
from holoanalyze.engine import Cancellation, Cancelled, Reconstruction, depths, phase_recover
from holoanalyze.window import MainWindow
from test_comparison import comparison_input
from test_gui import wait, close


@pytest.mark.parametrize('mode', ['gabor_cam0', 'gabor_cam1', 'phase'])
@pytest.mark.parametrize('padding', [0, 128])
def test_minip_is_minimum_of_float_intensities_without_depth_cache(mode, padding, monkeypatch):
    config = replace(Config(), padding_size=128, plane_separation_mm=1)
    pair, calibration = comparison_input(config)
    cancel = Cancellation()
    field = (phase_recover(pair, calibration, config, 2, cancel, lambda *a: None) if mode == 'phase'
             else np.sqrt(pair.frames[1 if mode == 'gabor_cam1' else 0].image))
    reference = Reconstruction(field, config, cancel)
    scan = depths(0, 2.1, .5)
    renders = [reference.render(z, cancel, padding) for z in scan]
    calls, progress = [], []
    def recover(*args):
        calls.append(1)
        return phase_recover(*args)
    def no_cache(*args):
        raise AssertionError('MinIP must not cache depth images')
    monkeypatch.setattr(minip, 'phase_recover', recover)
    monkeypatch.setattr('holoanalyze.cache.RenderCache.put', no_cache)
    result = minip.create_minip(pair, mode, config, calibration, 2, 0, 2.1, .5, padding, cancel,
                               lambda stage, n, total, data: progress.append((stage, n, total)))
    for variant in ('filtered', 'unfiltered'):
        expected = np.minimum.reduce([getattr(r, variant) for r in renders])
        np.testing.assert_allclose(getattr(result, variant), expected, rtol=1e-6)
    np.testing.assert_array_equal(result.depths_mm, [0, .5, 1, 1.5, 2])
    assert result.filtered.shape == field.shape and result.padding_size == padding
    assert calls == ([1] if mode == 'phase' else [])
    assert progress[-1][1:] == (5, 5)
    assert result.pixels().dtype == np.uint8 and result.pixels().ndim == 2


def test_cancelled_projection_never_returns_partial_result():
    config = replace(Config(), padding_size=128, plane_separation_mm=1)
    pair, calibration = comparison_input(config)
    cancel = Cancellation()
    def interrupt(stage, n, total, data):
        if stage.startswith('MinIP ·') and n == 2:
            cancel.cancel()
    with pytest.raises(Cancelled):
        minip.create_minip(pair, 'gabor_cam0', config, calibration, 2, 0, 5, .1, 0, cancel, interrupt)
    for low, high, step in ((2, 1, 1), (0, 1, 0), (0, 2, float('nan')), (0, 10001, 1)):
        with pytest.raises(ValueError):
            minip.create_minip(pair, 'gabor_cam0', config, calibration, 2, low, high, step, 0, Cancellation(), lambda *a: None)
    with pytest.raises(ValueError, match='キャリブレーション'):
        minip.create_minip(pair, 'phase', config, None, 2, 0, 1, 1, 0, Cancellation(), lambda *a: None)


@pytest.mark.parametrize('suffix', ['bmp', 'png'])
@pytest.mark.parametrize('levels', [2, 256])
def test_output_headers_and_pixels_are_always_gray8(tmp_path, suffix, levels):
    image = (np.arange(17*31) % levels).reshape(17, 31).astype(np.uint8)
    path = tmp_path / f'日本語.{suffix}'
    save_gray8(path, image)
    data = path.read_bytes()
    if suffix == 'bmp':
        assert data[:2] == b'BM' and struct.unpack_from('<H', data, 28)[0] == 8
        assert struct.unpack_from('<I', data, 30)[0] == 0  # no compression
    else:
        assert data[:8] == b'\x89PNG\r\n\x1a\n' and data[24:26] == bytes([8, 0])
    np.testing.assert_array_equal(read_image(path), image)


def test_raw_mono8_bmp_pair_keeps_pixels_and_timestamp_metadata(tmp_path):
    config = Config(output_dir=str(tmp_path))
    image = np.arange(256, dtype=np.uint8).reshape(16, 16)
    pair = ImagePair(tuple(Frame(image, getattr(config, f'serial{i}'), frame_id=20+i, exposure_ns=2**60+i,
                                host_ns=2**60+10+i) for i in (0, 1)))
    target = Session(tmp_path).save_raw(pair, config)
    loaded, _ = load_pair(next(target.glob('cam0*/*.bmp')), config)
    for old, new in zip(pair.frames, loaded.frames):
        np.testing.assert_array_equal(old.image, new.image)
        assert new.exposure_ns == old.exposure_ns and new.frame_id == old.frame_id


@pytest.mark.parametrize('mode', ['gabor_cam0', 'gabor_cam1', 'phase'])
def test_minip_gui_independent_creation_export_and_invalidation(app, tmp_path, monkeypatch, mode):
    config = replace(Config(), output_dir=str(tmp_path), padding_size=128, display_padding_size=0, plane_separation_mm=1)
    pair, calibration = comparison_input(config)
    w = MainWindow(config, tmp_path/'config.yaml')
    w.resize(1280, 800)
    w.show()
    a, view = w.acquisition, w.acquisition.minip
    try:
        a.set_pair(pair)
        w.calibration = calibration
        view.mode.setCurrentIndex(view.mode.findData(mode))
        view.minimum.setValue(0)
        view.maximum.setValue(2.1)
        view.step.setValue(.5)
        view.iterations.setValue(2)
        a.control_tabs.setCurrentIndex(2)
        a.start_minip()
        wait(app, lambda: w.worker is None)
        assert view.result is not None and a.viewer.analysis is None
        assert a.result_tabs.currentIndex() == 1 and view.explanation.isVisible()
        assert a.minip_save_button.visibleRegion().boundingRect() == a.minip_save_button.rect()
        before = view.result
        view.minimum.setValue(9)  # Old completed result has its own conditions.
        for suffix in ('bmp', 'png'):
            path = tmp_path/f'最小投影.{suffix}'
            monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(path), ''))
            a.save_minip()
            wait(app, lambda: w.worker is None)
            np.testing.assert_array_equal(read_image(path), view.panel.view.pixels)
            metadata = json.loads(path.with_suffix('.'+suffix+'.json').read_text())
            assert metadata['mode'] == mode and metadata['minimum_mm'] == 0
            assert metadata['maximum_mm'] == 2.1 and metadata['actual_maximum_mm'] == 2
            assert metadata['step_mm'] == .5 and metadata['depth_count'] == 5
            assert metadata['stored_bits'] == 8 and len(metadata['input_sha256']) == 2
            assert not path.with_suffix('.'+suffix+'.csv').exists()
        view.filtered.setChecked(False)
        a.toggle_contrast()
        assert view.result is before
        a.control_tabs.setCurrentIndex(0)
        assert a.control_tabs.currentIndex() == 0 and a.result_tabs.currentIndex() == 0
        a.resume()
        assert view.result is None and not a.minip_save_button.isEnabled()
    finally:
        close(app, w)


@pytest.mark.parametrize('key', [Qt.Key.Key_Q, Qt.Key.Key_Escape])
@pytest.mark.parametrize('kind', ['Analyze', '焦点探索', '深度再生', 'ベクトルマップ', 'MinIP'])
def test_shortcut_stops_only_worker_and_keeps_simulation_alive(app, tmp_path, key, kind):
    w = MainWindow(Config(output_dir=str(tmp_path)), tmp_path/'config.yaml')
    w.show()
    w.activateWindow()
    w.start_simulation()
    wait(app, lambda: w.live_pair is not None)
    owner_thread, callback_threads = threading.get_ident(), []
    def operation(cancel, progress):
        while True:
            cancel.check()
            progress('計算中', 1, 10, None)
            time.sleep(.01)
    w.acquisition.on_progress = lambda *a: callback_threads.append(threading.get_ident())
    try:
        w.submit(w.acquisition, kind, operation, lambda r: pytest.fail('Cancelled work was accepted'))
        wait(app, lambda: len(callback_threads) >= 2)
        frame = w.live_pair.frames[0].frame_id
        QTest.keyClick(w, key)
        wait(app, lambda: w.worker is None)
        assert w.isVisible() and not w.closing and not w.allow_close and w.timer.isActive()
        assert set(callback_threads) == {owner_thread}
        wait(app, lambda: w.live_pair.frames[0].frame_id > frame)
        QTest.keyClick(w, key)  # Idle Q/Esc never closes the window either.
        app.processEvents()
        assert w.isVisible()
        w.acquisition.capture()
        assert w.acquisition.pair is not None
    finally:
        close(app, w)


def test_calibration_defaults_match_packaged_yaml():
    from pathlib import Path
    for config in (Config(), load_config(Path(__file__).parents[1]/'config.yaml')):
        assert (config.calibration_window_px, config.calibration_search_px) == (256, 32)
