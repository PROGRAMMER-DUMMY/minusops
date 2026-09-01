"""
Issue #4 / decision #18 -- how policy coverage grows without becoming a liability.

rules.rego covers 13 rule IDs; AWS exposes 1000+ resource types. Hand-writing rules does
not scale, so agents propose them. But a GENERATED RULE THAT SILENTLY PERMITS is the
failure mode to design against: an agent that writes a rule which never fires makes a
resource type look reviewed when nothing checks it.

So a proposed rule is WARN-ONLY until a human explicitly promotes it, promotion is a
recorded act with provenance, and nothing an agent writes can ever weaken the gate.
"""
import json

import pytest
import rule_stages


def _registry(tmp_path, **stages):
    path = tmp_path / "rule_stages.json"
    path.write_text(json.dumps({"rules": stages}, indent=2), encoding="utf-8")
    return str(path)


def test_an_unknown_rule_is_warn_only_not_blocking(tmp_path):
    """The default that matters. A rule nobody promoted must never block."""
    reg = _registry(tmp_path)
    assert rule_stages.stage_of("SEC-99", registry_path=reg) == "warn"
    assert rule_stages.is_blocking("SEC-99", registry_path=reg) is False


def test_a_promoted_rule_blocks(tmp_path):
    reg = _registry(tmp_path, **{"SEC-01": {"stage": "blocking", "promoted_by": "alice"}})
    assert rule_stages.is_blocking("SEC-01", registry_path=reg) is True


def test_partitioning_findings_keeps_unpromoted_ones_out_of_the_blocking_set(tmp_path):
    reg = _registry(tmp_path, **{"SEC-01": {"stage": "blocking", "promoted_by": "alice"}})
    findings = [
        {"id": "SEC-01", "resource": "aws_s3_bucket.a"},
        {"id": "SEC-99", "resource": "aws_s3_bucket.b"},   # agent-authored, unpromoted
    ]
    result = rule_stages.partition(findings, registry_path=reg)
    assert [f["id"] for f in result["blocking"]] == ["SEC-01"]
    assert [f["id"] for f in result["warning"]] == ["SEC-99"]


def test_promotion_records_who_and_why(tmp_path):
    """Promotion is the moment a generated rule gains teeth. It must be attributable."""
    reg = _registry(tmp_path)
    rule_stages.promote("SEC-42", promoted_by="alice@corp",
                        reason="reviewed against AWS docs, fires on the 3 known cases",
                        registry_path=reg)
    entry = json.loads(open(reg, encoding="utf-8").read())["rules"]["SEC-42"]
    assert entry["stage"] == "blocking"
    assert entry["promoted_by"] == "alice@corp"
    assert entry["reason"].startswith("reviewed")
    assert entry["promoted_at"]


def test_promotion_without_an_approver_is_refused(tmp_path):
    """An unattributable promotion is how a generated rule silently gains teeth."""
    reg = _registry(tmp_path)
    with pytest.raises(ValueError):
        rule_stages.promote("SEC-42", promoted_by="", reason="x", registry_path=reg)
    with pytest.raises(ValueError):
        rule_stages.promote("SEC-42", promoted_by="alice", reason="", registry_path=reg)


def test_demote_returns_a_rule_to_warn_only(tmp_path):
    """A promoted rule that turns out wrong must be reversible without editing the .rego."""
    reg = _registry(tmp_path, **{"SEC-01": {"stage": "blocking", "promoted_by": "alice"}})
    rule_stages.demote("SEC-01", demoted_by="bob@corp", reason="false positives on X",
                       registry_path=reg)
    assert rule_stages.is_blocking("SEC-01", registry_path=reg) is False
    entry = json.loads(open(reg, encoding="utf-8").read())["rules"]["SEC-01"]
    assert entry["demoted_by"] == "bob@corp"


def test_a_missing_registry_means_everything_warns(tmp_path):
    """Fail-SAFE, not fail-closed: a missing registry must not make every rule blocking
    (which would wedge every plan), nor silently drop findings."""
    missing = str(tmp_path / "nope.json")
    assert rule_stages.stage_of("SEC-01", registry_path=missing) == "warn"
    result = rule_stages.partition([{"id": "SEC-01"}], registry_path=missing)
    assert result["blocking"] == []
    assert len(result["warning"]) == 1


def test_a_corrupt_registry_does_not_grant_blocking_status(tmp_path):
    """Garbage must not be readable as 'everything is promoted'."""
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert rule_stages.is_blocking("SEC-01", registry_path=str(path)) is False


# --- the gate half: promotion is what gives a rule teeth ---------------------------
import plan_gate


def _rego(findings):
    return {"evaluation_failed": False, "findings": findings, "reason": None}


def test_an_unpromoted_rule_never_blocks_a_plan(tmp_path, capsys):
    """Ships safe: the seeded registry has every rule at warn, so this changes nothing
    about what currently applies."""
    reg = _registry(tmp_path)
    blocked = plan_gate._reject_if_promoted_policy_violated(
        str(tmp_path), _rego([{"id": "SEC-01", "resource": "aws_s3_bucket.a",
                               "title": "public access"}]),
        False, registry_path=reg)
    assert blocked is False


def test_a_promoted_rule_blocks_the_plan(tmp_path, capsys):
    reg = _registry(tmp_path, **{"SEC-01": {"stage": "blocking", "promoted_by": "alice"}})
    blocked = plan_gate._reject_if_promoted_policy_violated(
        str(tmp_path), _rego([{"id": "SEC-01", "resource": "aws_s3_bucket.a",
                               "title": "public access"}]),
        False, registry_path=reg)
    assert blocked is True
    err = capsys.readouterr().err
    assert "SEC-01" in err and "aws_s3_bucket.a" in err


def test_a_failed_evaluation_does_not_block(tmp_path):
    """OPA missing is not a policy violation. Blocking here would make an optional tool a
    hard dependency of every plan."""
    reg = _registry(tmp_path, **{"SEC-01": {"stage": "blocking"}})
    assert plan_gate._reject_if_promoted_policy_violated(
        str(tmp_path), {"evaluation_failed": True, "reason": "opa_not_found", "findings": []},
        False, registry_path=reg) is False
