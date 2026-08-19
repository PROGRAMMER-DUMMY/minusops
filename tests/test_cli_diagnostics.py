"""
MINUS-157..160: fuzzy run matching, stage interception, and the 3-part error shape.

The reader of these messages is usually an agent. It cannot infer "run the previous step" from
a FileNotFoundError, so what is asserted here is that every failure carries a literal,
copy-pasteable command -- not just that it failed.
"""
import json
import os

import pytest

import cli_diagnostics as cd
import minusctl


def _run_dir(tmp_path, run_id, requirements=False, adr=False, terraform=False):
    root = tmp_path / "runs" / run_id
    root.mkdir(parents=True)
    (root / "run.json").write_text(json.dumps({
        "run_id": run_id, "root": str(root), "terraform_dir": str(root / "terraform"),
        "created_at": f"2026-08-19T00:00:00Z",
    }), encoding="utf-8")
    if requirements:
        (root / "requirements.json").write_text("{}", encoding="utf-8")
    if adr:
        (root / "architecture_decision.json").write_text("{}", encoding="utf-8")
    if terraform:
        (root / "terraform").mkdir()
        (root / "terraform" / "main.tf").write_text("# hcl\n", encoding="utf-8")
    return root


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point the runs registry at a tmp workspace so these tests never read the real runs/."""
    monkeypatch.setenv("MINUSOPS_OUTPUT_DIR", str(tmp_path))
    import runs
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    return tmp_path


# --- MINUS-160: the error shape -------------------------------------------------------------

def test_error_has_all_three_parts_in_order():
    body = cd.format_agent_error("it broke", "because X", "do-this --now")
    assert body.index("WHAT FAILED") < body.index("WHY IT FAILED") < body.index("ACTION REQUIRED")
    for part in ("it broke", "because X", "do-this --now"):
        assert part in body


def test_the_action_is_a_literal_command_not_a_placeholder_sentence():
    """A "fix" that still needs the reader to substitute something is a fourth problem, not a
    solution."""
    body = cd.format_agent_error("t", "r", "python core/reporting/minusctl.py next --run abc")
    action = body.split("ACTION REQUIRED:")[1]
    assert "python core/reporting/minusctl.py next --run abc" in action


def test_multiple_commands_are_each_on_their_own_line():
    body = cd.format_agent_error("t", "r", ["first --cmd", "second --cmd"])
    assert "first --cmd" in body and "second --cmd" in body
    assert body.count("\n       ") >= 2


def test_context_is_rendered_when_supplied():
    body = cd.format_agent_error("t", "r", "c", {"run": "abc123"})
    assert "run: abc123" in body


def test_fail_writes_to_stderr_and_returns_two(capsys):
    assert cd.fail("t", "r", "c") == 2
    captured = capsys.readouterr()
    assert "WHAT FAILED" in captured.err
    assert captured.out == ""


# --- MINUS-157: fuzzy matching --------------------------------------------------------------

def test_a_transposed_digit_suggests_the_real_run(workspace):
    _run_dir(workspace, "20260818-085523-requirements-first")
    _run_dir(workspace, "20260817-141132-requirements-first")
    assert cd.suggest_runs("20260818-085532-requirements-first")[0] == \
        "20260818-085523-requirements-first"


def test_a_truncated_id_is_matched_as_a_prefix_not_only_by_edit_distance(workspace):
    """Run ids are timestamps, so the common "typo" is a truncation -- a prefix, not an
    edit-distance neighbour."""
    _run_dir(workspace, "20260819-101423-synthesized")
    assert cd.suggest_runs("20260819-1014") == ["20260819-101423-synthesized"]


def test_nothing_close_suggests_nothing(workspace):
    """difflib will happily "match" two ids sharing a date prefix. A wrong suggestion sends an
    agent to the wrong workspace, which is worse than no suggestion."""
    _run_dir(workspace, "20260818-085523-requirements-first")
    assert cd.suggest_runs("completely-unrelated") == []


def test_recent_runs_reports_the_stage_each_one_actually_reached(workspace):
    _run_dir(workspace, "20260819-000003-c", requirements=True, adr=True, terraform=True)
    _run_dir(workspace, "20260819-000002-b", requirements=True, adr=True)
    _run_dir(workspace, "20260819-000001-a", requirements=True)
    stages = dict(cd.recent_runs())
    assert stages["20260819-000003-c"] == "synthesized"
    assert stages["20260819-000002-b"] == "decided"
    assert stages["20260819-000001-a"] == "requirements"


def test_stage_comes_from_artifacts_not_a_recorded_status(workspace):
    """A status field records what a command CLAIMED. The files record what happened, and the
    two diverge exactly when someone needs this listing most."""
    root = _run_dir(workspace, "20260819-000009-liar", requirements=True)
    meta = json.loads((root / "run.json").read_text(encoding="utf-8"))
    meta["status"] = "synthesized"
    (root / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    assert dict(cd.recent_runs())["20260819-000009-liar"] == "requirements"


def test_an_unresolvable_run_names_the_suggestion_in_the_fix_command(workspace, capsys):
    _run_dir(workspace, "20260818-085523-requirements-first")
    with pytest.raises(SystemExit) as excinfo:
        minusctl._run_by_id_or_latest("20260818-085532-requirements-first", command="readiness")
    message = str(excinfo.value)
    assert "WHAT FAILED" in message
    assert "readiness --run 20260818-085523-requirements-first" in message


def test_no_runs_at_all_points_at_create(workspace):
    with pytest.raises(SystemExit) as excinfo:
        minusctl._latest_run_or_exit()
    assert "minusctl.py create" in str(excinfo.value)


# --- MINUS-158: prerequisite interception ---------------------------------------------------

def test_missing_requirements_names_step_one(workspace):
    root = _run_dir(workspace, "20260819-000001-bare")
    gap = cd.missing_prerequisite(str(root), "20260819-000001-bare")
    assert gap["step"] == 1 and gap["artifact"] == "requirements.json"
    assert "minusctl.py create" in gap["command"]


def test_missing_adr_names_step_two(workspace):
    root = _run_dir(workspace, "20260819-000002-req", requirements=True)
    gap = cd.missing_prerequisite(str(root), "20260819-000002-req")
    assert gap["step"] == 2 and gap["artifact"] == "architecture_decision.json"


def test_missing_terraform_names_step_three(workspace):
    root = _run_dir(workspace, "20260819-000003-adr", requirements=True, adr=True)
    gap = cd.missing_prerequisite(str(root), "20260819-000003-adr")
    assert gap["step"] == 3 and gap["artifact"] == "terraform"
    assert "synthesizer.py" in gap["command"]


def test_an_empty_terraform_directory_is_not_synthesis(workspace):
    """mkdir is not generation. A directory with no .tf in it would otherwise read as done."""
    root = _run_dir(workspace, "20260819-000004-empty", requirements=True, adr=True)
    (root / "terraform").mkdir()
    assert cd.missing_prerequisite(str(root), "x")["artifact"] == "terraform"


def test_the_first_gap_is_reported_not_the_last(workspace):
    """Telling someone their synthesis is missing when they have no requirements yet sends
    them to the wrong end of the pipeline."""
    root = _run_dir(workspace, "20260819-000005-none")
    assert cd.missing_prerequisite(str(root), "x")["step"] == 1


def test_a_complete_run_has_no_gap(workspace):
    root = _run_dir(workspace, "20260819-000006-full", requirements=True, adr=True,
                    terraform=True)
    assert cd.missing_prerequisite(str(root), "x") is None


def test_up_to_bounds_how_far_the_check_looks(workspace):
    """`decision` is the command that WRITES step 2, so requiring step 2 would make it
    impossible to run."""
    root = _run_dir(workspace, "20260819-000007-req", requirements=True)
    assert cd.missing_prerequisite(str(root), "x", up_to=1) is None
    assert cd.missing_prerequisite(str(root), "x", up_to=2)["step"] == 2


def test_readiness_is_intercepted_on_an_incomplete_run(workspace):
    _run_dir(workspace, "20260819-000008-bare")
    with pytest.raises(SystemExit) as excinfo:
        minusctl._run_by_id_or_latest("20260819-000008-bare", command="readiness")
    message = str(excinfo.value)
    assert "needs step 1 (Requirements)" in message
    assert "minusctl.py create" in message


def test_decision_is_not_blocked_by_its_own_missing_output(workspace):
    _run_dir(workspace, "20260819-000010-req", requirements=True)
    run = minusctl._run_by_id_or_latest("20260819-000010-req", command="decision")
    assert run["run_id"] == "20260819-000010-req"


# --- MINUS-158: plan / approval steps -------------------------------------------------------

def test_no_plan_record_names_step_four(tmp_path):
    tf = tmp_path / "terraform"
    tf.mkdir()
    gap = cd.missing_plan_prerequisite(str(tf))
    assert gap["step"] == 4
    assert "plan_gate.py plan --dir" in gap["command"]


def test_a_planned_but_unapproved_directory_names_step_five(tmp_path, monkeypatch):
    """Approval is <plan_hash>.json in the approvals dir, NOT an `approved` key on the pending
    record -- reading the wrong one reports an approved plan as unapproved, and vice versa."""
    import plan_gate
    tf = tmp_path / "terraform"
    tf.mkdir()
    pending = tmp_path / "pending_plan.json"
    pending.write_text(json.dumps({"plan_hash": "a" * 64, "dir": str(tf)}), encoding="utf-8")
    monkeypatch.setattr(plan_gate, "_pending_path", lambda d: str(pending))
    monkeypatch.setattr(plan_gate, "_approved_path",
                        lambda d, h: str(tmp_path / "approvals" / f"{h}.json"))

    gap = cd.missing_plan_prerequisite(str(tf))
    assert gap["step"] == 5
    assert "plan_gate.py approve --dir" in gap["command"]

    os.makedirs(tmp_path / "approvals")
    (tmp_path / "approvals" / f"{'a' * 64}.json").write_text("{}", encoding="utf-8")
    assert cd.missing_plan_prerequisite(str(tf)) is None


# --- MINUS-159: help text -------------------------------------------------------------------

def test_epilog_carries_examples_requirements_and_the_next_step():
    text = cd.epilog(["cmd --flag"], requires=("a.json",), produces=("b.json",),
                     next_step="do the next thing")
    for expected in ("examples:", "cmd --flag", "requires:", "a.json", "produces:", "b.json",
                     "next: do the next thing"):
        assert expected in text


def test_enriched_parsers_preserve_their_formatting():
    """argparse reflows an epilog into one paragraph unless the formatter preserves it, which
    would make the examples un-copy-pasteable -- the only reason they are there."""
    import argparse
    parser = argparse.ArgumentParser()
    minusctl._rich(parser, ["a --b", "c --d"], requires=("x",))
    assert parser.formatter_class is argparse.RawDescriptionHelpFormatter
    assert "a --b\n" in parser.format_help()
