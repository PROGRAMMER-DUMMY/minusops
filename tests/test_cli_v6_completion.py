"""
.FR-04 completion: precedence, list filters, the attribute card, gate status.

These close the gap between v6's acceptance criteria (which passed) and its FR bodies (which
specified more). The precedence order is the part with teeth, and it changed in Matt's ruling
of 2026-08-22: explicit flag, then upward discovery, then the stored context, then REFUSAL.
There is no "most recent run" fallback -- an engineer's five-minute-old prototype is not a
safe default for `gate apply`.

Depends on: core/cli/{context,formatters}.py, core/cli/commands/{runs,gate}.py,
    core/governance/plan_gate.py
Shells out to: nothing (Terraform and AWS are never reached)
Used by: nothing (pytest entry point)
"""
import io
import json
import os
from contextlib import redirect_stdout

import pytest

import runs
from cli import context as cli_context
from cli import main as cli_main


def _capture(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli_main.main(argv)
    return code, out.getvalue()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(cli_context, "WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- Context timestamp ----------------------------------------------------------------

def test_the_context_records_when_it_was_set(workspace):
    """A stale selection is the dangerous one. Without a timestamp there is no way to notice
    the active run was chosen three weeks ago."""
    run = runs.new_run(name="clickstream", domain="marketing")

    cli_context.set_active_run(run["run_id"])

    saved = json.loads((workspace / ".minus" / "context.json").read_text(encoding="utf-8"))
    assert saved["active_run"] == run["run_id"]
    assert saved["updated_at"].endswith("+00:00")


# --- Upward discovery -----------------------------------------------------------------

def test_being_inside_a_run_directory_resolves_that_run(workspace, monkeypatch):
    """An operator who has cd'd into a run is unambiguously working on it."""
    run = runs.new_run(name="clickstream", domain="marketing")
    monkeypatch.chdir(workspace / "runs" / run["run_id"])

    assert cli_context.discover_run_from_cwd() == run["run_id"]


def test_discovery_works_from_a_nested_subdirectory(workspace, monkeypatch):
    """`cd runs/<id>/terraform` is where an operator actually stands when running the gate."""
    run = runs.new_run(name="clickstream", domain="marketing")
    monkeypatch.chdir(workspace / "runs" / run["run_id"] / "terraform")

    assert cli_context.discover_run_from_cwd() == run["run_id"]


def test_discovery_finds_nothing_outside_a_run(workspace):
    assert cli_context.discover_run_from_cwd() is None


def test_a_runs_lookalike_directory_is_not_a_run(workspace, monkeypatch):
    """`runs/` membership alone is not enough -- a scratch directory somebody made under
    runs/ has no run.json and must not be resolved as a workspace."""
    stray = workspace / "runs" / "not-a-run" / "terraform"
    stray.mkdir(parents=True)
    monkeypatch.chdir(stray)

    assert cli_context.discover_run_from_cwd() is None


def test_discovery_outranks_the_stored_context(workspace, monkeypatch):
    """Matt's ruling of 2026-08-22: where you ARE beats what you last selected. Standing in
    one run while a command silently operates on another is the surprise this removes."""
    selected = runs.new_run(name="selected", domain="ops")
    standing_in = runs.new_run(name="standing", domain="ops")
    cli_context.set_active_run(selected["run_id"])
    monkeypatch.chdir(workspace / "runs" / standing_in["run_id"])

    assert cli_context.resolve_run()["run_id"] == standing_in["run_id"]


def test_an_explicit_flag_outranks_discovery(workspace, monkeypatch):
    named = runs.new_run(name="named", domain="ops")
    standing_in = runs.new_run(name="standing", domain="ops")
    monkeypatch.chdir(workspace / "runs" / standing_in["run_id"])

    assert cli_context.resolve_run(named["run_id"])["run_id"] == named["run_id"]


def test_nothing_anywhere_still_refuses(workspace):
    """Ratified 2026-08-22. A prototype created five minutes ago by someone else is not a
    safe default for a mutating command."""
    runs.new_run(name="prototype", domain="ops")

    with pytest.raises(cli_context.ContextError):
        cli_context.resolve_run()


# --- List filters and columns ---------------------------------------------------------

def _seed(**kwargs):
    defaults = dict(orchestrator="mwaa", compute_engine="Glue 4.0", tier="test",
                    governance_status="PROVEN_TEST")
    defaults.update(kwargs)
    return runs.new_run(**defaults)


def test_the_table_has_the_specified_columns(workspace):
    _seed(name="clickstream", domain="marketing")

    _, output = _capture(["runs", "list"])

    header = output.splitlines()[0]
    for column in ("Active", "Run Name", "Domain", "Engine", "Orchestrator", "Cost/Mo",
                   "Status"):
        assert column in header


def test_inactive_rows_carry_an_empty_marker(workspace):
    """`[ ]` against `[*]` reads as a column. A blank cell reads as a rendering bug."""
    run = _seed(name="clickstream", domain="marketing")
    other = _seed(name="ledger", domain="finance")
    cli_context.set_active_run(run["run_id"])

    _, output = _capture(["runs", "list"])

    assert "[*]" in next(l for l in output.splitlines() if run["run_id"] in l)
    assert "[ ]" in next(l for l in output.splitlines() if other["run_id"] in l)


def test_an_unpriced_run_says_unpriced_not_zero(workspace):
    """NFR-03. $0.00 in a cost column is the one number an executive remembers."""
    _seed(name="clickstream", domain="marketing")

    _, output = _capture(["runs", "list"])

    assert "unpriced" in output
    assert "$0.00" not in output


def test_filtering_by_domain(workspace):
    _seed(name="clickstream", domain="marketing")
    _seed(name="ledger", domain="finance")

    _, output = _capture(["runs", "list", "--domain", "marketing"])

    assert "marketing" in output
    assert "finance" not in output


def test_filtering_by_orchestrator(workspace):
    _seed(name="clickstream", domain="marketing", orchestrator="mwaa")
    _seed(name="ledger", domain="finance", orchestrator="stepfunctions")

    _, output = _capture(["runs", "list", "--orchestrator", "stepfunctions"])

    assert "finance-ledger-stepfunctions_" in output
    assert "marketing-clickstream-mwaa_" not in output


def test_filtering_by_tier(workspace):
    _seed(name="clickstream", domain="marketing", tier="prod")
    _seed(name="ledger", domain="finance", tier="dev")

    _, output = _capture(["runs", "list", "--tier", "prod"])

    assert "clickstream" in output
    assert "ledger" not in output


def test_a_filter_that_matches_nothing_says_so(workspace):
    """An empty table with a header reads as "there are no runs", which is a different and
    much more alarming statement than "none matched"."""
    _seed(name="clickstream", domain="marketing")

    code, output = _capture(["runs", "list", "--domain", "nope"])

    assert code == 0
    assert "no runs match" in output.lower()


def test_a_run_with_no_tier_is_excluded_by_a_tier_filter(workspace):
    """Undeclared is not a wildcard. Including it would put an unclassified run in a `--tier
    prod` listing."""
    runs.new_run(name="untiered", domain="ops")
    _seed(name="clickstream", domain="marketing", tier="prod")

    _, output = _capture(["runs", "list", "--tier", "prod"])

    assert "untiered" not in output


def test_json_output_is_unfiltered_by_formatting_but_honours_filters(workspace):
    _seed(name="clickstream", domain="marketing")
    _seed(name="ledger", domain="finance")

    _, output = _capture(["runs", "list", "--domain", "finance", "--json"])

    data = json.loads(output)
    assert [item["domain"] for item in data] == ["finance"]


# --- The attribute card ---------------------------------------------------------------

@pytest.fixture
def described_run(workspace):
    run = _seed(name="clickstream", domain="marketing", owner="data-eng@acme.com")
    root = workspace / "runs" / run["run_id"]
    (root / "requirements.json").write_text(json.dumps({
        "goal": "clickstream lakehouse",
        "data_pipeline": {"sources": "Webhook / EventBridge",
                          "storage_zones": "Medallion S3 (Bronze, Silver, Gold, Quarantine)",
                          "catalog": "Apache Iceberg v2",
                          "consumption": "Athena + Glue Catalog Views",
                          "data_quality": "Great Expectations (Suite v3)",
                          "orchestration": "Amazon MWAA (Airflow 2.8.1)"}}),
        encoding="utf-8")
    (root / "architecture_decision.json").write_text(json.dumps({
        "selected_architecture": "medallion-lakehouse",
        "compute_engine": "AWS Glue 4.0 (PySpark, 10x G.1X)",
        "selected_modules": ["storage-medallion-s3", "compute-glue-etl"]}), encoding="utf-8")
    (root / "terraform").mkdir(exist_ok=True)
    (root / "terraform" / "outputs.json").write_text(json.dumps({
        "bronze_bucket": "acme-mktg-bronze-prod-001",
        "gold_bucket": "acme-mktg-gold-prod-001",
        "quarantine_bucket": "acme-mktg-quarantine-prod-001",
        "region": "us-east-1"}), encoding="utf-8")
    return run


def test_the_card_renders_the_four_specified_sections(described_run):
    _, output = _capture(["runs", "describe", described_run["run_id"]])

    for heading in ("[Metadata]", "[Architecture Attributes]",
                    "[FinOps & Resource Endpoints]", "[Artifact Paths]"):
        assert heading in output


def test_metadata_comes_from_the_run_record(described_run):
    _, output = _capture(["runs", "describe", described_run["run_id"]])

    assert "data-eng@acme.com" in output
    assert "marketing" in output
    assert "PROVEN_TEST" in output
    assert "test" in output


def test_architecture_attributes_come_from_requirements_and_the_decision(described_run):
    """These are the facts an architect asks for, and they live in two different files."""
    _, output = _capture(["runs", "describe", described_run["run_id"]])

    assert "Webhook / EventBridge" in output
    assert "Apache Iceberg v2" in output
    assert "AWS Glue 4.0 (PySpark, 10x G.1X)" in output
    assert "Great Expectations (Suite v3)" in output
    assert "Athena + Glue Catalog Views" in output


def test_resource_endpoints_come_from_terraform_outputs(described_run):
    """Reconstructing a bucket name from a prefix is the guess that seeds the wrong bucket.
    The outputs are what the stack actually created."""
    _, output = _capture(["runs", "describe", described_run["run_id"]])

    assert "s3://acme-mktg-bronze-prod-001" in output
    assert "s3://acme-mktg-quarantine-prod-001" in output


def test_spend_reads_bcm_evidence_when_it_exists(described_run, workspace):
    root = workspace / "runs" / described_run["run_id"]
    (root / "bcm").mkdir(exist_ok=True)
    (root / "bcm" / "estimated_monthly_spend.json").write_text(
        json.dumps({"estimated_monthly_cost": 248.50}), encoding="utf-8")

    _, output = _capture(["runs", "describe", described_run["run_id"]])

    assert "$248.50" in output


def test_spend_without_bcm_evidence_is_unpriced(described_run):
    _, output = _capture(["runs", "describe", described_run["run_id"]])

    assert "unpriced" in output
    assert "$0.00" not in output


def test_artifact_paths_say_which_ones_are_missing(described_run):
    """A path printed for a file that does not exist sends the reader to an empty directory
    and makes them doubt the tool, not the run."""
    _, output = _capture(["runs", "describe", described_run["run_id"]])

    proving = next(l for l in output.splitlines() if "proving_report.json" in l)
    decision = next(l for l in output.splitlines() if "architecture_decision.json" in l)
    assert "missing" in proving.lower()
    assert "missing" not in decision.lower()


def test_an_unpopulated_run_still_renders_every_section(workspace):
    """A run described immediately after `create` has almost nothing. It must render dashes,
    not crash and not omit the sections."""
    run = runs.new_run(name="fresh", domain="ops")

    code, output = _capture(["runs", "describe", run["run_id"]])

    assert code == 0
    for heading in ("[Metadata]", "[Architecture Attributes]",
                    "[FinOps & Resource Endpoints]", "[Artifact Paths]"):
        assert heading in output


def test_the_card_carries_no_emoji(described_run):
    _, output = _capture(["runs", "describe", described_run["run_id"]])

    assert all(ord(ch) < 0x2190 for ch in output)


# --- Gate status ----------------------------------------------------------------------

def test_gate_status_reports_a_directory_that_was_never_planned(workspace, monkeypatch):
    import plan_gate

    monkeypatch.setattr(plan_gate, "LOG_DIR", str(workspace / ".agents" / "logs"))
    status = plan_gate.gate_status(str(workspace / "terraform"))

    assert status["planned"] is False
    assert status["approved"] is False
    assert status["plan_hash"] is None


def test_gate_status_reads_the_pending_plan_without_running_terraform(workspace, monkeypatch):
    """Status has to be instant and credential-free, or nobody runs it. Re-hashing the plan
    would shell out to terraform on every call."""
    import plan_gate

    monkeypatch.setattr(plan_gate, "LOG_DIR", str(workspace / ".agents" / "logs"))
    def _never(*args, **kwargs):
        raise AssertionError("gate status must not invoke terraform")
    monkeypatch.setattr(plan_gate, "_tf", _never)

    tf_dir = str(workspace / "terraform")
    state = plan_gate._state_dir(tf_dir)
    os.makedirs(state, exist_ok=True)
    with open(os.path.join(state, "pending_plan.json"), "w", encoding="utf-8") as f:
        json.dump({"plan_hash": "a" * 64, "canonical_dir": tf_dir,
                   "cloud_drift": {"reverts_out_of_band_changes": True},
                   "g9_result": {"reason": "clean"}}, f)

    status = plan_gate.gate_status(tf_dir)

    assert status["planned"] is True
    assert status["plan_hash"] == "a" * 64
    assert status["approved"] is False
    assert status["reverts_out_of_band_changes"] is True


def test_gate_status_sees_an_approval_bound_to_that_hash(workspace, monkeypatch):
    import plan_gate

    monkeypatch.setattr(plan_gate, "LOG_DIR", str(workspace / ".agents" / "logs"))
    tf_dir = str(workspace / "terraform")
    state = plan_gate._state_dir(tf_dir)
    os.makedirs(os.path.join(state, "approvals"), exist_ok=True)
    with open(os.path.join(state, "pending_plan.json"), "w", encoding="utf-8") as f:
        json.dump({"plan_hash": "b" * 64, "canonical_dir": tf_dir}, f)
    with open(os.path.join(state, "approvals", ("b" * 64) + ".json"), "w",
              encoding="utf-8") as f:
        json.dump({"plan_hash": "b" * 64, "approver": "alice", "approved_at": "2026-08-22"}, f)

    status = plan_gate.gate_status(tf_dir)

    assert status["approved"] is True
    assert status["approver"] == "alice"


def test_an_approval_for_a_different_hash_does_not_count(workspace, monkeypatch):
    """A stale approval from a previous plan is exactly what the hash binding exists to
    invalidate. Reporting it as approved would undo that in the status line."""
    import plan_gate

    monkeypatch.setattr(plan_gate, "LOG_DIR", str(workspace / ".agents" / "logs"))
    tf_dir = str(workspace / "terraform")
    state = plan_gate._state_dir(tf_dir)
    os.makedirs(os.path.join(state, "approvals"), exist_ok=True)
    with open(os.path.join(state, "pending_plan.json"), "w", encoding="utf-8") as f:
        json.dump({"plan_hash": "c" * 64, "canonical_dir": tf_dir}, f)
    with open(os.path.join(state, "approvals", ("d" * 64) + ".json"), "w",
              encoding="utf-8") as f:
        json.dump({"plan_hash": "d" * 64, "approver": "alice"}, f)

    assert plan_gate.gate_status(tf_dir)["approved"] is False


def test_minusctl_gate_status_renders_without_touching_terraform(workspace, monkeypatch):
    import plan_gate

    monkeypatch.setattr(plan_gate, "LOG_DIR", str(workspace / ".agents" / "logs"))
    run = runs.new_run(name="clickstream", domain="marketing")
    cli_context.set_active_run(run["run_id"])

    code, output = _capture(["gate", "status"])

    assert code == 0
    assert "planned" in output.lower()
    assert all(ord(ch) < 0x2190 for ch in output)


def test_gate_status_is_not_forwarded_to_the_engine_as_a_stage(workspace, monkeypatch):
    """`status` is a CLI-side read of recorded state, not a sixth gate stage. Forwarding it
    would hand plan_gate a stage it does not implement."""
    from cli.commands import gate as gate_cmd

    run = runs.new_run(name="clickstream", domain="marketing")
    cli_context.set_active_run(run["run_id"])
    import plan_gate
    monkeypatch.setattr(plan_gate, "LOG_DIR", str(workspace / ".agents" / "logs"))

    def _never(argv):
        raise AssertionError(f"status was delegated as a stage: {argv}")
    monkeypatch.setattr(gate_cmd, "_delegate", _never)

    code, _ = _capture(["gate", "status"])

    assert code == 0


# --- Consistency between the two views ------------------------------------------------

def test_list_and_describe_agree_about_cost(described_run, workspace):
    """Two views of one run disagreeing about spend is worse than neither showing it: the
    reader believes whichever they saw last. Both must read the same BCM evidence."""
    root = workspace / "runs" / described_run["run_id"]
    (root / "bcm").mkdir(exist_ok=True)
    (root / "bcm" / "estimated_monthly_spend.json").write_text(
        json.dumps({"estimated_monthly_cost": 248.50}), encoding="utf-8")

    _, listed = _capture(["runs", "list"])
    _, described = _capture(["runs", "describe", described_run["run_id"]])

    assert "$248.50" in listed
    assert "$248.50" in described


def test_artifact_paths_are_workspace_relative(described_run):
    """An absolute path on Windows is 120 characters of noise that wraps in every terminal
    and cannot be pasted into a repo-relative context."""
    _, output = _capture(["runs", "describe", described_run["run_id"]])

    terraform_line = next(l for l in output.splitlines() if "main.tf" in l)
    assert "runs/" in terraform_line.replace("\\", "/")
    assert ":" not in terraform_line.split("main.tf")[0].replace("HCL", "")


# --- --role-arn as a verified assertion -----------------------------------------------
#
# `--mfa-arn` is deliberately absent; see the module note in plan_gate. This one is
# implementable because the gate already reads the ambient session's ARN.

def _posture(arn):
    return {"connected": True, "type": "temporary", "arn": arn}


def test_approve_refuses_when_the_session_is_not_the_asserted_role():
    """The operator states which role this approval must be made under. If the shell is
    assumed into a different one, that is exactly the mistake worth catching before an
    approval record exists."""
    import plan_gate

    blocked = plan_gate._reject_if_not_asserted_role(
        _posture("arn:aws:sts::111:assumed-role/minusops-deploy-acme-ml/alice"),
        "arn:aws:iam::111:role/minusops-deploy-acme-data")
    assert blocked is True


def test_approve_accepts_the_asserted_role_including_an_assumed_session():
    import plan_gate

    for arn in ("arn:aws:iam::111:role/minusops-deploy-acme-data",
                "arn:aws:sts::111:assumed-role/minusops-deploy-acme-data/alice"):
        assert plan_gate._reject_if_not_asserted_role(
            _posture(arn), "arn:aws:iam::111:role/minusops-deploy-acme-data") is False


def test_no_assertion_means_no_check():
    """Backward compatible: every existing caller passes nothing and must behave as before."""
    import plan_gate

    assert plan_gate._reject_if_not_asserted_role(_posture("arn:aws:iam::1:user/bob"),
                                                  None) is False


def test_an_unreadable_identity_fails_closed_against_an_assertion():
    """If we cannot tell whose session this is, we cannot tell it is the asserted role."""
    import plan_gate

    assert plan_gate._reject_if_not_asserted_role(
        {}, "arn:aws:iam::111:role/minusops-deploy-acme-data") is True


def test_the_gate_cli_accepts_role_arn():
    import plan_gate

    parser = plan_gate._build_parser()
    args = parser.parse_args(["approve", "--dir", "x", "--role-arn",
                              "arn:aws:iam::111:role/r"])
    assert args.role_arn == "arn:aws:iam::111:role/r"


def test_minusctl_gate_forwards_role_arn(workspace, monkeypatch):
    from cli.commands import gate as gate_cmd

    run = runs.new_run(name="clickstream", domain="marketing")
    cli_context.set_active_run(run["run_id"])
    seen = {}

    def _spy(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(gate_cmd, "_delegate", _spy)
    _capture(["gate", "approve", "--role-arn", "arn:aws:iam::111:role/r"])

    assert seen["argv"][seen["argv"].index("--role-arn") + 1] == "arn:aws:iam::111:role/r"


def test_there_is_no_mfa_arn_flag():
    """Pinned deliberately. `sts get-caller-identity` carries no MFA claim, so a --mfa-arn
    the gate accepted and never verified would be an unchecked assertion sitting in an audit
    record. MFA is enforced by the role's trust policy at AssumeRole time, upstream of here."""
    import plan_gate

    parser = plan_gate._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["approve", "--dir", "x", "--mfa-arn", "arn:aws:iam::111:mfa/u"])
