from dataclasses import replace
from pathlib import Path
import gc
import numpy as np
import pytest
from holoanalyze.cache import Render
from holoanalyze.config import Config, save_config, load_config
from holoanalyze.data import Frame, ImagePair
from holoanalyze.engine import Cancellation, Propagator, focus_peaks, phase_recover
from optical_padding import mean_pad
from test_calibration import identity_calibration


def test_interior_peak_ignores_high_boundary_and_rejects_flat_monotonic():
    config = replace(Config(), peak_prominence_fraction=.03)
    def curve(values):
        return [(i, v, v) for i, v in enumerate(values)]
    assert focus_peaks(curve([20, 8, 2, 4, 7, 4, 2, 1]), config) == [4]
    assert focus_peaks(curve([1, 2, 4, 4, 4, 2, 1]), config) == [3]
    assert focus_peaks(curve([9, 8, 7, 6, 5]), config) == []
    assert focus_peaks(curve([2]*8), config) == []
    assert focus_peaks(curve([0, .01, 0]), config) == [1]
    assert focus_peaks(curve([1, 1.01, 1]), config) == []


def test_explicit_padding_threads_and_yaml_roundtrip(tmp_path, monkeypatch):
    config = replace(Config(), padding_size=128, compute_threads=3)
    save_config(config, tmp_path/'settings.yaml')
    assert load_config(tmp_path/'settings.yaml').compute_threads == 3
    field = np.full((40, 50), 2+3j, np.complex64)
    padded, crop = mean_pad(field, side=128)
    assert padded.shape == (128, 128) and padded[0, 0] == 2+3j
    np.testing.assert_array_equal(padded[crop], field)
    with pytest.raises(ValueError):
        mean_pad(field, side=32)
    from holoanalyze import engine
    calls = []
    fft2, ifft2 = engine.fft.fft2, engine.fft.ifft2
    def observed_fft(*a, **kw):
        calls.append(kw['workers'])
        return fft2(*a, **kw)
    def observed_ifft(*a, **kw):
        calls.append(kw['workers'])
        return ifft2(*a, **kw)
    monkeypatch.setattr(engine.fft, 'fft2', observed_fft)
    monkeypatch.setattr(engine.fft, 'ifft2', observed_ifft)
    prop, cancel = Propagator(config), Cancellation()
    output = prop.propagate(field, prop.transfer(.1, cancel), cancel)
    assert calls == [3, 3]
    np.testing.assert_allclose(output, field, atol=1e-5)
    with pytest.raises(ValueError):
        replace(config, compute_threads=5).validate()


def test_gs_never_uses_bandlimit_even_beyond_sampling_limit(monkeypatch):
    from optical_bandlimit import NumpyAngularSpectrumBandlimit
    def forbidden(*args, **kwargs):
        raise AssertionError('GS must never use the anti-alias window')
    monkeypatch.setattr(NumpyAngularSpectrumBandlimit, 'window', forbidden)
    config = replace(Config(), padding_size=128, plane_separation_mm=100)
    image = np.random.default_rng(20).uniform(80, 120, (80, 80)).astype(np.float32)
    pair = ImagePair((Frame(image, config.serial0), Frame(image, config.serial1)))
    cal = identity_calibration(image.shape, config)
    output = phase_recover(pair, cal, config, 2, Cancellation(), lambda *a: None)
    np.testing.assert_allclose(abs(output)**2, image, rtol=1e-5)
