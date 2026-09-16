import os
from pathlib import Path
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[name] = "1"
sys.dont_write_bytecode = True
root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root), str(root.parent / "registration")]

import cv2
import pytest

cv2.setNumThreads(1)
cv2.ocl.setUseOpenCL(False)


def pytest_addoption(parser):
    parser.addoption("--run-optical", action="store_true", help="Run full 4096 CPU propagation on an authorized compute host")


def pytest_configure(config):
    config.addinivalue_line("markers", "optical: full 4096-square propagation; authorized compute host only")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-optical"):
        skip = pytest.mark.skip(reason="Use --run-optical on an authorized compute host")
        for item in items:
            if "optical" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def app():
    from PySide6.QtWidgets import QApplication
    instance = QApplication.instance() or QApplication([])
    yield instance
