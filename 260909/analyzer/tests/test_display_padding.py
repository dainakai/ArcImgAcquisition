"""Display FFT size, cancellation and exports must agree with the visible controls."""
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QFileDialog

from holoanalyze import engine
from holoanalyze.cache import Render, RenderCache
from holoanalyze.config import Config, load_config, save_config
from holoanalyze.data import Frame, ImagePair, read_image
from holoanalyze.engine import Analysis, Cancellation, Cancelled, Reconstruction
from holoanalyze.window import MainWindow
from test_gui import FakeReconstruction, wait, close


def test_display_padding_config_roundtrip_and_validation(tmp_path):
    for size in (0, 4096, 8192):
        config = replace(Config(), display_padding_size=size, display_cache_megabytes=0)
        save_config(config, tmp_path/'config.yaml')
        loaded = load_config(tmp_path/'config.yaml')
        assert loaded.display_padding_size == size and loaded.padding_size == 4096
    for size in (True, 4096., 1024, -1, '8192'):
        with pytest.raises(ValueError):
            replace(Config(), display_padding_size=size).validate()


def test_visited_depth_cache_spills_and_separates_depth_padding_and_inputs(monkeypatch):
    config = replace(Config(), padding_size=128, display_cache_megabytes=0)
    cancel = Cancellation()
    field = np.random.default_rng(37).uniform(1, 10, (40, 60)).astype(np.float32)
    rec = Reconstruction(field, config, cancel)
    results = {(z, pad): rec.render(z, cancel, pad) for z, pad in ((.1, 0), (.2, 0), (.1, 128))}
    cache = rec.cache
    directory = Path(cache.directory.name)
    assert len(cache.files) == 3 and not cache.memory and cache.nbytes == 0
    assert not np.allclose(results[.1, 0].unfiltered, results[.1, 128].unfiltered)
    other = Reconstruction(field*2, config, cancel).render(.1, cancel, 0)
    np.testing.assert_allclose(other.unfiltered, results[.1, 0].unfiltered*4, rtol=1e-5)
    def forbidden(*args, **kwargs):
        raise AssertionError('A visited depth and padding must not rerun the FFT')
    monkeypatch.setattr(engine.fft, 'fft2', forbidden)
    monkeypatch.setattr(engine.fft, 'ifft2', forbidden)
    for (z, pad), expected in results.items():
        restored = rec.render(z, cancel, pad)
        assert restored.z_mm == z and restored.padding_size == pad
        for name in ('filtered', 'unfiltered', 'previews'):
            np.testing.assert_array_equal(getattr(restored, name), getattr(expected, name))
    cache.clear()
    assert not directory.exists() and not cache.files


def test_cancelled_cache_spill_does_not_leave_partial_files(monkeypatch):
    cancel, cache = Cancellation(), RenderCache(0)
    save = np.savez
    def interrupted(*args, **kwargs):
        save(*args, **kwargs)
        cancel.cancel()
    monkeypatch.setattr(np, 'savez', interrupted)
    with pytest.raises(Cancelled):
        cache.put((1., 4096), {'image': Render(1, np.ones((4, 8)), np.ones((4, 8)))}, cancel)
    assert not cache.files and not cache.memory and cache.nbytes == 0
    assert not list(Path(cache.directory.name).iterdir())
    cache.clear()


def test_disk_failure_keeps_display_cache_bounded(monkeypatch):
    cancel, cache = Cancellation(), RenderCache(0)
    def disk_full(*args, **kwargs):
        raise OSError('No space left (test)')
    monkeypatch.setattr(np, 'savez', disk_full)
    for z in range(3):
        with pytest.raises(OSError):
            cache.put((z, 4096), {'image': Render(z, np.ones((4, 8)), np.ones((4, 8)))}, cancel)
        assert cache.nbytes == 0 and not cache.memory and not cache.files
    cache.clear()


@pytest.mark.optical
def test_actual_8k_complex_mean_padding_preserves_roi_and_field():
    # A uniform complex field stays uniform only when the border uses its
    # complex mean. Zero padding or an amplitude-only mean would diffract it.
    field = np.full((47, 63), 2+3j, np.complex64)
    original = field.copy()
    rec = Reconstruction(field, Config(), Cancellation())
    result = rec.render(18, Cancellation(), 8192)
    assert rec.spectrum.shape == (8192, 8192) and result.padding_size == 8192
    assert result.filtered.shape == field.shape
    np.testing.assert_allclose(result.filtered, 13, rtol=1e-5)
    np.testing.assert_allclose(result.unfiltered, 13, rtol=1e-5)
    np.testing.assert_array_equal(field, original)
    # Returning to a native FFT releases the large spectrum/grid, not the field.
    native = rec.render(18, Cancellation(), 0)
    assert rec.spectrum.shape == field.shape and rec.propagator.shape == field.shape
    np.testing.assert_allclose(native.filtered, 13, rtol=1e-5)


class PaddingReconstruction(FakeReconstruction):
    def render(self, z, cancel, padding_size=4096):
        result = super().render(z, cancel, padding_size)
        # Distinct padding results make stale-image export detectable.
        values = np.arange(2000, dtype=np.float32).reshape(40, 50)+padding_size+z
        return Render(z, values, values+10, padding_size=padding_size).prepare_preview()


def test_padding_latest_request_wins_keeps_view_and_exports_current_image(app, tmp_path, monkeypatch):
    w = MainWindow(Config(output_dir=str(tmp_path)), tmp_path/'config.yaml')
    w.show()
    a, viewer = w.acquisition, w.acquisition.viewer
    a.set_pair(ImagePair((Frame(np.ones((40, 50)), w.config.serial0), None)))
    a.analysis_metadata = {'mode': 'gabor_cam0'}
    analysis = Analysis(PaddingReconstruction(), curve=[(40, 1, 2), (41, 3, 4), (42, 2, 1)])
    try:
        viewer.set_analysis(analysis)
        wait(app, lambda: w.worker is None and viewer.rendered is not None)
        view = viewer.panel.view
        view.original_size()
        view.zoom(24)
        app.processEvents()
        view.horizontalScrollBar().setValue(100)
        view.verticalScrollBar().setValue(120)
        transform, position = view.transform(), view.mapToScene(QPoint(30, 40))
        curve = list(analysis.curve)
        for size in (0, 8192, 4096, 0):
            viewer.padding.setCurrentIndex(viewer.padding.findData(size))
        wait(app, lambda: w.worker is None)
        assert viewer.rendered.padding_size == 0 and viewer.rendered.z_mm == 41
        assert analysis.curve == curve and analysis.scan_padding_size == 4096
        assert view.transform() == transform and view.mapToScene(QPoint(30, 40)) == position
        for size in (0, 4096, 8192):
            viewer.padding.setCurrentIndex(viewer.padding.findData(size))
            wait(app, lambda: w.worker is None)
            assert viewer.rendered.padding_size == size
            for suffix in ('.tiff', '.png'):
                saved = tmp_path/f'pad{size}{suffix}'
                def choose(parent, title, default, filters):
                    assert f'_pad{size or "none"}.tiff' in default
                    return str(saved), 'PNG' if suffix == '.png' else 'TIFF'
                monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose)
                a.save_image()
                wait(app, lambda: w.worker is None)
                expected = viewer.rendered.filtered if suffix == '.tiff' else view.pixels
                np.testing.assert_array_equal(read_image(saved), expected)
                metadata = json.loads(saved.with_suffix(suffix+'.json').read_text())
                assert metadata['display_padding_size'] == size and metadata['tamura_padding_size'] == 4096
                assert metadata['display_fft_shape'] == ([size]*2 if size else [40, 50])
        # Stop returns the control to the result still on screen.
        viewer.padding.setCurrentIndex(viewer.padding.findData(0))
        w.stop()
        wait(app, lambda: w.worker is None)
        assert viewer.padding_size == viewer.rendered.padding_size == 8192
        assert viewer.pending_depth is None
        # Errors must do the same, so a subsequent save cannot claim wrong padding.
        def fail(*args, **kwargs):
            raise RuntimeError('Display allocation failed (test)')
        monkeypatch.setattr(analysis.reconstruction, 'render', fail)
        viewer.padding.setCurrentIndex(viewer.padding.findData(4096))
        wait(app, lambda: w.worker is None)
        assert viewer.padding_size == viewer.rendered.padding_size == 8192
        assert 'Display allocation' in w.message.text()
    finally:
        close(app, w)
