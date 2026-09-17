#!/usr/bin/env python3
"""Build a native-OS standalone distribution and smoke-test the frozen GUI."""
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from holoanalyze import __version__


def smoke_test(executable, cwd, config, env):
    for flags in ([], ["--simulate"], ["--simulate-native", "--config", str(config)]):
        start = time.monotonic()
        subprocess.run([str(executable), *flags, "--quit-after", "3"], env=env, cwd=cwd,
                       check=True, timeout=60)
        if time.monotonic()-start < 2.8:
            raise RuntimeError("Frozen GUI exited before completing its smoke test")


def prepare_macos_bundle(bundle, executable, bridge_name):
    import plistlib
    # PyInstaller removes external LC_RPATH entries. The SDK is loaded only
    # after Connect, but its GenICam dependencies still require these paths.
    bridge = bundle / "Contents" / "Frameworks" / "native" / bridge_name
    for binary in (executable, bridge):
        load_commands = subprocess.check_output(["otool", "-l", str(binary)], text=True)
        for path in ("/Applications/Spinnaker/lib", "/usr/local/lib"):
            if f"path {path} (offset" not in load_commands:
                subprocess.run(["install_name_tool", "-add_rpath", path, str(binary)], check=True)
    info = bundle / "Contents" / "Info.plist"
    with info.open("rb") as stream:
        metadata = plistlib.load(stream)
    metadata.update(CFBundleShortVersionString=__version__, CFBundleVersion=__version__,
                    CFBundleDisplayName="DualHolo Analyze")
    with info.open("wb") as stream:
        plistlib.dump(metadata, stream)
    # Restore the ad-hoc signatures invalidated by the load-command changes.
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(bundle)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)


def write_launchers(release):
    if os.name == "nt":
        for filename, flags in (("Run.cmd", ""), ("Simulate.cmd", "--simulate ")):
            (release / filename).write_text(
                '@echo off\n"%~dp0DualHoloAnalyze.exe" ' + flags + '%*\nif errorlevel 1 pause\n', encoding="utf-8")
    else:
        name = "Simulate.command" if sys.platform == "darwin" else "run.sh"
        executable = "DualHoloAnalyze.app/Contents/MacOS/DualHoloAnalyze" if sys.platform == "darwin" else "DualHoloAnalyze"
        flags = "--simulate " if sys.platform == "darwin" else ""
        path = release / name
        path.write_text('#!/bin/sh\nset -eu\nAPP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                        f'exec "$APP_DIR/{executable}" {flags}"$@"\n', encoding="utf-8")
        path.chmod(0o755)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", type=Path, required=True, help="holo_capture shared library from a bundled-OpenCV build")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "dist")
    parser.add_argument("--work", type=Path, default=Path(__file__).parent / "build")
    parser.add_argument("--platform", default=f"{sys.platform}-{platform.machine()}")
    parser.add_argument("--native-licenses", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if not args.bridge.is_file():
        parser.error("Build holo_capture first and pass the exact shared library path")
    output, work = args.output.resolve(), args.work.resolve()
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    name = "DualHoloAnalyze"
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--name", name,
               "--distpath", str(output), "--workpath", str(work / "pyinstaller"), "--specpath", str(work),
               "--paths", str(root.parent / "registration"),
               "--add-data", f"{root / 'config.yaml'}{os.pathsep}.",
               "--add-data", f"{root / 'README.md'}{os.pathsep}.",
               "--add-binary", f"{args.bridge.resolve()}{os.pathsep}native",
               "--exclude-module", "torch",
               str(root / "run.py")]
    if sys.platform == "darwin":
        command[3:3] = ["--osx-bundle-identifier", "org.dainakai.DualHoloAnalyze"]
    if os.name == "nt":
        version = tuple(int(n) for n in __version__.split("-")[0].split(".")) + (0,)
        version_file = work / "windows-version.txt"
        version_file.write_text(
            "VSVersionInfo(ffi=FixedFileInfo(filevers=" + repr(version) + ", prodvers=" + repr(version) +
            ", mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)), "
            "kids=[StringFileInfo([StringTable('040904B0', ["
            "StringStruct('FileDescription', 'DualHolo Analyze'), StringStruct('ProductName', 'DualHolo Analyze'), "
            "StringStruct('OriginalFilename', 'DualHoloAnalyze.exe'), "
            f"StringStruct('FileVersion', '{__version__}'), StringStruct('ProductVersion', '{__version__}')"
            "])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])", encoding="utf-8")
        command[3:3] = ["--version-file", str(version_file)]
    build_env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(work / "cache"))
    subprocess.run(command, check=True, cwd=root, env=build_env)
    bundle = output / (name + ".app" if sys.platform == "darwin" else name)
    executable = bundle / "Contents" / "MacOS" / name if sys.platform == "darwin" else bundle / (name + (".exe" if os.name == "nt" else ""))
    if sys.platform == "darwin":
        prepare_macos_bundle(bundle, executable, args.bridge.name)
    # Execute the frozen app with its embedded configuration, without a camera,
    # and with an empty working directory so missing packaged imports are caught.
    empty = work / "smoke-cwd"
    empty.mkdir(exist_ok=True)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    # Never accidentally validate a build-tree library instead of the bundled one.
    env.pop("HOLO_CAPTURE_LIBRARY", None)
    env.pop("PYTHONPATH", None)
    import yaml
    smoke_config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    smoke_config["output_dir"] = str(work / "native-smoke-captures")
    smoke_yaml = work / "native-smoke.yaml"
    smoke_yaml.write_text(yaml.safe_dump(smoke_config), encoding="utf-8")
    smoke_test(executable, empty, smoke_yaml, env)
    release = output / f"{name}-{__version__}-{args.platform}"
    release.mkdir(exist_ok=False)
    if sys.platform == "darwin":
        # Preserve framework symlinks and bundle layout.
        shutil.copytree(bundle, release / bundle.name, symlinks=True)
    else:
        for item in bundle.iterdir():
            target = release / item.name
            if item.is_dir():
                shutil.copytree(item, target, symlinks=True)
            else:
                shutil.copy2(item, target)
    shutil.copy2(root / "config.yaml", release)
    shutil.copy2(root / "README.md", release)
    shutil.copy2(root / "release-notes.md", release)
    write_launchers(release)
    licenses = release / "licenses"
    licenses.mkdir()
    installed = {}
    for dependency in ("numpy", "scipy", "opencv-python-headless", "PySide6", "PySide6-Essentials", "PySide6-Addons", "shiboken6", "PyYAML", "tifffile", "matplotlib", "contourpy", "cycler", "fonttools", "kiwisolver", "packaging", "pillow", "pyparsing", "python-dateutil", "six"):
        dist = metadata.distribution(dependency)
        installed[dependency] = dist.version
        for source in dist.files or []:
            # Retain license files with paths, including Qt's bundled third parties.
            if any(term in source.name.lower() for term in ("license", "copying", "copyright")):
                source_path = Path(dist.locate_file(source))
                if source_path.is_file():
                    target = licenses / dependency / Path(*[p for p in source.parts if p not in ("..", ".")])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_path, target)
    if args.native_licenses:
        shutil.copytree(args.native_licenses, licenses / "native", dirs_exist_ok=True)
    (release / "dependencies.json").write_text(json.dumps(installed, indent=2), encoding="utf-8")
    if sys.platform == "darwin":
        archive = output / (release.name + ".zip")
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(release), str(archive)], check=True)
    else:
        archive = Path(shutil.make_archive(str(release), "zip" if os.name == "nt" else "gztar", root_dir=output, base_dir=release.name))
    # Verify exactly what users download, including archive layout and native DLLs.
    extracted = work / "extracted"
    extracted.mkdir(exist_ok=False)
    if sys.platform == "darwin":
        subprocess.run(["ditto", "-x", "-k", str(archive), str(extracted)], check=True)
        extracted_executable = extracted / release.name / (name+".app") / "Contents" / "MacOS" / name
    else:
        shutil.unpack_archive(archive, extracted)
        extracted_executable = extracted / release.name / (name+(".exe" if os.name == "nt" else ""))
    smoke_test(extracted_executable, empty, smoke_yaml, env)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name+".sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8", newline="\n")
    print(archive)


if __name__ == "__main__":
    main()
