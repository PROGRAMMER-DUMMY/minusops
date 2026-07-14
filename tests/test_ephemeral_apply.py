"""
ephemeral_apply.py is G9 (docs/phase5_scope.md, Phase 5) -- ephemeral create+destroy against a
real emulator (LocalStack/MiniStack/Floci), catching apply-time-only failures static analysis
(G1/G2/G6) cannot.

Two kinds of proof here. Most fail-closed paths are proven with mocked subprocess calls -- fast,
and legitimate for verifying THIS module's own control flow (does a given terraform exit
code/output shape route to the right verdict), reserving real, live-emulator proof for
proof-bar item 3 (a real CI run) rather than re-deriving it slowly here. A handful of tests are
real, not mocked, because they were the actual mechanism that caught real bugs before this
shipped: the real apply-against-an-unreachable-endpoint test (which surfaced the provider-
override-ordering bug fixed in this file -- the classification plan originally ran before the
override was written, meaning it wasn't isolated from ambient credentials at all), and the real
`terraform apply -json` event-shape tests (which locked in the exact `apply_start`/
`apply_complete`/`apply_errored` shape, including the genuinely-observed case of a crashed
provider plugin dumping a non-JSON stack trace into the output stream).

RESOURCE_TYPE_ALLOWLIST is per-(type, emulator) (docs/phase5_scope.md section 7). LocalStack's
column is unverified by design (a paid LOCALSTACK_AUTH_TOKEN account this session could not
provision). MiniStack and Floci needed no token, so BOTH were run through a real gauntlet this
session (real Docker containers in CI, real terraform apply/destroy) -- the real result for
security-critical types (aws_iam_role, aws_kms_key, aws_s3_bucket_policy) is `verified=True`
(positive fixtures apply cleanly) but `negative_fidelity_verified=False` on BOTH emulators (both
incorrectly accept a malformed config real AWS is documented to reject) -- see
`test_run_blocks_on_negative_fidelity_unverified_security_critical_type` below, which locks in
this exact, currently-blocking finding. Tests that need a fully "verified" type for an unrelated
check temporarily monkeypatch an entry via `ea._entry(...)`, never assuming the real allowlist
already claims coverage it hasn't earned.
"""
import json
import subprocess
import sys
import time
from unittest import mock

import pytest

import ephemeral_apply as ea
import toolpath

TERRAFORM = toolpath.find_tool("terraform")


def _rc(rtype, name, address=None):
    return {
        "address": address or f"{rtype}.{name}", "mode": "managed", "type": rtype, "name": name,
        "change": {"actions": ["create"], "after": {}, "after_unknown": {}},
    }


def _plan(resource_changes=()):
    return {"resource_changes": list(resource_changes)}


# ---------------------------------------------------------------------------
# Pure functions: coverage classification, allowlist, provider override, apply-json parsing
# ---------------------------------------------------------------------------

def test_classify_coverage_full_aws_only():
    coverage, db, aws = ea.classify_coverage(_plan([_rc("aws_s3_bucket", "b")]))
    assert coverage == "full"
    assert db == []
    assert aws == ["aws_s3_bucket.b"]


def test_classify_coverage_partial_mixed():
    coverage, db, aws = ea.classify_coverage(
        _plan([_rc("aws_s3_bucket", "b"), _rc("databricks_cluster", "c")]))
    assert coverage == "partial"
    assert db == ["databricks_cluster.c"]
    assert aws == ["aws_s3_bucket.b"]


def test_classify_coverage_none_databricks_only():
    coverage, db, aws = ea.classify_coverage(_plan([_rc("databricks_cluster", "c")]))
    assert coverage == "none"
    assert db == ["databricks_cluster.c"]
    assert aws == []


def test_classify_coverage_none_empty_plan():
    coverage, db, aws = ea.classify_coverage(_plan([]))
    assert coverage == "none"
    assert db == [] and aws == []


def test_classify_coverage_none_for_provider_neutral_test_utility_types():
    """Real bug caught while wiring G9 into the real flow (docs/
    phase6_step1_authoring_scope.md section 3), not hypothetical: `terraform_data` and
    `random_id` are neither aws_* nor databricks_* -- the `aws` bucket used to be defined as
    merely "not databricks", which swept these in as if they were real AWS content, misreporting
    "full" coverage for a plan G9 has nothing to actually verify. tests/test_gate_e2e.py's own
    real auto-approve fixture uses terraform_data specifically because it has zero cloud
    footprint; this must stay "none" for G9 to correctly skip it."""
    coverage, db, aws = ea.classify_coverage(_plan([_rc("terraform_data", "demo")]))
    assert coverage == "none"
    assert db == [] and aws == []

    coverage, db, aws = ea.classify_coverage(_plan([_rc("random_id", "probe")]))
    assert coverage == "none"
    assert db == [] and aws == []


def test_classify_coverage_full_ignores_a_mixed_in_test_utility_type():
    """The same fix, the other direction: a real AWS resource alongside a provider-neutral
    test-utility one still correctly reports "full" (the test-utility type is simply irrelevant
    to coverage, not something that dilutes or blocks it)."""
    coverage, db, aws = ea.classify_coverage(
        _plan([_rc("aws_s3_bucket", "b"), _rc("terraform_data", "demo")]))
    assert coverage == "full"
    assert db == []
    assert aws == ["aws_s3_bucket.b"]


def test_unverified_types_flags_every_type_by_design():
    """Every entry in the real allowlist is unverified=False right now -- this is not a bug in
    the test, it's the honest, disclosed state of the module."""
    unverified = ea.unverified_types_in_plan(_plan([_rc("aws_s3_bucket", "b")]), "localstack")
    assert unverified == {"aws_s3_bucket"}


def test_unverified_types_flags_unknown_type_too():
    unverified = ea.unverified_types_in_plan(_plan([_rc("aws_totally_new_type", "x")]), "localstack")
    assert unverified == {"aws_totally_new_type"}


def test_unverified_types_ignores_databricks():
    unverified = ea.unverified_types_in_plan(_plan([_rc("databricks_cluster", "c")]), "localstack")
    assert unverified == set()


def test_unverified_types_empty_when_type_marked_verified(monkeypatch):
    monkeypatch.setitem(ea.RESOURCE_TYPE_ALLOWLIST, "aws_s3_bucket", ea._entry(False, ministack=(True, True), floci=(True, True), localstack=(True, True)))
    unverified = ea.unverified_types_in_plan(_plan([_rc("aws_s3_bucket", "b")]), "localstack")
    assert unverified == set()


def test_provider_override_is_the_only_credential_path():
    override = ea._generate_provider_override("http://localhost:4566")
    assert 'access_key                  = "test"' in override
    assert 'secret_key                  = "test"' in override
    assert "skip_credentials_validation = true" in override
    # Every service this repo's modules actually use must be present -- an unlisted one would
    # silently fall through to real AWS (the documented tflocal/endpoints{} gap this design
    # exists to close).
    for svc in ea._ENDPOINT_SERVICES:
        assert f'{svc} = "http://localhost:4566"' in override


def test_parse_apply_json_stream_real_success_shape():
    """Locked in from a real `terraform apply -auto-approve -json` run against a local-only
    resource (hashicorp/random) -- not hand-guessed."""
    lines = [
        {"type": "version", "terraform": "1.15.7"},
        {"type": "planned_change", "change": {"resource": {"addr": "random_id.probe"}, "action": "create"}},
        {"type": "apply_start", "hook": {"resource": {"addr": "random_id.probe"}, "action": "create"}},
        {"type": "apply_complete", "hook": {"resource": {"addr": "random_id.probe"}, "action": "create",
                                             "id_key": "id", "id_value": "rZbXAA"}},
        {"type": "change_summary", "changes": {"add": 1, "change": 0, "import": 0, "remove": 0}},
    ]
    text = "\n".join(json.dumps(line) for line in lines)
    events, error = ea._parse_apply_json_stream(text)
    assert error is None
    assert len(events) == 5
    outcomes = ea._resource_outcomes(events)
    assert outcomes == {"random_id.probe": "complete"}


def test_parse_apply_json_stream_real_crash_shape_is_malformed():
    """Locked in from a real crashed-provider-plugin apply: a Go panic stack trace gets dumped
    directly into what's otherwise a pure JSON stream. This MUST block, not be silently
    ignored while treating whatever parsed as sufficient."""
    text = (
        '{"type": "apply_start", "hook": {"resource": {"addr": "random_id.bad"}, "action": "create"}}\n'
        '{"type": "apply_errored", "hook": {"resource": {"addr": "random_id.bad"}, "action": "create"}}\n'
        '{"@level":"error","@message":"Error: Plugin did not respond","type":"diagnostic"}\n'
        '\n'
        'Stack trace from the terraform-provider-random_v3.9.0_x5.exe plugin:\n'
        '\n'
        'panic: runtime error: makeslice: len out of range\n'
    )
    events, error = ea._parse_apply_json_stream(text)
    assert error is not None
    assert "non-JSON line" in error
    # The valid lines before the crash are still returned -- useful for diagnosis -- but the
    # error signal is what callers must act on, not the partial event list.
    assert len(events) == 3


def test_resource_outcomes_distinguishes_complete_from_errored():
    events = [
        {"type": "apply_complete", "hook": {"resource": {"addr": "aws_s3_bucket.good"}}},
        {"type": "apply_errored", "hook": {"resource": {"addr": "aws_iam_role.bad"}}},
        {"type": "apply_start", "hook": {"resource": {"addr": "aws_kms_key.pending"}}},  # no terminal event
    ]
    outcomes = ea._resource_outcomes(events)
    assert outcomes == {"aws_s3_bucket.good": "complete", "aws_iam_role.bad": "errored"}


# ---------------------------------------------------------------------------
# Fail-closed sweep (docs/phase5_scope.md section 4), mocked subprocess for speed
# ---------------------------------------------------------------------------

def test_run_blocks_when_terraform_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: None)
    result = ea.run_ephemeral_apply(str(tmp_path))
    assert result["evaluation_failed"] is True
    assert result["reason"] == "terraform_not_found"


def _mock_run_sequence(*results):
    """Return a side_effect function for subprocess.run that yields each result in turn."""
    it = iter(results)

    def _run(*args, **kwargs):
        result = next(it)
        if isinstance(result, Exception):
            raise result
        return result

    return _run


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_blocks_on_init_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    (tmp_path / "main.tf").write_text("", encoding="utf-8")
    with mock.patch.object(ea.subprocess, "run",
                            side_effect=_mock_run_sequence(_completed(returncode=1, stderr="bad init"))):
        result = ea.run_ephemeral_apply(str(tmp_path))
    assert result["evaluation_failed"] is True
    assert result["reason"] == "init_failed"
    assert not (tmp_path / ea._OVERRIDE_FILE).exists()  # cleaned up on early exit


def test_run_blocks_on_plan_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    with mock.patch.object(ea.subprocess, "run", side_effect=_mock_run_sequence(
            _completed(returncode=0),               # init
            _completed(returncode=1, stderr="bad plan"),  # plan
    )):
        result = ea.run_ephemeral_apply(str(tmp_path))
    assert result["evaluation_failed"] is True
    assert result["reason"] == "plan_failed"


def test_run_blocks_on_plan_malformed(tmp_path, monkeypatch):
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    with mock.patch.object(ea.subprocess, "run", side_effect=_mock_run_sequence(
            _completed(returncode=0),                       # init
            _completed(returncode=0),                       # plan
            _completed(returncode=0, stdout="not json"),    # show -json
    )):
        result = ea.run_ephemeral_apply(str(tmp_path))
    assert result["evaluation_failed"] is True
    assert result["reason"] == "plan_malformed"


def test_run_reports_none_coverage_without_touching_apply(tmp_path, monkeypatch):
    """A Databricks-only plan must never attempt an apply at all -- confirmed by only mocking
    exactly 3 subprocess.run calls (init, plan, show); a 4th call (apply) would raise
    StopIteration from the mock, failing the test loudly if it were ever attempted."""
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    plan_json = json.dumps(_plan([_rc("databricks_cluster", "c")]))
    with mock.patch.object(ea.subprocess, "run", side_effect=_mock_run_sequence(
            _completed(returncode=0),
            _completed(returncode=0),
            _completed(returncode=0, stdout=plan_json),
    )):
        result = ea.run_ephemeral_apply(str(tmp_path))
    assert result["evaluation_failed"] is False
    assert result["coverage"] == "none"
    assert result["databricks_resources"] == ["databricks_cluster.c"]


def test_run_blocks_on_unverified_resource_type(tmp_path, monkeypatch):
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    plan_json = json.dumps(_plan([_rc("aws_s3_bucket", "b")]))
    with mock.patch.object(ea.subprocess, "run", side_effect=_mock_run_sequence(
            _completed(returncode=0),
            _completed(returncode=0),
            _completed(returncode=0, stdout=plan_json),
    )):
        result = ea.run_ephemeral_apply(str(tmp_path))
    assert result["evaluation_failed"] is True
    assert result["reason"] == "resource_type_unverified"
    assert result["coverage"] == "full"


def test_run_blocks_on_unsupported_emulator(tmp_path, monkeypatch):
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    result = ea.run_ephemeral_apply(str(tmp_path), emulator="not-a-real-emulator")
    assert result["evaluation_failed"] is True
    assert result["reason"] == "unsupported_emulator"
    assert result["emulator"] == "not-a-real-emulator"


def test_every_verdict_names_its_emulator(tmp_path, monkeypatch):
    """docs/phase5_scope.md section 7.3: every run_ephemeral_apply() result must carry which
    emulator produced it -- a MiniStack green and a LocalStack green must never be
    presentable as the same evidence. Checked across a spread of real exit paths (not just
    one), since each was a separate dict literal in the source and any one could have been
    missed when this field was added."""
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: None)
    result = ea.run_ephemeral_apply(str(tmp_path), emulator="ministack")
    assert result["reason"] == "terraform_not_found"
    assert result["emulator"] == "ministack"

    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    with mock.patch.object(ea.subprocess, "run", side_effect=_mock_run_sequence(
            _completed(returncode=1, stderr="bad init"))):
        result = ea.run_ephemeral_apply(str(tmp_path), emulator="floci")
    assert result["reason"] == "init_failed"
    assert result["emulator"] == "floci"


def test_run_blocks_on_negative_fidelity_unverified_security_critical_type(tmp_path, monkeypatch):
    """Real, current finding from this session's actual gauntlet run (docs/phase5_scope.md
    section 7): aws_iam_role applies cleanly on both MiniStack and Floci (verified=True) but
    BOTH emulators incorrectly accept a malformed trust-policy principal ARN real AWS rejects
    (negative_fidelity_verified=False on both) -- this must BLOCK, distinctly from
    resource_type_unverified, even though the type is otherwise "verified"."""
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    plan_json = json.dumps(_plan([_rc("aws_iam_role", "r")]))
    with mock.patch.object(ea.subprocess, "run", side_effect=_mock_run_sequence(
            _completed(returncode=0), _completed(returncode=0),
            _completed(returncode=0, stdout=plan_json),
    )):
        result = ea.run_ephemeral_apply(str(tmp_path), emulator="ministack")
    assert result["evaluation_failed"] is True
    assert result["reason"] == "negative_fidelity_unverified"


def test_run_apply_failed_when_nothing_succeeded(tmp_path, monkeypatch):
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    monkeypatch.setitem(ea.RESOURCE_TYPE_ALLOWLIST, "aws_s3_bucket", ea._entry(False, ministack=(True, True), floci=(True, True), localstack=(True, True)))
    plan_json = json.dumps(_plan([_rc("aws_s3_bucket", "b")]))
    apply_output = json.dumps({"type": "apply_errored", "hook": {"resource": {"addr": "aws_s3_bucket.b"}}})
    with mock.patch.object(ea.subprocess, "run", side_effect=_mock_run_sequence(
            _completed(returncode=0),                              # init
            _completed(returncode=0),                              # plan
            _completed(returncode=0, stdout=plan_json),             # show -json
            _completed(returncode=1, stdout=apply_output),          # apply
            _completed(returncode=0),                              # destroy
    )):
        result = ea.run_ephemeral_apply(str(tmp_path))
    assert result["evaluation_failed"] is True
    assert result["reason"] == "apply_failed"


def test_run_apply_partial_failure_when_some_succeeded(tmp_path, monkeypatch):
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    monkeypatch.setitem(ea.RESOURCE_TYPE_ALLOWLIST, "aws_s3_bucket", ea._entry(False, ministack=(True, True), floci=(True, True), localstack=(True, True)))
    monkeypatch.setitem(ea.RESOURCE_TYPE_ALLOWLIST, "aws_kms_key", ea._entry(True, ministack=(True, True), floci=(True, True), localstack=(True, True)))
    plan_json = json.dumps(_plan([_rc("aws_s3_bucket", "b"), _rc("aws_kms_key", "k")]))
    apply_output = "\n".join(json.dumps(e) for e in [
        {"type": "apply_complete", "hook": {"resource": {"addr": "aws_s3_bucket.b"}}},
        {"type": "apply_errored", "hook": {"resource": {"addr": "aws_kms_key.k"}}},
    ])
    with mock.patch.object(ea.subprocess, "run", side_effect=_mock_run_sequence(
            _completed(returncode=0), _completed(returncode=0),
            _completed(returncode=0, stdout=plan_json),
            _completed(returncode=1, stdout=apply_output),
            _completed(returncode=0),
    )):
        result = ea.run_ephemeral_apply(str(tmp_path))
    assert result["evaluation_failed"] is True
    assert result["reason"] == "apply_partial_failure"


def test_run_apply_result_malformed_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    monkeypatch.setitem(ea.RESOURCE_TYPE_ALLOWLIST, "aws_s3_bucket", ea._entry(False, ministack=(True, True), floci=(True, True), localstack=(True, True)))
    plan_json = json.dumps(_plan([_rc("aws_s3_bucket", "b")]))
    with mock.patch.object(ea.subprocess, "run", side_effect=_mock_run_sequence(
            _completed(returncode=0), _completed(returncode=0),
            _completed(returncode=0, stdout=plan_json),
            _completed(returncode=0, stdout="garbage, not json"),
            _completed(returncode=0),
    )):
        result = ea.run_ephemeral_apply(str(tmp_path))
    assert result["evaluation_failed"] is True
    assert result["reason"] == "apply_result_malformed"


def test_run_apply_timeout_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    monkeypatch.setitem(ea.RESOURCE_TYPE_ALLOWLIST, "aws_s3_bucket", ea._entry(False, ministack=(True, True), floci=(True, True), localstack=(True, True)))
    plan_json = json.dumps(_plan([_rc("aws_s3_bucket", "b")]))
    with mock.patch.object(ea.subprocess, "run", side_effect=_mock_run_sequence(
            _completed(returncode=0), _completed(returncode=0),
            _completed(returncode=0, stdout=plan_json),
            subprocess.TimeoutExpired(cmd="terraform apply", timeout=5),
            _completed(returncode=0),
    )):
        result = ea.run_ephemeral_apply(str(tmp_path))
    assert result["evaluation_failed"] is True
    assert result["reason"] == "apply_timeout"


def test_run_teardown_failure_overrides_an_otherwise_clean_verdict(tmp_path, monkeypatch):
    """A failed teardown must flip an otherwise-successful verdict -- an ephemeral environment
    that fails to tear down is a real operational problem, never a footnote on a green result."""
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    monkeypatch.setitem(ea.RESOURCE_TYPE_ALLOWLIST, "aws_s3_bucket", ea._entry(False, ministack=(True, True), floci=(True, True), localstack=(True, True)))
    plan_json = json.dumps(_plan([_rc("aws_s3_bucket", "b")]))
    apply_output = json.dumps({"type": "apply_complete", "hook": {"resource": {"addr": "aws_s3_bucket.b"}}})
    with mock.patch.object(ea.subprocess, "run", side_effect=_mock_run_sequence(
            _completed(returncode=0), _completed(returncode=0),
            _completed(returncode=0, stdout=plan_json),
            _completed(returncode=0, stdout=apply_output),
            _completed(returncode=1, stderr="destroy failed"),
    )):
        result = ea.run_ephemeral_apply(str(tmp_path))
    assert result["evaluation_failed"] is True
    assert result["reason"] == "teardown_failed"


def test_run_clean_success_reports_full_coverage_and_applied_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    monkeypatch.setitem(ea.RESOURCE_TYPE_ALLOWLIST, "aws_s3_bucket", ea._entry(False, ministack=(True, True), floci=(True, True), localstack=(True, True)))
    plan_json = json.dumps(_plan([_rc("aws_s3_bucket", "b")]))
    apply_output = json.dumps({"type": "apply_complete", "hook": {"resource": {"addr": "aws_s3_bucket.b"}}})
    with mock.patch.object(ea.subprocess, "run", side_effect=_mock_run_sequence(
            _completed(returncode=0), _completed(returncode=0),
            _completed(returncode=0, stdout=plan_json),
            _completed(returncode=0, stdout=apply_output),
            _completed(returncode=0),
    )):
        result = ea.run_ephemeral_apply(str(tmp_path))
    assert result["evaluation_failed"] is False
    assert result["coverage"] == "full"
    assert result["aws_resources_applied"] == ["aws_s3_bucket.b"]


def test_exception_in_apply_stage_propagates_not_swallowed_by_teardown(tmp_path, monkeypatch):
    """Direct regression for the finally-return anti-pattern this file's first draft avoided --
    confirmed live (a bare `return` in a finally block silently swallows a real exception raised
    in the try block) before writing the single-exit design. A genuine bug during the apply
    stage must still propagate after teardown runs, never be hidden behind cleanup."""
    monkeypatch.setattr(ea.toolpath, "find_tool", lambda name: "terraform")
    monkeypatch.setitem(ea.RESOURCE_TYPE_ALLOWLIST, "aws_s3_bucket", ea._entry(False, ministack=(True, True), floci=(True, True), localstack=(True, True)))
    plan_json = json.dumps(_plan([_rc("aws_s3_bucket", "b")]))

    def _run(*args, **kwargs):
        cmd = args[0]
        if "apply" in cmd:
            raise RuntimeError("a genuine, unexpected programming error")
        if "destroy" in cmd:
            return _completed(returncode=0)
        if "init" in cmd:
            return _completed(returncode=0)
        if "plan" in cmd and "-out" in cmd:
            return _completed(returncode=0)
        if "show" in cmd:
            return _completed(returncode=0, stdout=plan_json)
        raise AssertionError(f"unexpected command: {cmd}")

    with mock.patch.object(ea.subprocess, "run", side_effect=_run):
        with pytest.raises(RuntimeError, match="a genuine, unexpected programming error"):
            ea.run_ephemeral_apply(str(tmp_path))


# ---------------------------------------------------------------------------
# Real, live integration tests -- no LocalStack account needed for these specifically, since
# they test genuine unreachability (never requires a paid or even a running LocalStack).
# ---------------------------------------------------------------------------

@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Confirmed live, not theoretical: this test caused a REAL regression on "
           "windows-latest CI (test_rego_gate.py/test_schema_lint.py/test_schema_watch.py all "
           "failed immediately after it with 'file...being used by another process'). A timed-"
           "out apply against an unreachable endpoint leaves an orphaned terraform-provider-aws "
           "process that holds a Windows-only file lock on the SHARED TF_PLUGIN_CACHE_DIR, "
           "breaking unrelated tests in the same CI job -- Windows' delete/lock semantics for a "
           "dead-but-not-yet-reaped process differ from POSIX's (macos-latest and ubuntu-latest "
           "both ran this same test clean in the same CI run). G9 itself is structurally "
           "Ubuntu-only anyway (docs/phase5_scope.md section 1) -- skipping on Windows only "
           "is the correct fix, not process-tree-killing on a platform this gate will never "
           "run on for real.")
def test_real_apply_against_unreachable_endpoint_blocks_not_hangs_forever(tmp_path):
    """The real test that caught the provider-override-ordering bug in this file's first draft
    (see the module's own run_ephemeral_apply docstring): a genuinely unreachable LocalStack
    endpoint (nothing is listening -- this does not require any LocalStack account, paid or
    free, to prove) must block, in bounded time, never silently proceed as if it applied."""
    import os
    (tmp_path / "main.tf").write_text('''
terraform {
  required_providers { aws = { source = "hashicorp/aws", version = ">= 5.0" } }
}
resource "aws_s3_bucket" "probe" {
  bucket = "g9-test-unreachable-probe"
}
''', encoding="utf-8")
    with mock.patch.dict(ea.RESOURCE_TYPE_ALLOWLIST, {"aws_s3_bucket": ea._entry(False, ministack=(True, True), floci=(True, True), localstack=(True, True))}):
        start = time.monotonic()
        result = ea.run_ephemeral_apply(str(tmp_path), localstack_endpoint="http://localhost:4566",
                                         apply_timeout=8, destroy_timeout=8)
        elapsed = time.monotonic() - start
    assert result["evaluation_failed"] is True
    assert result["reason"] in ("apply_timeout", "apply_failed")
    assert elapsed < 90, f"took {elapsed}s -- should give up in bounded time, not hang"
    assert not os.path.exists(str(tmp_path / ea._OVERRIDE_FILE))


# ---------------------------------------------------------------------------
# Audit-chain logging -- same underlying primitive plan_gate.py's _audit() uses, advisory only
# ---------------------------------------------------------------------------

def test_log_result_writes_to_the_same_audit_chain(tmp_path, monkeypatch):
    """Path resolution matches plan_gate.py's own LOG_DIR exactly (os.getcwd()-based) -- this is
    what makes it genuinely the same audit chain rather than a second one under a similar name."""
    monkeypatch.chdir(tmp_path)
    result = {"evaluation_failed": False, "coverage": "full", "databricks_resources": [],
              "aws_resources_applied": ["aws_s3_bucket.b"], "findings": []}
    ea.log_result("/some/dir", result)
    log_path = tmp_path / ".agents" / "logs" / "audit.jsonl"
    assert log_path.exists()
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["component"] == "ephemeral_apply"
    assert lines[0]["status"] == "OK"
    assert lines[0]["g9_result"] == result


def test_log_result_marks_blocked_status_on_a_failed_verdict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = {"evaluation_failed": True, "reason": "apply_timeout", "coverage": "full"}
    ea.log_result("/some/dir", result)
    log_path = tmp_path / ".agents" / "logs" / "audit.jsonl"
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines[0]["status"] == "BLOCKED"


# ---------------------------------------------------------------------------
# compose_with_g5 -- docs/phase5_scope.md section 2's explicit composition requirement
# ---------------------------------------------------------------------------

def test_compose_with_g5_no_g9_evidence_never_reads_as_clean():
    """Condition 1's core requirement: absence of a G9 result must never be reported as if G9
    passed."""
    g5 = {"reduced_assurance": False, "databricks_resources": []}
    composed = ea.compose_with_g5(g5, None)
    assert composed["g9_ran"] is False
    assert "no ephemeral-apply verification has run" in composed["summary"]


def test_compose_with_g5_no_g9_evidence_but_g5_already_flags_databricks():
    g5 = {"reduced_assurance": True, "databricks_resources": ["databricks_cluster.c"]}
    composed = ea.compose_with_g5(g5, None)
    assert composed["g9_ran"] is False
    assert composed["reduced_assurance"] is True
    assert "databricks_cluster.c" in composed["summary"]


def test_compose_with_g5_full_coverage_clean():
    g5 = {"reduced_assurance": False, "databricks_resources": []}
    g9 = {"evaluation_failed": False, "coverage": "full", "databricks_resources": []}
    composed = ea.compose_with_g5(g5, g9)
    assert composed["g9_ran"] is True
    assert composed["g9_coverage"] == "full"
    assert "every resource" in composed["summary"].lower()


def test_compose_with_g5_partial_coverage_never_reads_as_full():
    """The exact case condition 1 named: a mixed plan's G9 result must never be reported as
    carrying the same assurance as a full-AWS plan."""
    g5 = {"reduced_assurance": True, "databricks_resources": ["databricks_cluster.c"]}
    g9 = {"evaluation_failed": False, "coverage": "partial",
          "databricks_resources": ["databricks_cluster.c"]}
    composed = ea.compose_with_g5(g5, g9)
    assert composed["g9_coverage"] == "partial"
    assert "never exercised" in composed["summary"]
    assert "databricks_cluster.c" in composed["summary"]


def test_compose_with_g5_none_coverage_never_reads_as_passed():
    g5 = {"reduced_assurance": True, "databricks_resources": ["databricks_cluster.c"]}
    g9 = {"evaluation_failed": False, "coverage": "none",
          "databricks_resources": ["databricks_cluster.c"]}
    composed = ea.compose_with_g5(g5, g9)
    assert composed["g9_coverage"] == "none"
    assert "never exercised" in composed["summary"]
