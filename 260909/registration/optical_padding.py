"""Shared, fixed-size mean padding for every optical propagation calculation."""
import numpy as np

PROPAGATION_SHAPE = (4096, 4096)

def mean_pad(field, *, side=4096):
    # Nondefault sizes are explicitly selected by DualHolo Analyze. Research
    # callers retain the fixed 4096 default; no automatic resizing is performed.
    if isinstance(side, bool) or not isinstance(side, int) or not 16 <= side <= 8192:
        raise ValueError('Padding side must be an integer between 16 and 8192')
    field = np.asarray(field)
    if field.ndim != 2 or any(n > side for n in field.shape):
        raise ValueError(f'Optical input exceeds the selected {side} x {side} padding')
    if not field.size or not np.all(np.isfinite(field)):
        raise ValueError('Optical input must be nonempty and finite')
    h, w = field.shape
    y, x = (side - h) // 2, (side - w) // 2
    crop = np.s_[y:y+h, x:x+w]
    dtype = np.complex64 if np.iscomplexobj(field) else np.float32
    mean = field.mean(dtype=np.complex128 if np.iscomplexobj(field) else np.float64)
    padded = np.full((side, side), mean, dtype=dtype)
    padded[crop] = field
    return padded, crop
