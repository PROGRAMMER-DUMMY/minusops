"""
Sprint 1 (MINUS-143/144/145): the PR reviewer action, its comment renderer, and the merge gate.

comment.py is a pure renderer -- it reads files and returns Markdown -- which is exactly what
makes these assertions possible without a workflow runner.
"""
import json
import os
import sys

import pytest
import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, ".github", "actions", "pr-reviewer"))

import comment as pr_comment  # noqa: E402

_ACTION = os.path.join(_ROOT, ".github", "actions", "pr-reviewer", "action.yml")
_DEPLOY = os.path.join(_ROOT, ".github", "workflows", "deploy.yml")


def _yaml(path):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


# --- MINUS-144: the sticky comment ----------------------------------------------------------

def test_cost_uses_the_nested_estimate_not_the_pre_usage_create_block():
    """`create.totalCost` is the estimate BEFORE usage lines attach and reads 0.0 for every
    stack. Reporting it would put a $0 cost on a $430/mo plan -- the same false green the
    reflector's cost gate was fixed for."""
    doc = {"create": {"totalCost": 0.0}, "estimate": {"totalCost": 430.29}}
    body = "\n".join(pr_comment.cost_section(doc, budget_usd=500))
    assert "$430.29" in body
    assert "$0.00" not in body


def test_cost_diff_names_the_direction():
    over = "\n".join(pr_comment.cost_section({"estimate": {"totalCost": 600.0}}, budget_usd=500))
    under = "\n".join(pr_comment.cost_section({"estimate": {"totalCost": 430.29}}, budget_usd=500))
    assert "+$100.00" in over and "OVER" in over
    assert "-$69.71" in under and "under" in under


def test_absent_cost_evidence_is_never_rendered_as_zero():
    """A plausible-looking figure in a PR comment gets believed. A blank does not."""
    body = "\n".join(pr_comment.cost_section(None))
    assert "unavailable" in body
    assert "bcm_pricing_calculator.py" in body
    assert "$0" not in body


def test_reflector_unknown_is_not_shown_as_a_pass():
    result = {"blocked": False, "summary": {"pass": 4, "blocked": 0, "unknown": 1},
              "gates": [{"gate": "G4_cost", "status": "unknown", "detail": "no evidence"}]}
    body = "\n".join(pr_comment.reflector_section(result))
    assert "UNKNOWN" in body
    assert "`unknown` is not a pass" in body


def test_blocked_reflector_drives_the_headline(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"blocked": True, "summary": {"pass": 4, "blocked": 1,
                                                             "unknown": 0}, "gates": []}),
                    encoding="utf-8")
    body = pr_comment.render(reflector_json=str(path), tf_dir="x/terraform")
    assert "MinusOps review: BLOCKED" in body


def test_plan_hash_is_rendered_in_full_for_sign_off():
    """A truncated digest is unusable both for the reviewer signing off and for the merge gate
    that recomputes it."""
    digest = "5cad83d90a8a47021cb11b843962f6ee46ccc81f170f41e2e0acb6a34c372d3a"
    body = pr_comment.render(plan_hash=digest)
    assert digest in body
    body_none = pr_comment.render()
    assert "bound to a reviewable plan" in body_none


def test_comment_carries_a_stable_marker_so_it_stays_sticky():
    assert pr_comment.MARKER in pr_comment.render()


def test_pipe_in_a_gate_detail_does_not_break_the_table():
    result = {"blocked": False, "summary": {"pass": 1, "blocked": 0, "unknown": 0},
              "gates": [{"gate": "G2", "status": "pass", "detail": "a | b"}]}
    row = [l for l in pr_comment.reflector_section(result) if l.startswith("| `G2`")][0]
    assert r"a \| b" in row, "a literal pipe in a detail must be escaped, not split the row"


# --- MINUS-143: the action ------------------------------------------------------------------

def test_action_runs_all_four_stages():
    action = open(_ACTION, encoding="utf-8").read()
    for stage in ("minusctl gate verify", "minusctl gate plan",
                  "bcm_pricing_calculator.py prepare", "reflector.py"):
        assert stage in action, stage


def test_action_never_applies():
    """If apply ever appears here, the review path has gained the ability to change
    infrastructure without the environment gate."""
    assert "minusctl gate apply" not in open(_ACTION, encoding="utf-8").read()


def test_action_exposes_the_plan_hash_for_the_merge_gate():
    assert "plan_hash" in _yaml(_ACTION)["outputs"]


def test_plan_hash_comes_from_the_gate_record_not_scraped_log_text():
    """The log prints a truncated prefix; the merge gate compares full digests."""
    action = open(_ACTION, encoding="utf-8").read()
    assert "pending_plan.json" in action


# --- MINUS-145: the merge gate --------------------------------------------------------------

def test_deploy_reverifies_the_approved_hash_before_apply():
    deploy = _yaml(_DEPLOY)
    steps = deploy["jobs"]["deploy"]["steps"]
    names = [s.get("name", "") for s in steps]
    verify_at = names.index("Re-verify the approved plan hash")
    apply_at = next(i for i, n in enumerate(names) if n.startswith("Apply"))
    assert verify_at < apply_at, "the hash check must run before apply, not after"


def test_mismatched_hash_fails_the_job():
    deploy = open(_DEPLOY, encoding="utf-8").read()
    assert 'if [ "$APPROVED" != "$COMPUTED" ]' in deploy
    assert "plan hash mismatch" in deploy


def test_an_unreviewed_deploy_is_allowed_but_never_silent():
    deploy = open(_DEPLOY, encoding="utf-8").read()
    # YAML 1.1 parses a bare `on:` key as the boolean True, so it is not reachable as "on".
    triggers = _yaml(_DEPLOY)[True]
    assert "approved_plan_hash" in triggers["workflow_dispatch"]["inputs"]
    assert "bound to no PR sign-off" in deploy
