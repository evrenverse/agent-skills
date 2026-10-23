"""Test package. Puts lib/ on sys.path the way the bin/ launchers do, so tests import `paseo_tools`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
