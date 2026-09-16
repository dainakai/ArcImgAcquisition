"""Resolve the installed Windows C API name using a fake SDK, without cameras."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

assert sys.platform == "win32"
inspector, fake = (Path(arg).resolve() for arg in sys.argv[1:])
with tempfile.TemporaryDirectory(prefix="dual_holo_sdk_") as tmp:
    root = Path(tmp)
    program_files = root / "Program Files"
    sdk = program_files / "Teledyne/Spinnaker/bin64/vs2015"
    sdk.mkdir(parents=True)
    library = sdk / "SpinnakerC_v140.dll"
    shutil.copy2(fake, library)
    # os.environ normalizes Windows variable names to uppercase. Avoid two
    # case variants in the child environment block pointing at different SDKs.
    env = dict(os.environ, PROGRAMFILES=str(program_files))
    env.pop("SPINNAKER_C_LIBRARY", None)

    def inspect(environment):
        return subprocess.run([str(inspector), "--runtime-only"], cwd=root, env=environment,
                              capture_output=True, text=True, timeout=10)

    def assert_loaded(result, expected):
        assert result.returncode == 0, result.stdout + result.stderr
        # std::filesystem may preserve '/' inside SDK path components.
        assert Path(result.stdout.removeprefix("Spinnaker C runtime: ").strip()) == expected, result.stdout

    # A bogus old spelling proves the actual SDK name wins discovery.
    legacy = sdk / "Spinnaker_C_v140.dll"
    legacy.write_bytes(b"not a DLL")
    found = inspect(env)
    assert_loaded(found, library)

    override = root / "Override DLL.dll"
    shutil.copy2(fake, override)
    explicit = inspect(dict(env, SPINNAKER_C_LIBRARY=str(override)))
    assert_loaded(explicit, override)

    missing = root / "missing.dll"
    failed = inspect(dict(env, SPINNAKER_C_LIBRARY=str(missing)))
    assert failed.returncode == 1 and str(missing) in failed.stderr, failed.stdout + failed.stderr
    assert str(library) not in failed.stderr, "Explicit DLL override must not fall back to other SDKs"

    library.unlink()
    shutil.copy2(fake, legacy)
    compatible = inspect(env)
    assert_loaded(compatible, legacy)
    print("Windows SDK discovery: Teledyne SpinnakerC_v140.dll, explicit override and legacy fallback passed")
