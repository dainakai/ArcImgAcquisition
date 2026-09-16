"""Install, smoke-test and archive a standalone release on its native OS."""
import argparse
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--build", required=True, type=Path)
parser.add_argument("--platform", required=True)
parser.add_argument("--output", default="dist", type=Path)
args = parser.parse_args()
build = args.build.resolve()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
source = Path(__file__).resolve().parents[1]
cache = (build / "CMakeCache.txt").read_text()
assert "HOLO_BUNDLED_OPENCV:BOOL=ON" in cache, "Packaging requires a static bundled OpenCV build"
version = next(line.split("=", 1)[1] for line in cache.splitlines() if line.startswith("CMAKE_PROJECT_VERSION:STATIC="))
name = f"DualHolo-{version}-{args.platform}"
with tempfile.TemporaryDirectory(prefix="dual_holo_package_") as tmp:
    stage = Path(tmp) / name
    subprocess.run(["cmake", "--install", str(build), "--config", "Release",
                    "--component", "app", "--prefix", str(stage)], check=True)
    inspector = stage / ("inspect_cameras.exe" if sys.platform == "win32" else "inspect_cameras")
    assert inspector.is_file(), "Camera runtime diagnostic executable missing"
    if sys.platform == "darwin":
        exe = stage / "DualHolo.app/Contents/MacOS/DualHolo"
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(stage / "DualHolo.app")], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(stage / "DualHolo.app")], check=True)
        deps = subprocess.check_output(["otool", "-L", str(exe)], text=True)
        assert "/opt/homebrew/" not in deps and "/usr/local/" not in deps and "Spinnaker" not in deps, deps
    else:
        exe = stage / ("dual_holo.exe" if sys.platform == "win32" else "dual_holo")
        if sys.platform == "linux":
            deps = subprocess.check_output(["ldd", str(exe)], text=True)
            assert "not found" not in deps and "libopencv" not in deps and "Spinnaker" not in deps, deps
    subprocess.run([sys.executable, str(source / "tests/test_cli.py"), str(exe)], check=True)
    for notice in ("opencv/LICENSE", "3rdparty/zlib/LICENSE", "3rdparty/libtiff/COPYRIGHT"):
        assert (stage / "licenses" / notice).is_file(), f"Missing third-party notice: {notice}"
    archive = Path(shutil.make_archive(str(output / name), "gztar" if sys.platform == "linux" else "zip", tmp, name))
    # Verify the distributed bytes, including permissions after extraction.
    unpacked = Path(tmp) / "unpacked"
    shutil.unpack_archive(archive, unpacked)
    restored = unpacked / name / exe.relative_to(stage)
    if sys.platform == "darwin":
        # zipfile extraction does not restore POSIX modes; Finder/ditto does.
        subprocess.run(["ditto", "-x", "-k", str(archive), str(unpacked)], check=True)
    subprocess.run([sys.executable, str(source / "tests/test_cli.py"), str(restored)], check=True)
    gui_command = [str(restored), "--simulate", "--seconds", "1", "--output", str(Path(tmp) / "gui-smoke")]
    if sys.platform == "linux":
        gui_command = ["xvfb-run", "-a", *gui_command]
    subprocess.run(gui_command, cwd=unpacked, check=True, timeout=30)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / (archive.name + ".sha256")).write_text(f"{digest}  {archive.name}\n", newline="\n")
    print(f"Packaged and verified: {archive.name} ({archive.stat().st_size:,} bytes; {platform.machine()})")
