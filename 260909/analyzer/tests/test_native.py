from dataclasses import replace
from pathlib import Path
import os
import time

import pytest

from holoanalyze.camera import NativeCamera
from holoanalyze.config import Config
from holoanalyze.data import Session


def test_native_simulation_and_recorder(tmp_path):
    library = os.environ.get("HOLO_CAPTURE_LIBRARY")
    if not library:
        pytest.skip("Set HOLO_CAPTURE_LIBRARY to test the native bridge")
    config = replace(Config(), camera_library=library, output_dir=str(tmp_path))
    camera = NativeCamera(config, Session(tmp_path), simulate=True)
    try:
        end = time.monotonic()+5
        pair = None
        while pair is None and time.monotonic() < end:
            pair = camera.poll()
            time.sleep(.03)
        assert pair is not None
        assert all(f.image.shape == (480, 640) for f in pair.frames)
        assert pair.frames[0].exposure_ns == pair.frames[1].exposure_ns
        assert not camera.status()["recording"]
        camera.record(True)
        end = time.monotonic()+5
        while min(camera.status()["saved"]) < 2 and time.monotonic() < end:
            time.sleep(.03)
        camera.record(False)
        assert min(camera.status()["saved"]) >= 2
    finally:
        camera.close()
    assert not list(tmp_path.rglob("IN_PROGRESS"))
