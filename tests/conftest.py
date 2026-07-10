"""Make the `core/` package importable from the tests without installing anything."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "core")
APP = os.path.join(ROOT, "app")
CORE_SUBPACKAGES = ("generation", "architecture", "governance", "cost", "reporting", "providers")
for path in (CORE, APP, *(os.path.join(CORE, sub) for sub in CORE_SUBPACKAGES)):
    if path not in sys.path:
        sys.path.insert(0, path)

# Tests must never reach AWS: the reporter auto-creates BCM estimates when ambient
# credentials exist, so disable that path for the whole suite (tests that exercise it
# re-enable and mock explicitly).
os.environ["MINUS_BCM_AUTO"] = "0"
