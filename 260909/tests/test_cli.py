"""Exercise the packaged executable without opening cameras."""
import csv
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

exe = str(Path(sys.argv[1]).resolve())
with tempfile.TemporaryDirectory(prefix="dual_holo_cli_") as tmp:
    root = Path(tmp)
    env = dict(os.environ, SPINNAKER_C_LIBRARY=str(root / "missing-spinnaker-library"))

    def run(*args, success=True):
        result = subprocess.run([exe, *args], cwd=root, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        assert (result.returncode == 0) == success, result.stdout
        return result.stdout

    def summary(path):
        sessions = list(path.glob("session_*"))
        assert len(sessions) == 1, sessions
        return sessions[0], (sessions[0] / "run_summary.yml").read_text()

    def counter(text, key):
        return int(float(re.search(rf"^{key}:\s*(\S+)", text, re.M)[1]))

    run("--help")
    # Explicit settings keep output relative to the settings file on every OS,
    # including macOS after changing Finder's default capture directory.
    settings = root / "settings with spaces" / "custom.yml"
    settings.parent.mkdir()
    settings.write_text('%YAML:1.0\noutput_dir: "relative captures"\n')
    run("--config", str(settings), "--simulate", "--headless", "--seconds", "0.2")
    session, text = summary(settings.parent / "relative captures")
    assert counter(text, "recordings") == 0 and not list(session.rglob("*.tiff"))
    run("--config", str(settings), "--simulate", "--headless", "--seconds", "0.2",
        "--output", str(root / "override"))
    summary(root / "override")
    run("--headless", "--record-at", "0", "--seconds", "0.2", success=False)
    run("--simulate", "--headless", "--seconds", "0.5", "--output", str(root / "idle"))
    session, text = summary(root / "idle")
    assert not list(session.rglob("*.tiff")) and counter(text, "recordings") == 0
    run("--simulate", "--headless", "--seconds", "1.4", "--record-at", "0.2",
        "--record-for", "0.7", "--output", str(root / "rec"))
    session, text = summary(root / "rec")
    clips = list(session.glob("recording_*"))
    assert len(clips) == 1 and not list(session.rglob("*.partial.tiff"))
    assert not (clips[0] / "IN_PROGRESS").exists()
    with (clips[0] / "frames.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    metadata = dict(line.split("=", 1) for line in (clips[0] / "summary.txt").read_text().splitlines())
    for cam in range(2):
        n = counter(text, f"saved_cam{cam}")
        assert n > 0 and n == counter(text, f"accepted_cam{cam}")
        assert n == len([row for row in rows if int(row["camera"]) == cam])
    for row in rows:
        assert int(metadata["started_steady_ns"]) <= int(row["record_admitted_ns"]) <= int(metadata["stopped_steady_ns"])
        assert (clips[0] / row["file"]).is_file()
    assert len(rows) == len(list(session.rglob("*.tiff")))
    assert counter(text, "pending_frames") == 0
    failed = run("--headless", "--seconds", "0.1", "--output", str(root / "missing"), success=False)
    assert "Spinnaker C runtime not found" in failed
print("CLI simulation, recording boundaries, manifests and missing-runtime handling passed")
