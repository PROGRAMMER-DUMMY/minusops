"""
Increment 6: a clean report must not be mistakable for a verified one.

Today the reviewer cannot tell whether the gate checked a resource type and found nothing
wrong, or never had a rule for it at all. Both render as a green report. That ambiguity is
the residual risk of agent-generated infrastructure, so the report states its own limits --
the same honesty pattern coverage_audit.classify() already applies to pricing.
"""
import verification_coverage as vc

PLAN = {
    "resource_changes": [
        {"address": "aws_s3_bucket.a", "mode": "managed", "type": "aws_s3_bucket",
         "change": {"actions": ["create"]}},
        {"address": "aws_s3_bucket.b", "mode": "managed", "type": "aws_s3_bucket",
         "change": {"actions": ["create"]}},
        {"address": "aws_dynamodb_table.x", "mode": "managed", "type": "aws_dynamodb_table",
         "change": {"actions": ["create"]}},
        {"address": "data.aws_caller_identity.me", "mode": "data",
         "type": "aws_caller_identity", "change": {"actions": ["read"]}},
    ]
}


def test_classify_reports_one_row_per_managed_resource_type():
    cov = vc.classify(PLAN, findings=[], claims_by_type={})
    assert {r["resource_type"] for r in cov["types"]} == {"aws_s3_bucket", "aws_dynamodb_table"}
    assert cov["type_count"] == 2


def test_a_type_with_a_firing_rule_is_marked_rule_covered():
    findings = [{"id": "SEC-01", "resource": "aws_s3_bucket.a"}]
    cov = vc.classify(PLAN, findings=findings, claims_by_type={})
    row = next(r for r in cov["types"] if r["resource_type"] == "aws_s3_bucket")
    assert row["state"] == "rule_covered"
    assert row["rule_ids"] == ["SEC-01"]


def test_a_type_with_only_claims_is_marked_claim_informed_not_verified():
    """Claims inform; they are not verification. The label must not imply a rule ran."""
    cov = vc.classify(PLAN, findings=[], claims_by_type={"aws_dynamodb_table": [
        {"claim_text": "x", "observed_at": "2026-07-01T00:00:00Z"}]})
    row = next(r for r in cov["types"] if r["resource_type"] == "aws_dynamodb_table")
    assert row["state"] == "claim_informed"
    assert row["claim_count"] == 1


def test_a_type_with_neither_is_marked_unchecked():
    cov = vc.classify(PLAN, findings=[], claims_by_type={})
    row = next(r for r in cov["types"] if r["resource_type"] == "aws_dynamodb_table")
    assert row["state"] == "unchecked"


def test_coverage_ratio_counts_only_rule_covered_types():
    """The success metric. Claims must not inflate it -- otherwise coverage looks like it
    grew when only memory did."""
    cov = vc.classify(
        PLAN,
        findings=[{"id": "SEC-01", "resource": "aws_s3_bucket.a"}],
        claims_by_type={"aws_dynamodb_table": [{"claim_text": "x", "observed_at": "t"}]})
    assert cov["rule_covered_count"] == 1
    assert cov["type_count"] == 2
    assert cov["coverage_ratio"] == 0.5


def test_empty_plan_does_not_divide_by_zero():
    cov = vc.classify({"resource_changes": []}, findings=[], claims_by_type={})
    assert cov["type_count"] == 0
    assert cov["coverage_ratio"] is None


def test_manifest_carries_verification_coverage(tmp_path, monkeypatch):
    """End-to-end: the disclosure must reach the artifact a reviewer actually opens."""
    import reporter
    monkeypatch.setattr(reporter, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(reporter, "REPORTS", str(tmp_path / "artifacts" / "reports"))
    monkeypatch.setenv("MINUS_BCM_AUTO", "0")
    tf = tmp_path / "tf"
    tf.mkdir()
    out = reporter._generate_report_bundle(str(tf), PLAN, template="t")

    import json as _json
    manifest = _json.loads((__import__("pathlib").Path(out) / "manifest.json").read_text(encoding="utf-8"))
    cov = manifest["verification_coverage"]
    assert cov["type_count"] == 2
    assert {r["resource_type"] for r in cov["types"]} == {"aws_s3_bucket", "aws_dynamodb_table"}


def test_report_html_states_coverage_so_silence_is_not_read_as_verified(tmp_path, monkeypatch):
    """C5: the disclosure must reach the PDF's print source, not just the manifest."""
    import reporter
    monkeypatch.setattr(reporter, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(reporter, "REPORTS", str(tmp_path / "artifacts" / "reports"))
    monkeypatch.setenv("MINUS_BCM_AUTO", "0")
    tf = tmp_path / "tf"
    tf.mkdir()
    out = reporter._generate_report_bundle(str(tf), PLAN, template="t")
    doc = (__import__("pathlib").Path(out) / "report.html").read_text(encoding="utf-8")
    assert "Verification coverage" in doc
    assert "aws_dynamodb_table" in doc
    assert "No rule, no claims" in doc, "an unchecked type must be labelled, not omitted"
