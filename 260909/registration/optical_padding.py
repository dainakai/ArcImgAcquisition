"""Shared, fixed-size mean padding for every optical propagation calculation."""
import numpy as np

PROPAGATION_SHAPE = (4096, 4096)

def mean_pad(field):
    field = np.asarray(field)
    if field.ndim != 2 or any(n > p for n, p in zip(field.shape, PROPAGATION_SHAPE)):
        raise ValueError('Optical propagation requires a 2D field no larger than 4096 x 4096')
    if not field.size or not np.all(np.isfinite(field)):
        raise ValueError('Optical input must be nonempty and finite')
    h, w = field.shape
    y, x = (4096 - h) // 2, (4096 - w) // 2
    crop = np.s_[y:y+h, x:x+w]
    dtype = np.complex64 if np.iscomplexobj(field) else np.float32
    mean = field.mean(dtype=np.complex128 if np.iscomplexobj(field) else np.float64)
    padded = np.full(PROPAGATION_SHAPE, mean, dtype=dtype)
    padded[crop] = field
    return padded, crop
