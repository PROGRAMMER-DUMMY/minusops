"""
Bi-directional visual reconciliation (PRD v13 FR-05).

This is the most dangerous surface in the product. Everywhere else the console is a reader;
here a drag on a canvas rewrites `terraform/main.tf`. The entire value of the deploy gate --
that infrastructure changes are reviewed HCL bound to an approved plan hash -- is undone if
a mouse gesture can edit that HCL quietly.

So the tests that matter are the refusals, and they outnumber the happy path deliberately:

  propose() must never write. Not the tf file, not the decision, not the audit chain.
  confirm(confirmed=False) must never write. A modal that was dismissed is a no.
  A confirmed change must revoke the standing approval, by deleting the approval record --
  the same mechanism the one manual `approval-revoked` in this repo's audit chain used --
  so `gate_status` reports approved: False rather than a second staleness flag that could
  disagree with it.

Depends on: core/architecture/reconciler.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import json
import os

import pytest

import audit_chain
import reconciler


HCL = '''resource "aws_glue_job" "etl" {
  name     = "acme-etl"
  role_arn = aws_iam_role.glue.arn

  default_arguments = {
    "--source_path" = module.storage.gold_bucket_arn
    "--target_path" = module.storage.silver_bucket_arn
  }
}
'''


@pytest.fixture
def run(tmp_path):
    root = tmp_path / "runs" / "acme-run"
    (root / "terraform").mkdir(parents=True)
    (root / "terraform" / "main.tf").write_text(HCL, encoding="utf-8")
    (root / "architecture_decision.json").write_text(
        json.dumps({"architecture": "lakehouse", "selected_modules": ["storage-medallion-s3"]}),
        encoding="utf-8")
    return str(root)


def _change():
    return {
        "kind": "reconnect",
        "target": "aws_glue_job.etl",
        "attribute": "--source_path",
        "from": "module.storage.gold_bucket_arn",
        "to": "module.storage.bronze_bucket_arn",
    }


# --- propose() is inert -----------------------------------------------------------------

def test_propose_never_touches_the_terraform_file(run):
    before = open(os.path.join(run, "terraform", "main.tf"), encoding="utf-8").read()

    reconciler.propose(run, _change(), author="shubh")

    assert open(os.path.join(run, "terraform", "main.tf"), encoding="utf-8").read() == before


def test_propose_never_touches_the_decision_record(run):
    path = os.path.join(run, "architecture_decision.json")
    before = open(path, encoding="utf-8").read()

    reconciler.propose(run, _change(), author="shubh")

    assert open(path, encoding="utf-8").read() == before


def test_propose_writes_no_audit_entry(run, tmp_path):
    audit = tmp_path / "audit.jsonl"

    reconciler.propose(run, _change(), author="shubh", audit_path=str(audit))

    assert not audit.exists(), "a proposal is not an event; nothing happened yet"


# --- The review modal's contents (FR-05.2) ----------------------------------------------

def test_the_proposal_carries_everything_the_modal_must_show(run):
    proposal = reconciler.propose(run, _change(), author="shubh")

    assert proposal["author"] == "shubh"
    assert proposal["at"], "an unattributed infrastructure change is not reviewable"
    assert proposal["summary"], "plain-English change summary"
    assert proposal["warnings"], "safety and lineage warning"
    assert proposal["diff"], "side-by-side HCL diff"


def test_the_summary_names_both_sides_of_the_change_in_plain_english(run):
    summary = reconciler.propose(run, _change(), author="shubh")["summary"]

    assert "gold_bucket_arn" in summary and "bronze_bucket_arn" in summary
    assert "aws_glue_job.etl" in summary


def test_the_diff_is_a_real_unified_diff_of_the_file(run):
    diff = reconciler.propose(run, _change(), author="shubh")["diff"]

    assert "--- " in diff and "+++ " in diff
    assert "-" in diff and "+" in diff
    assert "gold_bucket_arn" in diff and "bronze_bucket_arn" in diff


def test_a_change_that_matches_nothing_is_refused_rather_than_silently_applied(run):
    """A canvas edit referring to HCL that does not exist means the canvas and the code have
    already diverged. Writing anything at that point would be guessing."""
    change = dict(_change(), **{"from": "module.storage.nonexistent_arn"})

    proposal = reconciler.propose(run, change, author="shubh")

    assert proposal["applicable"] is False
    assert proposal["diff"] == ""
    assert "not found" in proposal["reason"].lower()


# --- confirm() is gated -----------------------------------------------------------------

def test_an_unconfirmed_proposal_changes_nothing(run):
    before = open(os.path.join(run, "terraform", "main.tf"), encoding="utf-8").read()
    proposal = reconciler.propose(run, _change(), author="shubh")

    result = reconciler.confirm(proposal, confirmed=False)

    assert result["applied"] is False
    assert open(os.path.join(run, "terraform", "main.tf"), encoding="utf-8").read() == before


def test_confirmation_must_be_explicitly_true_not_merely_truthy(run):
    """`confirmed="no"` is a string and would pass a truthiness check. On this surface the
    default must be refusal for anything that is not exactly True."""
    proposal = reconciler.propose(run, _change(), author="shubh")

    for value in ("no", "false", 0, None, "", "yes", 1):
        result = reconciler.confirm(proposal, confirmed=value)
        assert result["applied"] is False, f"{value!r} was treated as confirmation"


def test_a_confirmed_change_rewrites_the_hcl(run, tmp_path):
    proposal = reconciler.propose(run, _change(), author="shubh")

    result = reconciler.confirm(proposal, confirmed=True, audit_path=str(tmp_path / "a.jsonl"))
    text = open(os.path.join(run, "terraform", "main.tf"), encoding="utf-8").read()

    assert result["applied"] is True
    assert "bronze_bucket_arn" in text
    assert "gold_bucket_arn" not in text


def test_a_confirmed_change_records_the_reconciliation_on_the_decision(run, tmp_path):
    proposal = reconciler.propose(run, _change(), author="shubh")

    reconciler.confirm(proposal, confirmed=True, audit_path=str(tmp_path / "a.jsonl"))
    decision = json.load(open(os.path.join(run, "architecture_decision.json"), encoding="utf-8"))

    assert decision["reconciliations"], "the decision must carry its own edit history"
    assert decision["reconciliations"][-1]["author"] == "shubh"


def test_a_confirmed_change_is_audited_under_the_declared_action(run, tmp_path):
    audit = tmp_path / "audit.jsonl"
    proposal = reconciler.propose(run, _change(), author="shubh")

    reconciler.confirm(proposal, confirmed=True, audit_path=str(audit))
    records = [json.loads(line) for line in open(audit, encoding="utf-8") if line.strip()]

    assert records, "a confirmed infrastructure edit must be in the audit chain"
    assert records[-1]["action"] == reconciler.AUDIT_ACTION
    assert "aws_glue_job.etl" in records[-1]["details"]


def test_a_confirmed_change_is_chained_not_just_appended(run, tmp_path):
    """In the file is not in the chain.

    The test above only proved the record LANDED. Writing it with a bare `open(..., "a")`
    passes that and still leaves an entry with no `prev_hash`/`entry_hash`, which breaks
    every link after it -- 85 such entries were found in this repo's own audit.jsonl, and
    `audit verify` exited 1 with 218 errors. So the assertion that matters is the verifier's,
    not the reader's.
    """
    audit = tmp_path / "audit.jsonl"
    audit_chain.append(str(audit), {"action": "prior", "operator": "someone"})

    proposal = reconciler.propose(run, _change(), author="shubh")
    reconciler.confirm(proposal, confirmed=True, audit_path=str(audit))

    ok, errors = audit_chain.verify(str(audit))
    assert ok, f"a reconciliation must not break the chain: {errors}"

    records = [json.loads(line) for line in open(audit, encoding="utf-8") if line.strip()]
    assert records[-1]["prev_hash"] == records[-2]["entry_hash"],         "the entry must link to the one before it"


def test_an_unwritable_audit_path_does_not_fail_a_confirmed_edit(run, tmp_path):
    """The files are already rewritten by the time the audit runs, so a logging failure must
    not be reported as a failed edit -- that would tell an operator nothing happened when
    main.tf had in fact changed."""
    blocker = tmp_path / "occupied"
    blocker.write_text("a file where the log directory needs to be", encoding="utf-8")
    proposal = reconciler.propose(run, _change(), author="shubh")

    result = reconciler.confirm(proposal, confirmed=True,
                                audit_path=str(blocker / "audit.jsonl"))

    assert result["files"], "the edit itself still succeeded"


# --- Plan invalidation (FR-05.3, AC-02) -------------------------------------------------

def test_a_confirmed_change_revokes_the_standing_approval(run, tmp_path, monkeypatch):
    """The approval record is DELETED, so `gate_status` reports approved: False. A separate
    STALE flag would be a second answer to "is this approved" that could disagree with the
    gate -- and the gate is the one that decides whether apply runs."""
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    approval = approvals / "abc123.json"
    approval.write_text(json.dumps({"approver": "shubh", "plan_hash": "abc123"}),
                        encoding="utf-8")
    monkeypatch.setattr(reconciler, "_approval_records",
                        lambda _dir: [str(approval)])

    proposal = reconciler.propose(run, _change(), author="shubh")
    result = reconciler.confirm(proposal, confirmed=True, audit_path=str(tmp_path / "a.jsonl"))

    assert not approval.exists(), "the approval survived a change to the planned source"
    assert result["approvals_revoked"] == 1
    assert result["status"] == reconciler.STALE_PLAN


def test_the_result_tells_the_operator_the_exact_next_command(run, tmp_path):
    proposal = reconciler.propose(run, _change(), author="shubh")

    result = reconciler.confirm(proposal, confirmed=True, audit_path=str(tmp_path / "a.jsonl"))

    assert "minusctl gate plan" in result["next_command"]


# --- Invariants -------------------------------------------------------------------------

def test_the_module_imports_only_the_standard_library_and_core():
    """PRD v13 invariant 4 bans THIRD-PARTY packages, not sibling core modules.

    So the check is not "every import is stdlib" -- that would forbid `audit_chain`, and
    forbidding it is what produced the unchained-entry bug. Every non-stdlib import must
    instead resolve to a file inside `core/`, which proves it ships with this repo rather
    than arriving from pip.
    """
    import ast
    import sys
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tree = ast.parse(open(os.path.join(repo, "core", "architecture", "reconciler.py"),
                          encoding="utf-8").read())

    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    core_modules = {os.path.splitext(f)[0]
                    for _root, _dirs, files in os.walk(os.path.join(repo, "core"))
                    for f in files if f.endswith(".py")}

    foreign = roots - set(sys.stdlib_module_names) - core_modules
    assert not foreign, f"third-party imports: {sorted(foreign)}"
