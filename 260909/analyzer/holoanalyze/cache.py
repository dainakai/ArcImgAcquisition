"""One displayed depth and its filtered/unfiltered previews; no image stack."""
from dataclasses import dataclass
import numpy as np


def display_pixels(image, limits):
    lo, hi = limits
    return np.rint(np.clip((image-lo)/max(float(hi-lo), 1e-12), 0, 1)*255).astype(np.uint8)


@dataclass
class Render:
    z_mm: float
    filtered: np.ndarray
    unfiltered: np.ndarray
    previews: np.ndarray | None = None
    linear_limits: tuple | None = None

    def prepare_preview(self, limits=None):
        if self.previews is None:
            if limits is None:
                limits = np.percentile(self.filtered, [1, 99])
            self.previews = np.stack([display_pixels(a, limits) for a in (self.filtered, self.unfiltered)])
        return self

    def pixels(self, filtered=True, normalize=True):
        if normalize:
            return self.prepare_preview().previews[0 if filtered else 1]
        limits = self.linear_limits or (0, max(float(self.filtered.max()), float(self.unfiltered.max())))
        return display_pixels(self.filtered if filtered else self.unfiltered, limits)

    @property
    def nbytes(self):
        return self.filtered.nbytes+self.unfiltered.nbytes+(self.previews.nbytes if self.previews is not None else 0)
