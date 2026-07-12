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

# Every real-terraform test (test_databricks_workspace_module.py, test_schema_lint.py, etc.)
# does its own `terraform init` in a fresh tmp_path, and without a shared plugin cache each one
# re-downloads the same provider binary from scratch. Across a session's worth of runs this
# genuinely fills a disk -- confirmed directly: pytest's own tmp dir alone grew to 65GB from
# provider re-downloads before this fix, and the resulting "No space left on device" crashed an
# unrelated full-suite run outright. setdefault, not a hard override -- respects an operator's
# or CI's own TF_PLUGIN_CACHE_DIR if one is already set. Safe to share across the whole
# session/machine: this cache holds only regenerable provider binaries, never plan output or
# test state (the same reasoning schema_watch.py and module_provenance.py's schema_hash already
# rely on for their own live-fetch work).
os.environ.setdefault("TF_PLUGIN_CACHE_DIR", os.path.join(ROOT, ".agents", "tf-plugin-cache"))
os.makedirs(os.environ["TF_PLUGIN_CACHE_DIR"], exist_ok=True)
