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

# Without this, the cache above does nothing at all.
#
# Terraform will not install a provider from the shared cache unless the dependency lock file
# already records that provider's official checksums, because a cache entry alone cannot prove
# authenticity. Every test here inits a fresh tmp_path with no .terraform.lock.hcl, so that
# condition is never met: Terraform said "Installed hashicorp/aws v6.62.0 (signed by
# HashiCorp)" -- the registry download path -- and re-downloaded the ~490 MB package every
# single time, while the populated cache sat unused.
#
# Measured 2026-09-01 on one pinned-version init with a warm cache, everything else identical:
#
#     without this variable   97-293s   "Installed ... (signed by HashiCorp)"
#     with it                   10.3s   "Using ... from the shared cache directory"
#
# The variance is download throughput; this connection measured 4.6 MB/s. Local file I/O was
# never the cost -- copying the same 863 MB binary takes 2.1s and hashing it 3.7s.
#
# What the name warns about does not apply here. The risk is that Terraform records a lock
# entry covering only the current platform, instead of the registry's checksums for every
# platform. These lock files live in a tmp_path that is deleted when the test ends and are never
# committed. Provider VERSION resolution still goes to the registry ("Finding hashicorp/aws
# versions matching ..."), so a newly released provider is still discovered and still
# downloaded -- schema_lint.py's live-schema contract is unaffected. Only the redundant
# re-download of a version already sitting in the cache is skipped.
#
# Scoped to the test suite. Nothing an operator runs through minusctl reads this file.
os.environ.setdefault("TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE", "1")


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


# One file must never become two modules.
#
# core/ is on sys.path and so is each of its subpackages, so almost every module here can be
# reached by more than one name -- `plan_gate`, `core.governance.plan_gate`, and (because
# core/governance is on the path too) anything else that resolves. Each name Python has not
# seen before builds a SEPARATE module object holding its own copy of every module-level name.
#
# For a test suite that is a correctness problem, not tidiness. monkeypatch.setattr targets one
# object; if the code under test imported the other, the patch silently does nothing and the
# test exercises the real implementation while reporting a pass. That is not theoretical here:
# test_aws_telemetry_returns_none_without_credentials patched bare `aws` while cloud_drift
# reached providers.aws, and the unpatched run_aws returned live AWS data
# ({'identity': 'resource-explorer-2'}) instead of the refusal the test asserts.
#
# A whole-session check rather than a per-test one, because import identity is global and
# order-dependent: the duplicate is usually created by a different file than the one that
# suffers from it. tests/test_module_identity.py covers the product's own entry points in
# clean subprocesses; this covers the suite. There were 22 when this was written.
#
# The rule: import a module the way production imports it. Engines flat (`import reporter`),
# the CLI through its package (`from core.cli import main`), since that is what the console
# script in pyproject.toml loads.
def _duplicated_core_modules():
    import collections

    by_file = collections.defaultdict(list)
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        path = os.path.abspath(path)
        if path.startswith(os.path.join(ROOT, "core")) or path.startswith(os.path.join(ROOT, "app")):
            by_file[path].append(name)
    return {os.path.relpath(f, ROOT): sorted(n) for f, n in by_file.items() if len(n) > 1}


def pytest_sessionfinish(session, exitstatus):
    duplicates = _duplicated_core_modules()
    if not duplicates:
        return
    lines = ["", "One source file was imported under more than one module name:"]
    for path, names in sorted(duplicates.items()):
        lines.append(f"  {path}")
        lines.append(f"      {names}")
    lines += [
        "",
        "Each name is a separate module object with its own module-level state, so a",
        "monkeypatch against one is invisible to code that imported the other -- a test can",
        "pass while exercising the real implementation. Import it the way production does:",
        "engines flat (`import reporter`), the CLI through its package",
        "(`from core.cli import main`). See tests/test_module_identity.py.",
        "",
    ]
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("\n".join(lines), red=True)
    else:
        print("\n".join(lines))
    if session.exitstatus == 0:
        session.exitstatus = 1


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
