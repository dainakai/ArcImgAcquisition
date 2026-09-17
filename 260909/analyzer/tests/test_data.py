import csv
from dataclasses import replace
import json

import numpy as np
import pytest
import tifffile

from holoanalyze.config import Config, load_config, save_config
from holoanalyze.data import Frame, ImagePair, Session, find_partner, load_pair, read_image, save_result


def recording(tmp_path, times, names=None):
    rows = []
    for camera, index, instant in times:
        name = f"cam{camera}_{26259157+camera}/frame_{index:06d}_id{1000-camera*900+index}.tiff"
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        tifffile.imwrite(path, np.full((17, 23), index, np.uint16))
        rows.append(dict(file=name, camera=camera, estimated_exposure_host_ns=instant, clock_uncertainty_ms=.02))
    with (tmp_path / "frames.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return [tmp_path / r["file"] for r in rows]


def test_exposure_pairing_survives_missing_frame_and_integer_precision(tmp_path):
    epoch = 2**60
    paths = recording(tmp_path, [(0, 1, epoch), (0, 2, epoch+100_000_000),
                                  (1, 1, epoch+100_000_010), (1, 2, epoch+200_000_000)])
    pair, note = find_partner(paths[1], tolerance_ms=.00002)
    assert pair == (paths[1], paths[2])
    assert "露光時刻差" in note
    assert find_partner(paths[0])[0] == (paths[0], None)
    loaded, _ = load_pair(paths[2], Config())
    assert loaded.frames[0].serial == "26259157"
    assert loaded.frames[1].image.dtype == np.uint16


def test_no_guess_without_manifest_and_legacy_pair(tmp_path):
    paths = recording(tmp_path, [(0, 1, 10), (1, 1, 10)])
    (tmp_path / "frames.csv").unlink()
    assert find_partner(paths[0])[0][1] is None
    old = tmp_path / "frame_000123"
    old.mkdir()
    for i in (0, 1):
        tifffile.imwrite(old / f"cam{i}_{26259157+i}.tiff", np.ones((8, 9), np.uint8))
    assert all(find_partner(old / "cam1_26259158.tiff")[0])
    (tmp_path / "frames.csv").write_text("pair_dir,cam0_frame_id,cam1_frame_id\nframe_000123,900,3\n")
    result, _ = load_pair(old / "cam1_26259158.tiff", Config())
    assert result.frames[0].frame_id == 900 and result.frames[1].frame_id == 3


def test_ambiguous_pair_rejected(tmp_path):
    paths = recording(tmp_path, [(0, 1, 1000), (1, 1, 1001), (1, 2, 1002)])
    assert find_partner(paths[0])[0][1] is None


def test_raw_round_trip_and_explicit_session(tmp_path):
    config = Config(output_dir=str(tmp_path))
    session = Session(tmp_path)
    assert not session.path.exists()
    pair = ImagePair(tuple(Frame(np.arange(80, dtype=np.uint16).reshape(8, 10), getattr(config, f"serial{i}"), 3, 1000, 1010)
                           for i in (0, 1)))
    output = session.save_raw(pair, config)
    loaded, _ = load_pair(next(output.glob("cam0*/*.tiff")), config)
    for old, new in zip(pair.frames, loaded.frames):
        np.testing.assert_array_equal(old.image, new.image)


def test_config_validation_and_paths(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("pixel_pitch_um: 2.74\noutput_dir: captures\n")
    config = load_config(p)
    assert config.output_dir == str(tmp_path / "captures")
    assert config.plane_separation_mm is None
    for content in ("pixel_pitch_um: 0", "scan_step_mm: .nan", "gs_iterations: 2.5", "unknown: 1", "plane_separation_mm: 0", "serial0: '../bad'"):
        p.write_text(content)
        with pytest.raises((TypeError, ValueError)):
            load_config(p)
    save_config(config, p)
    assert load_config(p) == config


def test_float_tiff_and_png_saving(tmp_path):
    intensity = np.linspace(.2, 1.5, 120, dtype=np.float32).reshape(10, 12)
    preview = np.arange(120, dtype=np.uint8).reshape(10, 12)
    for suffix in (".tiff", ".png"):
        p = tmp_path / ("再構成"+suffix)
        save_result(p, intensity, preview, {"z_mm": 10}, [(10, .2, .3)])
        np.testing.assert_array_equal(read_image(p), intensity if suffix == ".tiff" else preview)
        assert json.loads(p.with_suffix(suffix+".json").read_text())["z_mm"] == 10
        assert not list(tmp_path.glob("*.partial*"))
