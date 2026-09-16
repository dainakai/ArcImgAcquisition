"""Depth-dependent 2D Nyquist band limit for 4096-square angular spectra.

Frequencies are unshifted FFT frequencies. For each direction j, retain only
q_j = 2 |z| |f_j| / (L sqrt(lambda^-2 - fx^2 - fy^2)) < 1.
This addresses transfer-function sampling, not missing measured fringes or
arbitrary circular-convolution boundary errors. Units: pitch/wavelength in um,
distance in mm. The 1e-6 guard covers float32 evaluation of precomputed slopes.
"""
import math


def _cosine_window(qx, qy, z, cutoff, width):
    import torch
    tx=((cutoff-z*qx)/width).clamp(0,1)
    ty=((cutoff-z*qy)/width).clamp(0,1)
    return (.5-.5*torch.cos(torch.pi*tx))*(.5-.5*torch.cos(torch.pi*ty))


_compiled_cosine_window = None


class ExactAngularSpectrumBandlimit:
    def __init__(self, side, pitch_um, wavelength_um, device,
                 taper_fraction=.05, nyquist_guard=1e-6):
        import torch
        global _compiled_cosine_window
        if _compiled_cosine_window is None:
            _compiled_cosine_window = torch.compile(_cosine_window, fullgraph=True)
        if side != 4096:
            raise ValueError('Optical propagation must use 4096 x 4096')
        if pitch_um <= 0 or wavelength_um <= 0 or not 0 < taper_fraction < 1:
            raise ValueError('Invalid optical sampling or taper')
        if not 0 < nyquist_guard < .01:
            raise ValueError('Invalid numerical Nyquist guard')
        self.side=side; self.pitch_um=float(pitch_um)
        self.wavelength_um=float(wavelength_um)
        self.extent_um=side*self.pitch_um
        self.safe_ratio=1-float(nyquist_guard)
        self.taper_fraction=float(taper_fraction)
        f=torch.fft.fftfreq(side,d=self.pitch_um,device=device,dtype=torch.float64)
        fx=f[None,:].abs(); fy=f[:,None].abs()
        kz2=self.wavelength_um**-2-fx.square()-fy.square()
        if bool((kz2<=0).any()):
            raise ValueError('Non-propagating spatial frequencies are unsupported')
        inv_kz=torch.rsqrt(kz2)
        scale=2000/self.extent_um
        self.qx_per_mm=(scale*fx*inv_kz).float()
        self.qy_per_mm=(scale*fy*inv_kz).float()
        self.max_slope=max(float(self.qx_per_mm.max()),float(self.qy_per_mm.max()))
        self.ones=torch.ones((side,side),device=device,dtype=torch.float32)
        # A tensor distance avoids recompiling the same kernel for every z.
        self.distance=torch.zeros((),device=device,dtype=torch.float32)

    def window(self, z_mm):
        z=abs(float(z_mm))
        if not math.isfinite(z):
            raise ValueError('Nonfinite propagation distance')
        if z*self.max_slope <= self.safe_ratio*(1-self.taper_fraction):
            return self.ones
        width=self.safe_ratio*self.taper_fraction
        self.distance.fill_(z)
        return _compiled_cosine_window(self.qx_per_mm,self.qy_per_mm,self.distance,
                                       self.safe_ratio,width)


class NumpyAngularSpectrumBandlimit:
    """CPU counterpart, optionally evaluated in row blocks to bound memory.

    Same exact two-dimensional q condition as the existing Torch implementation;
    no Torch import, accelerator, compilation cache or change to the FFT size.
    """
    def __init__(self, side, pitch_um, wavelength_um, taper_fraction=.05, nyquist_guard=1e-6):
        import numpy as np
        if side != 4096:
            raise ValueError('Optical propagation must use 4096 x 4096')
        if not all(math.isfinite(v) and v > 0 for v in (pitch_um, wavelength_um)):
            raise ValueError('Invalid optical sampling')
        if not 0 < taper_fraction < 1 or not 0 < nyquist_guard < .01:
            raise ValueError('Invalid taper or numerical guard')
        self.f = np.fft.fftfreq(side, d=pitch_um)
        self.pitch_um = float(pitch_um)
        self.wavelength_um = float(wavelength_um)
        self.extent_um = side * pitch_um
        self.safe_ratio = 1 - nyquist_guard
        self.taper_fraction = taper_fraction
        if wavelength_um**-2 <= 2 * np.max(self.f**2):
            raise ValueError('Non-propagating spatial frequencies are unsupported')

    def window(self, z_mm, rows=slice(None)):
        import numpy as np
        z = abs(float(z_mm))
        if not math.isfinite(z):
            raise ValueError('Nonfinite propagation distance')
        fx = np.abs(self.f[None, :])
        fy = np.abs(self.f[rows, None])
        scale = 2000 * z / self.extent_um / np.sqrt(self.wavelength_um**-2-fx*fx-fy*fy)
        width = self.safe_ratio * self.taper_fraction
        tx = np.clip((self.safe_ratio-scale*fx)/width, 0, 1)
        ty = np.clip((self.safe_ratio-scale*fy)/width, 0, 1)
        return ((.5-.5*np.cos(np.pi*tx))*(.5-.5*np.cos(np.pi*ty))).astype(np.float32)
