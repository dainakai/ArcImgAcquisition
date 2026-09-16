"""Reject a DualHolo tag or an analyzer tag with a mismatched package version."""
import os
from holoanalyze import __version__

expected = "analyzer-v" + __version__
actual = os.environ["RELEASE_TAG"]
if actual != expected:
    raise SystemExit(f"Expected {expected}, got {actual}; DualHolo keeps its independent v* tags")
print(f"Verified independent release {actual}")
