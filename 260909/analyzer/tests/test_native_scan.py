"""Native rectangular focus FFTs are separate from selected-depth display FFTs."""
from dataclasses import replace
import numpy as np
import pytest
from scipy import fft
from holoanalyze import engine
from holoanalyze.config import Config, load_config, save_config
from holoanalyze.data import Frame, ImagePair
from optical_bandlimit import NumpyAngularSpectrumBandlimit


def test_native_rectangular_scan_only_keeps_curve_then_pads_display(monkeypatch):
    config = replace(Config(), padding_size=128)
    image = np.random.default_rng(14).uniform(5, 150, (63, 97)).astype(np.float32)
    pair = ImagePair((Frame(image, config.serial0), None))
    calls, original_fft = [], engine.fft.fft2
    def observed(field, **kwargs):
        calls.append(field.shape)
        return original_fft(field, **kwargs)
    monkeypatch.setattr(engine.fft, 'fft2', observed)
    scan = [0., .1, .2]
    result = engine.analyze(pair, 'gabor_cam0', config, None, 1, scan, engine.Cancellation(), lambda *a: None)
    assert calls == [image.shape]
    assert result.reconstruction.spectrum is None and not hasattr(result.reconstruction, 'cache')
    # Independent exact transfer on the unpadded rectangular DFT grid.
    fy = fft.fftfreq(63, d=2.74)[:, None]
    fx = fft.fftfreq(97, d=2.74)[None, :]
    k = 1/.515
    phase = -2*np.pi*1000*(fx*fx+fy*fy)/(np.sqrt(k*k-fx*fx-fy*fy)+k)
    spectrum = original_fft(np.sqrt(image))
    for row in result.curve:
        expected = abs(fft.ifft2(spectrum*np.exp(1j*phase*row[0])))**2
        assert row[2] == pytest.approx(engine.tamura(expected), rel=2e-6)
    render = result.reconstruction.render(.1, engine.Cancellation())
    assert calls == [image.shape, (128, 128)] and render.filtered.shape == image.shape
    assert result.reconstruction.render(.1, engine.Cancellation()) is not render


def test_rectangular_bandlimit_uses_both_physical_extents():
    band = NumpyAngularSpectrumBandlimit((40, 96), 2.74, .515)
    fx, fy = abs(band.fx[None, :]), abs(band.fy[:, None])
    z = 1.2
    q = 2000*z/np.sqrt(.515**-2-fx*fx-fy*fy)
    qx, qy = q*fx/(96*2.74), q*fy/(40*2.74)
    taper = lambda values: .5-.5*np.cos(np.pi*np.clip((1-1e-6-values)/((1-1e-6)*.05), 0, 1))
    np.testing.assert_allclose(band.window(z), taper(qx)*taper(qy), atol=1e-7)


def test_separate_ranges_round_trip_and_old_cache_keys_are_migrated(tmp_path):
    config = replace(Config(), cam0_scan_min_mm=40, cam0_scan_max_mm=60, cam1_scan_min_mm=70, cam1_scan_max_mm=90)
    p = tmp_path/'settings.yaml'
    save_config(config, p)
    restored = load_config(p)
    assert restored.scan_bounds(0) == (40, 60) and restored.scan_bounds(1) == (70, 90)
    with p.open('a') as f:
        f.write('cache_megabytes: 192\ncache_directory: /old/cache\n')
    assert load_config(p) == restored
    with pytest.raises(ValueError):
        replace(config, cam1_scan_max_mm=50).validate()
