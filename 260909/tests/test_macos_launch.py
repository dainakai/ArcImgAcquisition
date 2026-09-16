"""Launch the standalone .app through Finder's Launch Services, without cameras.

No --config/--output override: verify captures is beside the .app even when
Finder starts from /. The bundle itself stays read-only.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

bundle = Path(sys.argv[1]).resolve()
assert sys.platform == "darwin" and bundle.suffix == ".app", bundle
with tempfile.TemporaryDirectory(prefix="dual_holo_finder_") as tmp:
    root = Path(tmp).resolve()
    isolated = root / "Application Folder"
    isolated.mkdir()
    capture_root = isolated / "captures"
    app = isolated / "DualHolo.app"
    shutil.copytree(bundle, app, symlinks=True)
    resource = app / "Contents/Resources/config.yml"
    assert resource.is_file(), "Standalone .app must include its default settings"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    paths = [*app.rglob("*"), app]
    modes = {p: p.stat().st_mode & 0o777 for p in paths if not p.is_symlink()}
    session = None
    try:
        for p, mode in modes.items():
            p.chmod(mode & ~0o222)
        log, err = root / "launch.log", root / "launch.err"
        result = subprocess.run(["open", "-n", "-W", "--stdout", str(log), "--stderr", str(err),
                                 str(app), "--args", "--simulate", "--seconds", "2"],
                                cwd="/", capture_output=True, text=True, timeout=25)
        assert result.returncode == 0, result.stdout + result.stderr
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            stdout = log.read_text() if log.exists() else ""
            stderr = err.read_text() if err.exists() else ""
            match = re.search(r'^Session: "(.+)"$', stdout, re.M)
            if match:
                session = Path(match[1])
                if (session / "run_summary.yml").is_file():
                    break
            time.sleep(0.05)
        assert session and session.parent == capture_root, stdout + stderr
        assert session.name.endswith("_SIMULATION"), session
        assert f'Config: "{resource}"' in stdout, stdout
        summary = (session / "run_summary.yml").read_text()
        stats = dict(re.findall(r"^(\w+):\s*([^\n]*)", summary, re.M))
        assert stats["exit_reason"] == "duration", stdout + stderr + summary
        assert float(stats["run_seconds"]) >= 2, stats
        assert int(float(stats["gui_draws"])) >= 2, stats
        assert all(int(float(stats[f"received_cam{i}"])) >= 2 for i in range(2)), stats
        assert not list(session.rglob("*.tiff")), "Finder launch must start with REC OFF"
        print("Finder launch: read-only standalone bundle, embedded config, captures beside .app and GUI passed")
        # A non-writable destination must fail visibly instead of silently
        # redirecting the user's captures to another location.
        shutil.rmtree(capture_root)
        isolated.chmod(0o555)
        exe = app / "Contents/MacOS/DualHolo"
        blocked = subprocess.run([str(exe), "--simulate", "--headless", "--seconds", "0.1"],
                                 cwd="/", capture_output=True, text=True, timeout=10)
        assert blocked.returncode == 1, blocked.stdout + blocked.stderr
        assert "Cannot create capture folder:" in blocked.stderr and str(capture_root) in blocked.stderr, blocked.stderr
        assert "Move DualHolo.app with Finder to a writable folder" in blocked.stderr, blocked.stderr
        assert not capture_root.exists(), capture_root
        # Explicit --output still lets an app in a read-only folder run.
        override = root / "Explicit Output"
        redirected = subprocess.run([str(exe), "--simulate", "--headless", "--seconds", "0.1",
                                     "--output", str(override)],
                                    cwd="/", capture_output=True, text=True, timeout=10)
        assert redirected.returncode == 0, redirected.stdout + redirected.stderr
        assert len(list(override.glob("session_*/run_summary.yml"))) == 1
        assert not list(override.rglob("*.tiff")), "Explicit output must start with REC OFF"
        print("Unwritable app folder: actionable error, no alternate default, explicit --output works")
    finally:
        isolated.chmod(0o755)
        for p, mode in reversed(list(modes.items())):
            p.chmod(mode)
