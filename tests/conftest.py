"""Make the `core/` package importable from the tests without installing anything."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "core")
APP = os.path.join(ROOT, "app")
CORE_SUBPACKAGES = ("generation", "architecture", "governance", "cost", "reporting",
                    "providers", "integrations")
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


# The plugin cache above fixed re-downloading. It does not fix this, which is a separate leak
# in the same direction and was the one actually filling the disk.
#
# `terraform init` unpacks each provider through a loose file in the system temp directory and
# removes it on the way out. A run that is killed or dies mid-init never gets to the way out,
# and leaves a 185 MB file behind with a randomised name. They do not expire and nothing else
# collects them. Measured on this machine 2026-09-01: 793 files, 62.8 GB, the oldest 6.9 days
# old -- against 2.4 GB of free space at the time.
#
# That is worth a guard rather than a note, because of what the symptom looks like. A test that
# cannot write a provider binary fails with whatever assertion came next, not with a disk error,
# so the failure reads as a code defect. This session lost real time to exactly that: a security
# regression was reported against modules/security-iam-scoped that turned out to be a full disk,
# and docs/PROGRESS.md records the same class of thing on 2026-08-18. Wrong answers that look
# like findings are worse than no answers.
#
# One hour, not zero: a live `terraform init` in a concurrent session has a temp file seconds
# old, and reaping that would break a run this suite does not own. Every failure is swallowed --
# an orphan another process still holds open on Windows is next session's problem, never a
# reason to fail collection.
_PROVIDER_TEMP_ORPHAN_AGE_SECONDS = 3600


def _reap_provider_temp_orphans():
    """Delete stale `terraform-provider*` files from the system temp directory."""
    import glob
    import tempfile
    import time

    cutoff = time.time() - _PROVIDER_TEMP_ORPHAN_AGE_SECONDS
    reclaimed = 0
    for path in glob.glob(os.path.join(tempfile.gettempdir(), "terraform-provider*")):
        try:
            if not os.path.isfile(path) or os.path.getmtime(path) > cutoff:
                continue
            size = os.path.getsize(path)
            os.remove(path)
            reclaimed += size
        except OSError:
            continue
    return reclaimed


def pytest_configure(config):
    config._minus_reclaimed_bytes = _reap_provider_temp_orphans()


def pytest_report_header(config):
    reclaimed = getattr(config, "_minus_reclaimed_bytes", 0)
    if not reclaimed:
        return None
    return f"reaped {reclaimed / (1024 ** 3):.1f} GB of orphaned terraform provider temp files"


# MINUS-140. `record_claim()` exports the whole claim corpus to JSONL on every insert, and
# `_claims_corpus_dir()` resolves to the repo's own tracked `knowledge/claims/`. Any test that
# records a claim therefore rewrote committed files -- `ingested_at`/`observed_at` churn on
# rows whose content_hash never changed -- so `git status` went dirty after every pytest run.
#
# Redirecting the corpus is the fix rather than freezing the clock: a frozen clock only makes
# the diff empty, leaving the tests still writing to tracked files, so the next field that is
# not a timestamp reopens the same hole. Nothing here should be touching the real corpus at all.
#
# Not MINUSOPS_OUTPUT_DIR, which would reach this and every other generated artifact -- and
# test_modules.py asserts on output_root()'s own fallback chain, which a globally-set override
# would defeat.
@pytest.fixture(autouse=True)
def _isolate_claim_corpus(tmp_path_factory, monkeypatch):
    # tmp_path_factory, not tmp_path: a directory created inside the test's own tmp_path is
    # visible to it, and test_finops_agent.py asserts its tmp_path stays empty.
    corpus = tmp_path_factory.mktemp("claims-corpus", numbered=False)         if not hasattr(_isolate_claim_corpus, "_dir") else _isolate_claim_corpus._dir
    _isolate_claim_corpus._dir = corpus
    try:
        import synthesizer
    except ImportError:  # a test run that never imports the generator at all
        return

    # Guarded, not blanket: test_claim_writeback.py legitimately points MINUSOPS_OUTPUT_DIR at
    # its own tmp_path and then asserts the corpus landed there. Replacing the resolver
    # outright defeated that setup, so only a path that resolves INTO the repo's tracked
    # corpus is diverted -- a test that already redirected keeps whatever it chose.
    real = synthesizer._claims_corpus_dir
    tracked = os.path.abspath(os.path.join(ROOT, "knowledge", "claims"))

    def _guarded():
        resolved = os.path.abspath(real())
        return str(corpus) if resolved == tracked else resolved

    monkeypatch.setattr(synthesizer, "_claims_corpus_dir", _guarded)


@pytest.fixture(autouse=True)
def _clear_alert_dedup_window():
    """Empty base_hook's alert cooldown between tests.

    The window is process-global by design -- one running control plane, one cooldown -- but
    that makes it shared state across tests, where one test's alert would suppress an
    identical alert in the next. Isolation belongs here rather than as a reset() helper in
    production code that exists only for tests.
    """
    try:
        import base_hook
    except ImportError:
        yield
        return
    base_hook._recent_sends.clear()
    yield
    base_hook._recent_sends.clear()
