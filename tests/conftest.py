"""Make the `core/` package importable from the tests without installing anything."""
import os
import sys

import pytest

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
