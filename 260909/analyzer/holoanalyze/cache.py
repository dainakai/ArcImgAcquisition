"""Visited display depths only; focus scans never populate the bounded cache."""
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import tempfile
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
    padding_size: int = 4096

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


def padding_label(size):
    return "パディングなし" if size == 0 else f"{size // 1024}k 平均値" if size in (4096, 8192) else f"{size}² 平均値"


class RenderCache:
    """Per-analysis LRU of cropped images, spilling visited depths to temporary files.

    Keys include depth and display padding. An instance is never shared between
    input pairs, optical settings or recovered fields. Full padded FFT arrays
    and Tamura scan images are not written here.
    """
    def __init__(self, megabytes=256):
        self.limit = megabytes*1024*1024
        self.memory = OrderedDict()
        self.files = {}
        self.nbytes = 0
        self.directory = None

    def get(self, key, cancel):
        cancel.check()
        if key in self.memory:
            self.memory.move_to_end(key)
            return self.memory[key]
        if key not in self.files:
            return None
        with np.load(self.files[key], allow_pickle=False) as archive:
            renders = {}
            for mode in archive['modes'].tolist():
                cancel.check()
                limits = archive[mode+'_linear']
                renders[mode] = Render(key[0], archive[mode+'_filtered'], archive[mode+'_unfiltered'],
                    archive[mode+'_previews'], tuple(limits) if len(limits) else None, padding_size=key[1])
        self.put(key, renders, cancel)
        return renders

    def put(self, key, renders, cancel):
        cancel.check()
        for render in renders.values():
            render.prepare_preview()
        if key not in self.memory:
            self.memory[key] = renders
            self.nbytes += sum(r.nbytes for r in renders.values())
        self.memory.move_to_end(key)
        try:
            self._trim(cancel)
        except Exception:
            # A full disk or cancellation must not accumulate new images above
            # the memory budget on every subsequent display request.
            dropped = self.memory.pop(key, None)
            if dropped is not None:
                self.nbytes -= sum(r.nbytes for r in dropped.values())
            raise

    def _trim(self, cancel):
        while self.nbytes > self.limit and self.memory:
            victim, values = next(iter(self.memory.items()))
            if victim not in self.files:
                cancel.check()
                if self.directory is None:
                    self.directory = tempfile.TemporaryDirectory(prefix='dualholo-display-')
                path = Path(self.directory.name)/f'{len(self.files):08d}.npz'
                arrays = {'modes': np.array(list(values))}
                for mode, render in values.items():
                    render.prepare_preview()
                    arrays.update({mode+'_filtered': render.filtered, mode+'_unfiltered': render.unfiltered,
                        mode+'_previews': render.previews, mode+'_linear': np.array(render.linear_limits or ())})
                temporary = path.with_suffix('.partial')
                try:
                    with temporary.open('wb') as stream:
                        np.savez(stream, **arrays)
                    cancel.check()
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
                self.files[victim] = path
            self.memory.pop(victim)
            self.nbytes -= sum(r.nbytes for r in values.values())

    def clear(self):
        self.memory.clear()
        self.files.clear()
        self.nbytes = 0
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None
