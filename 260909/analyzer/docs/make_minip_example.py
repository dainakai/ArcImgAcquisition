"""Reproduce the README sample from a recorded dot-plate hologram (no camera I/O)."""
import argparse
from dataclasses import asdict
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT.parent/'registration')]
from holoanalyze.config import Config
from holoanalyze.data import Frame, ImagePair, fingerprint, read_image, save_result
from holoanalyze.engine import Cancellation
from holoanalyze.minip import create_minip


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent/'imagej-minip'/'sample_minip.bmp')
    args = parser.parse_args()
    config = Config()
    image = read_image(args.input)
    pair = ImagePair((Frame(image, config.serial0, path=str(args.input)), None))
    started = time.monotonic()
    result = create_minip(pair, 'gabor_cam0', config, None, 1, 53, 55, .1, 4096, Cancellation(),
        lambda stage, n, total, data: print(f'{stage} {n}/{total}', flush=True) if n % 10 == 0 or n == total else None)
    # A small region is sufficient for the illustrated procedure. Propagation
    # above uses the entire original input, without resampling or flipping.
    x, y, size = 1550, 800, 512
    if image.shape[0] < y+size or image.shape[1] < x+size:
        raise ValueError("The example ROI requires the original 2448 × 2048 input")
    roi = (slice(y, y+size), slice(x, x+size))
    metadata = dict(kind='minip', mode='gabor_cam0', config=asdict(config), input=str(args.input),
        input_sha256=fingerprint(image), input_shape=list(image.shape), crop_xywh=[x, y, size, size],
        minimum_mm=53, maximum_mm=55, step_mm=.1, depth_count=len(result.depths_mm),
        depths_mm=result.depths_mm.tolist(), filtered=True, display_padding_size=4096,
        contrast_normalized=True, contrast_limits=list(result.limits),
        elapsed_seconds=round(time.monotonic()-started, 3))
    save_result(args.output, result.filtered[roi], result.pixels()[roi], metadata)
    print(f'Saved {args.output}: {metadata["elapsed_seconds"]} s', flush=True)


if __name__ == '__main__':
    main()
