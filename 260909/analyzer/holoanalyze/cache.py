"""Keep every computed depth, spilling full-precision frames to a temporary cache."""
from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
import weakref
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

    def prepare_preview(self):
        if self.previews is None:
            limits = np.percentile(self.filtered, [1, 99])
            self.previews = np.stack([display_pixels(a, limits) for a in (self.filtered, self.unfiltered)])
        return self

    def pixels(self, filtered=True, normalize=True):
        if normalize:
            return self.prepare_preview().previews[0 if filtered else 1]
        limits = (0, max(float(self.filtered.max()), float(self.unfiltered.max())))
        return display_pixels(self.filtered if filtered else self.unfiltered, limits)

    @property
    def nbytes(self):
        return self.filtered.nbytes+self.unfiltered.nbytes+(self.previews.nbytes if self.previews is not None else 0)


class DepthCache:
    def __init__(self, megabytes, directory=None):
        self.limit = megabytes*1024*1024
        self.memory_bytes = 0
        self.entries = {}
        self.directory = directory
        self.temporary = None

    def __len__(self):
        return len(self.entries)

    def __contains__(self, z):
        return round(float(z), 9) in self.entries

    def reserve_scan(self, shape, count):
        required = max(0, int(np.prod(shape))*10*count-self.limit)
        root = Path(self.directory or tempfile.gettempdir())
        root.mkdir(parents=True, exist_ok=True)
        if required and shutil.disk_usage(root).free < required+128*1024*1024:
            raise ValueError(f"全深度の保持に約 {required/1024**3:.2f} GiB の空きが必要です。探索範囲・間隔かキャッシュ保存先を変更してください。")

    def put(self, value):
        key = round(float(value.z_mm), 9)
        if key in self.entries:
            return
        value.prepare_preview()
        if self.memory_bytes+value.nbytes <= self.limit:
            self.entries[key] = value
            self.memory_bytes += value.nbytes
            return
        if self.temporary is None:
            self.temporary = tempfile.TemporaryDirectory(prefix="dualholo-depths-", dir=self.directory,
                                                         ignore_cleanup_errors=True)
        path = Path(self.temporary.name)/f"depth_{len(self.entries):06d}.bin"
        try:
            # One contiguous file per depth avoids opening three independent
            # archives during interactive navigation. Layout: float32 F/U, uint8
            # F/U previews. Shapes are owned by this temporary cache only.
            with path.open('wb') as output:
                for array in (value.filtered, value.unfiltered, value.previews):
                    output.write(np.ascontiguousarray(array).data)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        self.entries[key] = (path, value.filtered.shape)

    def get(self, z):
        item = self.entries.get(round(float(z), 9))
        if item is None or isinstance(item, Render):
            return item
        path, shape = item
        arrays = []
        for dtype, offset in ((np.float32, 0), (np.uint8, int(np.prod(shape))*8)):
            array = np.memmap(path, dtype=dtype, mode='r', offset=offset, shape=(2, *shape))
            # Keep the temporary directory alive until the last mapped view has
            # been released, including views held by Qt. Close mappings first so
            # Windows can remove the files when TemporaryDirectory is finalized.
            weakref.finalize(array, _release_mapping, array._mmap, self.temporary)
            arrays.append(array)
        return Render(float(z), arrays[0][0], arrays[0][1], arrays[1])

    @property
    def depths(self):
        return sorted(self.entries)

    @property
    def disk_count(self):
        return sum(not isinstance(v, Render) for v in self.entries.values())


def _release_mapping(mapping, directory):
    mapping.close()
