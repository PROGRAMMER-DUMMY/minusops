"""
Step 9: seed, adopt, and the PR reviewer's packaging.

The seed tests never reach AWS. What matters is the safety contract around it -- that the
default changes nothing, that approval is asked before anything is sent, and that an empty
result is reported as a failure rather than a success.
"""
import json
import os

import adopt as adopt_engine
import seed as seed_engine

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_OUTPUTS = {
    "bucket_names": {"bronze": "acme-bronze-1", "silver": "acme-silver-1", "gold": "acme-gold-1"},
    "glue_job_names": {"bronze_to_silver": "acme-bronze_to_silver"},
    "athena_workgroup": "acme-analysts",
    "glue_catalog_database": "acme_gold",
}


# --- Seed ---------------------------------------------------------------------------------

def test_seed_defaults_to_plan_and_sends_nothing(tmp_path, monkeypatch):
    """`minusctl` is local-only by contract. seed is the one exception, so it must not act
    until an operator explicitly asks."""
    monkeypatch.setattr(seed_engine, "read_outputs", lambda _dir: _OUTPUTS)
    called = []
    monkeypatch.setattr(seed_engine, "_aws", lambda *a, **k: called.append(a) or (True, {}, ""))
    monkeypatch.setattr(seed_engine.approval, "request_approval",
                        lambda *a, **k: called.append("approval") or True)

    result = seed_engine.seed(str(tmp_path), "fixture.json")

    assert result["executed"] is False
    assert called == [], "plan mode must not call AWS or even ask for approval"
    text = seed_engine.format_result(result)
    assert "PLAN ONLY" in text
    assert "s3://acme-bronze-1/data/sample.json" in text


def test_seed_asks_before_it_acts(tmp_path, monkeypatch):
    monkeypatch.setattr(seed_engine, "read_outputs", lambda _dir: _OUTPUTS)
    fixture = tmp_path / "sample.json"
    fixture.write_text("[]", encoding="utf-8")
    sent = []
    monkeypatch.setattr(seed_engine, "_aws", lambda args, **k: sent.append(args) or (True, {}, ""))
    monkeypatch.setattr(seed_engine.approval, "request_approval", lambda *a, **k: False)

    result = seed_engine.seed(str(tmp_path), str(fixture), execute=True)

    assert result["ok"] is False
    assert "approval denied" in result["error"]
    assert sent == [], "a denied approval must leave AWS untouched"


def test_seed_names_every_side_effect_in_the_approval_request(tmp_path, monkeypatch):
    """An approval prompt that says "seed the pipeline" teaches operators to click yes."""
    monkeypatch.setattr(seed_engine, "read_outputs", lambda _dir: _OUTPUTS)
    fixture = tmp_path / "sample.json"
    fixture.write_text("[]", encoding="utf-8")
    asked = {}

    def _capture(action, details, mode="gatekeeper"):
        asked.update(action=action, details=details)
        return False

    monkeypatch.setattr(seed_engine.approval, "request_approval", _capture)
    seed_engine.seed(str(tmp_path), str(fixture), execute=True)

    for expected in ("acme-bronze-1", "acme-bronze_to_silver", "acme_gold"):
        assert expected in asked["details"]


def test_seed_refuses_a_stack_missing_the_outputs_it_needs(tmp_path, monkeypatch):
    monkeypatch.setattr(seed_engine, "read_outputs", lambda _dir: {"bucket_names": {}})
    result = seed_engine.seed(str(tmp_path), "fixture.json")
    assert result["ok"] is False
    assert "glue_job_names" in result["error"]


def test_seed_reads_bucket_names_from_outputs_not_from_a_prefix():
    """Bucket names contain the account id and the run hash. Re-deriving them by string
    surgery is how you seed the wrong bucket and report success."""
    planned = seed_engine.plan_steps(_OUTPUTS, "f.json")
    assert planned["bronze"] == "acme-bronze-1"
    assert "acme-bronze-1" in " ".join(planned["steps"][0]["command"])


def test_an_empty_gold_table_is_a_failure_not_a_pass(tmp_path, monkeypatch):
    """The transform ran and produced nothing queryable. Reporting that as success is exactly
    the false green this whole command exists to prevent."""
    monkeypatch.setattr(seed_engine, "read_outputs", lambda _dir: _OUTPUTS)
    monkeypatch.setattr(seed_engine.approval, "request_approval", lambda *a, **k: True)
    monkeypatch.setattr(seed_engine.time, "sleep", lambda _s: None)
    fixture = tmp_path / "sample.json"
    fixture.write_text("[]", encoding="utf-8")

    def _fake_aws(args, **kwargs):
        head = " ".join(args[:2])
        if head == "s3 cp":
            return True, "", ""
        if head == "glue start-job-run":
            return True, {"JobRunId": "jr_1"}, ""
        if head == "glue get-job-run":
            return True, {"JobRun": {"JobRunState": "SUCCEEDED"}}, ""
        if head == "athena start-query-execution":
            return True, {"QueryExecutionId": "q1"}, ""
        if head == "athena get-query-execution":
            return True, {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}, ""
        if head == "athena get-query-results":
            # Header row only: the table exists and holds nothing.
            return True, {"ResultSet": {"Rows": [{"Data": [{"VarCharValue": "row_count"}]}]}}, ""
        raise AssertionError(f"unexpected call: {args}")

    monkeypatch.setattr(seed_engine, "_aws", _fake_aws)
    result = seed_engine.seed(str(tmp_path), str(fixture), execute=True)

    assert result["ok"] is False
    assert "empty" in result["error"]
    # The first two steps genuinely succeeded and must still be reported as such.
    assert [r["step"] for r in result["results"]] == ["upload", "run_job"]


def test_a_failed_glue_run_surfaces_the_aws_error_message(tmp_path, monkeypatch):
    """SystemExit means the paths were never wired; AccessDenied means the role cannot write.
    Swallowing the message loses the diagnosis."""
    monkeypatch.setattr(seed_engine, "read_outputs", lambda _dir: _OUTPUTS)
    monkeypatch.setattr(seed_engine.approval, "request_approval", lambda *a, **k: True)
    monkeypatch.setattr(seed_engine.time, "sleep", lambda _s: None)
    fixture = tmp_path / "sample.json"
    fixture.write_text("[]", encoding="utf-8")

    def _fake_aws(args, **kwargs):
        head = " ".join(args[:2])
        if head == "s3 cp":
            return True, "", ""
        if head == "glue start-job-run":
            return True, {"JobRunId": "jr_1"}, ""
        return True, {"JobRun": {"JobRunState": "FAILED",
                                 "ErrorMessage": "SystemExit: --source_path"}}, ""

    monkeypatch.setattr(seed_engine, "_aws", _fake_aws)
    result = seed_engine.seed(str(tmp_path), str(fixture), execute=True)
    assert result["ok"] is False
    assert "SystemExit: --source_path" in result["error"]


# --- Adopt --------------------------------------------------------------------------------

_BROWNFIELD = '''
resource "aws_s3_bucket" "legacy" {
  bucket = "acme-legacy"
}

resource "aws_iam_role_policy" "wide" {
  name   = "wide"
  role   = "some-role"
  policy = jsonencode({
    Statement = [{ Effect = "Allow", Action = "s3:*", Resource = "*" }]
  })
}
'''


def test_adopt_inventories_without_touching_anything(tmp_path):
    (tmp_path / "main.tf").write_text(_BROWNFIELD, encoding="utf-8")
    before = sorted(os.listdir(tmp_path))

    result = adopt_engine.adopt(str(tmp_path))

    assert result["inventory"]["resources"] == 2
    assert result["inventory"]["by_type"]["aws_s3_bucket"] == 1
    assert sorted(os.listdir(tmp_path)) == before, "adopt without --anchor must write nothing"


def test_adopt_flags_stateful_types_because_destroy_means_data_loss(tmp_path):
    (tmp_path / "main.tf").write_text(_BROWNFIELD, encoding="utf-8")
    result = adopt_engine.adopt(str(tmp_path))
    assert "aws_s3_bucket" in result["inventory"]["stateful"]
    assert "DATA LOSS" in adopt_engine.format_result(result)


def test_adopt_reports_sec_findings_and_does_not_call_that_ok(tmp_path):
    """A brownfield directory with a wildcard policy is not adopted successfully -- the
    production gate blocks on it, so saying otherwise sets up a surprise later."""
    (tmp_path / "main.tf").write_text(_BROWNFIELD, encoding="utf-8")
    result = adopt_engine.adopt(str(tmp_path))
    assert result["ok"] is False
    assert result["blocking"]
    assert any("SEC" in step or "optimize_analyzer" in step for step in result["next_steps"])


def test_anchoring_is_opt_in(tmp_path):
    """Anchoring claims the current files are the reviewed starting point. Doing it during a
    look-around would silently bless the wildcard policy the scan is about to report."""
    (tmp_path / "main.tf").write_text('resource "aws_sns_topic" "t" { name = "t" }',
                                      encoding="utf-8")
    assert adopt_engine.adopt(str(tmp_path))["baseline"] is None
    assert not (tmp_path / ".minus").exists()

    result = adopt_engine.adopt(str(tmp_path), anchor=True)
    assert result["baseline"] is not None
    assert (tmp_path / ".minus").is_dir()


def test_adopt_notices_local_state(tmp_path):
    (tmp_path / "main.tf").write_text('resource "aws_sns_topic" "t" { name = "t" }',
                                      encoding="utf-8")
    result = adopt_engine.adopt(str(tmp_path))
    assert result["inventory"]["backends"] == []
    assert any("backend" in step.lower() for step in result["next_steps"])


def test_adopt_on_an_empty_directory_says_so(tmp_path):
    result = adopt_engine.adopt(str(tmp_path))
    assert result["ok"] is False
    assert "nothing to adopt" in result["error"]


# --- MINUS-115 / 133: the PR reviewer ------------------------------------------------------

def test_pr_reviewer_is_packaged_and_never_applies():
    """The reviewer plans. If `apply` ever appears in it, the review path has grown the
    ability to change infrastructure without the environment gate."""
    action = open(os.path.join(_ROOT, ".github", "actions", "pr-reviewer", "action.yml"),
                  encoding="utf-8").read()
    assert "minusctl gate verify" in action
    assert "minusctl gate plan" in action
    assert "minusctl gate apply" not in action


def test_pr_reviewer_refuses_to_invent_a_cost():
    """A plausible-looking made-up figure in a PR comment is worse than no figure, because
    reviewers believe it.

    The guarantee moved from the action's inline bash into comment.py with MINUS-144; it is
    asserted in depth in tests/test_pr_reviewer.py, and kept here so the rule survives even
    if that file is ever narrowed."""
    renderer = open(os.path.join(_ROOT, ".github", "actions", "pr-reviewer", "comment.py"),
                    encoding="utf-8").read()
    assert "unavailable" in renderer
    assert "bcm_pricing_calculator.py" in renderer


def test_pr_workflow_uses_pull_request_not_pull_request_target():
    """pull_request_target runs with the base repo's secrets against the fork's code, which
    hands any fork author the OIDC role."""
    workflow = open(os.path.join(_ROOT, ".github", "workflows", "pr-review.yml"),
                    encoding="utf-8").read()
    # Comments are stripped first: the file explains WHY pull_request_target is wrong, and
    # a naive substring check would fail on its own rationale.
    directives = chr(10).join(line for line in workflow.splitlines()
                           if not line.lstrip().startswith("#"))
    assert "pull_request_target" not in directives
    assert "pull_request:" in directives
    # A blocked review has to make the check red, not just leave a comment.
    assert "verdict != 'pass'" in workflow
