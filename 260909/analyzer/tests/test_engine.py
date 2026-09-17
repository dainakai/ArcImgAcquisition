from dataclasses import replace
import numpy as np
import pytest

from optical_padding import mean_pad
from optical_bandlimit import NumpyAngularSpectrumBandlimit
from holoanalyze.config import Config
from holoanalyze.data import Frame, ImagePair
from holoanalyze.engine import Cancellation, Cancelled, Reconstruction, analyze, depths, phase_recover, tamura
from test_calibration import identity_calibration


def test_fixed_mean_padding_and_no_extra_flip():
    for data in (np.array([[1, 9], [25, 49]], np.float32)**.5,
                 np.array([[1+2j, 3+8j], [2-1j, 8+4j]], np.complex64)):
        padded, crop = mean_pad(data)
        assert padded.shape == (4096, 4096)
        np.testing.assert_array_equal(padded[crop], data)
        assert padded[0, 0] == data.mean()
    with pytest.raises(ValueError):
        mean_pad(np.ones((5, 4097)))


def test_exact_2d_guard_taper_and_grid():
    band = NumpyAngularSpectrumBandlimit(4096, 2.74, .515)
    z = 180
    rows = slice(1700, 1710)
    window = band.window(z, rows)
    fx, fy = abs(band.f[None, :]), abs(band.f[rows, None])
    qscale = 2*z*1000/(4096*2.74*np.sqrt(.515**-2-fx*fx-fy*fy))
    invalid = (qscale*fx >= 1-1e-6) | (qscale*fy >= 1-1e-6)
    assert np.all(window[invalid] == 0)
    assert np.all((window >= 0) & (window <= 1))
    np.testing.assert_array_equal(band.window(.01, rows), np.ones((10, 4096)))
    np.testing.assert_allclose(depths(-1, 1, .3), [-1, -.7, -.4, -.1, .2, .5, .8])
    assert tamura(np.zeros((8, 8))) == 0
    with pytest.raises(ValueError):
        depths(1, 0, .1)


@pytest.mark.optical
def test_full_4096_identity_constant_and_recomputation():
    cancel, config = Cancellation(), Config()
    intensity = np.random.default_rng(12).uniform(20, 180, (64, 80)).astype(np.float32)
    reconstruction = Reconstruction(np.sqrt(intensity), config, cancel)
    assert reconstruction.spectrum is None
    rendered = reconstruction.render(0, cancel)
    assert reconstruction.spectrum.shape == (4096, 4096)
    np.testing.assert_allclose(rendered.filtered, intensity, rtol=2e-5, atol=1e-4)
    np.testing.assert_allclose(rendered.unfiltered, intensity, rtol=2e-5, atol=1e-4)
    assert reconstruction.render(0, cancel) is not rendered
    del reconstruction
    reconstruction = Reconstruction(np.full((64, 80), 10, np.float32), config, cancel)
    rendered = reconstruction.render(180, cancel)
    np.testing.assert_allclose(rendered.filtered, 100, rtol=1e-5)
    np.testing.assert_allclose(rendered.unfiltered, 100, rtol=1e-5)


@pytest.mark.optical
def test_nonconstant_field_against_analytic_two_frequency_solution():
    # A DC field plus one exact DFT mode has a closed-form propagation result.
    # This independently checks FFT ordering, phase sign and taper attenuation.
    config, cancel = Config(), Cancellation()
    side, kx, ky = 4096, 1000, 600
    x = np.arange(side, dtype=np.float64)[None, :]
    y = np.arange(side, dtype=np.float64)[:, None]
    initial_phase = 2*np.pi*(kx*x+ky*y)/side
    field = (2+.2*np.exp(1j*initial_phase)).astype(np.complex64)
    fx, fy = kx/(side*2.74), ky/(side*2.74)
    kz = np.sqrt(.515**-2-fx*fx-fy*fy)
    z = .975*(side*2.74)*kz/(2000*fx)
    phase = -2*np.pi*z*1000*(fx*fx+fy*fy)/(kz+1/.515)
    reconstruction = Reconstruction(field, config, cancel)
    del field
    result = reconstruction.render(z, cancel)
    expected = 4.04+.8*np.cos(initial_phase+phase)
    np.testing.assert_allclose(result.unfiltered, expected, rtol=3e-5, atol=3e-5)
    safe, taper = 1-1e-6, .05
    qx, qy = 2000*z*fx/(side*2.74*kz), 2000*z*fy/(side*2.74*kz)
    weight = np.prod([.5-.5*np.cos(np.pi*np.clip((safe-q)/(safe*taper), 0, 1)) for q in (qx, qy)])
    expected = 4+(.2*weight)**2+.8*weight*np.cos(initial_phase+phase)
    np.testing.assert_allclose(result.filtered, expected, rtol=3e-5, atol=3e-5)
    assert 0 < weight < 1


@pytest.mark.optical
def test_gs_constraints_cancellation_and_incremental_scan():
    config = replace(Config(), plane_separation_mm=-12)
    image = np.full((64, 80), 100, np.uint8)
    pair = ImagePair((Frame(image, config.serial0), Frame(image, config.serial1)))
    cal = identity_calibration(image.shape, config)
    output = phase_recover(pair, cal, config, 1, Cancellation(), lambda *a: None)
    np.testing.assert_allclose(abs(output)**2, image, rtol=1e-5)
    cancel = Cancellation()
    rows = []
    def progress(stage, current, total, row):
        if row is not None:
            rows.append(row)
            cancel.cancel()
    result = analyze(pair, "gabor_cam0", config, None, 1, [0, 1, 2], cancel, progress)
    assert result.stopped and len(result.curve) == 1 and len(rows) == 1
    assert result.best_filtered is None  # an endpoint is never a focus estimate
    assert result.reconstruction.spectrum is None
    assert not hasattr(result.reconstruction, "cache")
    with pytest.raises(Cancelled):
        result.reconstruction.render(1, cancel)
    with pytest.raises(ValueError):
        phase_recover(pair, cal, Config(), 1, Cancellation(), lambda *a: None)
