#!/usr/bin/env python3
"""Source and frozen entry point; no SDK import or camera connection at startup."""
import os
from pathlib import Path
import sys

# FFT explicitly uses the configured 1–4 CPU workers. Keep unrelated BLAS/OpenCV
# pools serial so they do not multiply the compute concurrency.
for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "1"
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
sys.dont_write_bytecode = True
root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
sys.path.insert(0, str(root.parent / "registration" if not getattr(sys, "frozen", False) else root / "registration"))


def main():
    import argparse
    import cv2
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from holoanalyze.config import Config, load_config
    from holoanalyze.window import MainWindow

    parser = argparse.ArgumentParser(description="DualHolo Analyze — camera-free startup; CPU-only reconstruction")
    parser.add_argument("--config", type=Path)
    simulation = parser.add_mutually_exclusive_group()
    simulation.add_argument("--simulate", action="store_true")
    simulation.add_argument("--simulate-native", action="store_true", help="Test the bundled C bridge without opening cameras")
    parser.add_argument("--quit-after", type=float, help="GUI smoke test: exit after this many seconds")
    args = parser.parse_args()
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("DualHolo Analyze")
    app.setOrganizationName("ArcImgAcquisition")
    adjacent = Path(sys.executable).parent / "config.yaml" if getattr(sys, "frozen", False) else root / "config.yaml"
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        adjacent = Path(sys.executable).parents[3] / "config.yaml"
    config_path = args.config or (adjacent if adjacent.is_file() else root / "config.yaml")
    startup_error = ""
    try:
        config = load_config(config_path)
        if getattr(sys, "frozen", False) and not args.config and config_path == root / "config.yaml":
            from dataclasses import replace
            from PySide6.QtCore import QStandardPaths
            output = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)) / "DualHoloAnalyze" / "captures"
            config = replace(config, output_dir=str(output))
    except Exception as exc:
        # Invalid config should not prevent offline GUI access or selecting a replacement.
        config = Config(output_dir=str(Path.home()/"Pictures"/"DualHoloAnalyze"/"captures"))
        startup_error = f"Config could not be loaded: {exc}"
        if sys.stderr is not None:
            print(startup_error, file=sys.stderr)
    window = MainWindow(config, config_path)
    if startup_error:
        window.notify(startup_error)
    available = app.primaryScreen().availableGeometry()
    window.resize(min(1480, int(available.width()*.95)), min(1000, int(available.height()*.92)))
    window.show()
    if args.simulate:
        QTimer.singleShot(0, window.start_simulation)
    if args.simulate_native:
        QTimer.singleShot(0, window.start_native_simulation)
    smoke_ok = [True]
    if args.quit_after is not None:
        def finish_smoke():
            smoke_ok[0] = not startup_error and (not (args.simulate or args.simulate_native) or window.live_pair is not None)
            if not smoke_ok[0] and sys.stderr is not None:
                print("GUI smoke check failed: " + window.message.text(), file=sys.stderr)
            window.close()
        QTimer.singleShot(int(max(args.quit_after, .1)*1000), finish_smoke)
    result = app.exec()
    return result if smoke_ok[0] else 2


if __name__ == "__main__":
    raise SystemExit(main())
