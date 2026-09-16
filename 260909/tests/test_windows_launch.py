"""Exercise Explorer-style failures and the shipped Windows launchers, camera-free."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

assert sys.platform == "win32"
exe = Path(sys.argv[1]).resolve()
user32 = ctypes.WinDLL("user32", use_last_error=True)
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def window_text(hwnd):
    text = ctypes.create_unicode_buffer(16384)
    user32.GetWindowTextW(hwnd, text, len(text))
    return text.value


def windows(title, pid=None):
    found = []

    @callback_type
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if (pid is None or owner.value == pid) and user32.IsWindowVisible(hwnd) and window_text(hwnd) == title:
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found


def wait_for_window(title, process, pid=None):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        assert process.poll() is None, f"Exited before showing {title}"
        found = windows(title, pid)
        if found:
            return found[0]
        time.sleep(0.025)
    raise AssertionError(f"No visible {title} window")


def pictures_directory():
    shell32, ole32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("ole32")
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                                           ctypes.POINTER(wintypes.LPWSTR)]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    guid = ctypes.create_string_buffer(uuid.UUID("33e28130-4e1e-4676-835a-98395c3bc3bb").bytes_le)
    path = wintypes.LPWSTR()
    assert shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(path)) == 0
    try:
        return Path(path.value)
    finally:
        ole32.CoTaskMemFree(ctypes.cast(path, ctypes.c_void_p))


capture_root = pictures_directory() / "DualHolo/captures"
existing = set(capture_root.glob("session_*"))


def session_from(stdout):
    match = re.search(r'^Session: (".+")$', stdout, re.M)
    assert match, stdout
    session = Path(json.loads(match[1]))
    assert session.parent == capture_root and session not in existing, session
    assert not list(session.rglob("*.tiff")), "Startup must not save images"
    return session


with tempfile.TemporaryDirectory(prefix="dual_holo_windows_") as tmp:
    root = Path(tmp)
    # This explicit missing library prevents any access to actual cameras.
    env = dict(os.environ, SPINNAKER_C_LIBRARY=str(root / "missing-sdk.dll"))

    for case, expected in (("missing-runtime", "Spinnaker C runtime not found"),
                           ("bad-config", "Invalid configuration")):
        cwd = root / case
        cwd.mkdir()
        if case == "bad-config":
            (cwd / "config.yml").write_text("%YAML:1.0\nexpected_hz: 0.0\n")
        with (root / (case + ".log")).open("w+") as log:
            process = subprocess.Popen([str(exe)], cwd=cwd, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_CONSOLE)
            try:
                dialog = wait_for_window("DualHolo - Startup error", process, process.pid)
                texts = []

                @callback_type
                def child(hwnd, _):
                    texts.append(window_text(hwnd))
                    return True

                user32.EnumChildWindows(dialog, child, 0)
                message = "\n".join(texts)
                assert expected in message, message
                error_file = Path(re.search(r"Details saved to:\s*([^\r\n]+)", message)[1])
                assert error_file.name.startswith(f"startup-error-{process.pid}-"), error_file
                saved_error = error_file.read_text(encoding="utf-8-sig")
                assert expected in saved_error and "Executable:" in saved_error, saved_error
                if case == "missing-runtime":
                    assert "missing-sdk.dll" in saved_error and "Windows error" in saved_error, saved_error
                time.sleep(0.5)
                assert process.poll() is None and user32.IsWindowVisible(dialog), "Error vanished before acknowledgement"
                # Dismiss through the title-bar close action. A synthetic
                # WM_COMMAND without a real button notification can be ignored
                # by the native MessageBox implementation on Windows Server.
                assert user32.PostMessageW(dialog, 0x0010, 0, 0), ctypes.get_last_error()  # WM_CLOSE
                assert process.wait(timeout=5) == 1
                log.seek(0)
                stdout = log.read()
                if case == "missing-runtime":
                    shutil.rmtree(session_from(stdout))
                error_file.unlink()
                print(f"Explorer-style {case}: visible persistent error, complete log, nonzero exit verified")
            except Exception:
                log.seek(0)
                print(log.read(), flush=True)
                print("Remaining error windows:", windows("DualHolo - Startup error", process.pid), flush=True)
                raise
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

    for name, arguments in (("Simulate.cmd", "--seconds 2"), ("Run.cmd", "--simulate --seconds 2")):
        launcher = exe.parent / name
        assert launcher.is_file(), launcher
        assert not windows("Dual Hologram Viewer"), "Another GUI is already running"
        with (root / (name + ".log")).open("w+") as log:
            # CALL lets cmd.exe handle the quoted batch path, including spaces.
            process = subprocess.Popen(f'call "{launcher}" {arguments}', shell=True, cwd=root, env=env,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            try:
                wait_for_window("Dual Hologram Viewer", process)
                assert process.wait(timeout=15) == 0
                log.seek(0)
                session = session_from(log.read())
                stats = dict(re.findall(r"^(\w+):\s*([^\n]*)", (session / "run_summary.yml").read_text(), re.M))
                assert stats["exit_reason"] == "duration" and float(stats["run_seconds"]) >= 2, stats
                assert int(float(stats["gui_draws"])) >= 2, stats
                shutil.rmtree(session)
                print(f"{name}: visible GUI, default user capture folder, REC OFF and clean exit verified")
            finally:
                if process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)

    failed = subprocess.run(f'call "{exe.parent / "Run.cmd"}" --headless --seconds 0.1', shell=True,
                            cwd=root, env=env, input="\n", capture_output=True, text=True, timeout=15)
    assert failed.returncode == 1 and "Spinnaker C runtime not found" in failed.stdout + failed.stderr, failed.stdout + failed.stderr
    shutil.rmtree(session_from(failed.stdout))
    print("Run.cmd retains failure output and preserves the failure exit code")
