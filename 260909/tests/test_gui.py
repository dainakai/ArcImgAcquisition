"""Catch immediate successful exits and exercise the actual GTK window on X11.

All acquisition uses --simulate. Linux: xvfb-run -a python test_gui.py EXE --x11
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

parser = argparse.ArgumentParser()
parser.add_argument("exe", type=Path)
parser.add_argument("--x11", action="store_true")
args = parser.parse_args()
exe = args.exe.resolve()


def summary(output):
    sessions = list(output.glob("session_*"))
    assert len(sessions) == 1, sessions
    text = (sessions[0] / "run_summary.yml").read_text()
    return sessions[0], dict(re.findall(r"^(\w+):\s*([^\n]*)", text, re.M))


def command(*argv):
    return subprocess.run(argv, check=True, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=5).stdout.strip()


def wait_until(predicate, process=None, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None:
            assert process.poll() is None, "GUI exited before the requested action"
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("GUI condition timed out")


def visible_window():
    found = subprocess.run(["xdotool", "search", "--onlyvisible", "--name",
                            "^Dual Hologram Viewer$"], text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    return found.stdout.strip().splitlines()[0] if found.returncode == 0 else None


with tempfile.TemporaryDirectory(prefix="dual_holo_gui_") as tmp:
    root = Path(tmp)
    env = dict(os.environ, SPINNAKER_C_LIBRARY=str(root / "no-sdk"))
    manager = None
    try:
        if args.x11:
            manager = subprocess.Popen(["openbox", "--sm-disable"], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)

            def manager_ready():
                return subprocess.run(["wmctrl", "-m"], stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, timeout=5).returncode == 0

            wait_until(manager_ready, manager)

        # An exit code of zero alone is insufficient: rc.1 stopped immediately
        # because GTK reports WND_PROP_VISIBLE=-1 for an existing window.
        output = root / "duration"
        began = time.monotonic()
        result = subprocess.run([str(exe), "--simulate", "--seconds", "2",
                                 "--output", str(output)], cwd=root, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        elapsed = time.monotonic() - began
        assert result.returncode == 0, result.stdout
        assert elapsed >= 1.95, f"GUI exited too early ({elapsed:.3f}s):\n{result.stdout}"
        session, stats = summary(output)
        assert stats["exit_reason"] == "duration", result.stdout
        assert float(stats["run_seconds"]) >= 2, stats
        assert int(float(stats["gui_draws"])) >= 2, stats
        assert all(int(float(stats[f"received_cam{i}"])) >= 2 for i in range(2)), stats
        assert not list(session.rglob("*.tiff")), "REC must start OFF"
        print(f"GUI remained active for {stats['run_seconds']} seconds; repeated drawing verified")

        if args.x11:
            # Use a real window manager's close request (the title-bar X), with
            # recording active. This must finish queued writes and never reopen.
            output = root / "close"
            with (root / "close.log").open("w+") as log:
                process = subprocess.Popen([str(exe), "--simulate", "--seconds", "20",
                                            "--output", str(output)], cwd=root, env=env,
                                           stdout=log, stderr=subprocess.STDOUT, text=True)
                try:
                    window = wait_until(visible_window, process)
                    geometry = command("xdotool", "getwindowgeometry", "--shell", window)
                    assert int(re.search(r"^WIDTH=(\d+)", geometry, re.M)[1]) > 0, geometry
                    assert int(re.search(r"^HEIGHT=(\d+)", geometry, re.M)[1]) > 0, geometry
                    time.sleep(0.5)
                    assert process.poll() is None, "GUI disappeared after becoming visible"
                    assert not list(output.rglob("*.tiff")), "GUI saved images before REC"
                    command("xdotool", "windowactivate", "--sync", window)
                    command("xdotool", "key", "--clearmodifiers", "r")
                    wait_until(lambda: len(list(output.rglob("*.tiff"))) >= 4, process)
                    command("wmctrl", "-ic", hex(int(window)))
                    assert process.wait(timeout=8) == 0
                    session, stats = summary(output)
                    assert stats["exit_reason"] == "window_closed", stats
                    assert float(stats["run_seconds"]) < 20, stats
                    assert int(float(stats["recordings"])) == 1, stats
                    for cam in range(2):
                        saved = int(float(stats[f"saved_cam{cam}"]))
                        assert saved > 0 and saved == int(float(stats[f"accepted_cam{cam}"])), stats
                    assert int(float(stats["pending_frames"])) == 0, stats
                    assert not list(session.rglob("IN_PROGRESS")), "Close did not drain the recording"
                    print("GTK window mapped; R started REC; title-bar close drained every admitted frame")
                except Exception:
                    log.flush()
                    log.seek(0)
                    print(log.read())
                    raise
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
    finally:
        if manager is not None:
            manager.terminate()
            manager.wait(timeout=5)
