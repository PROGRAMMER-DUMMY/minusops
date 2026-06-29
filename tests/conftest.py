"""Make the `core/` package importable from the tests without installing anything."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "core")
APP = os.path.join(ROOT, "app")
for path in (CORE, APP):
    if path not in sys.path:
        sys.path.insert(0, path)
