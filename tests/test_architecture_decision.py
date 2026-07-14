import architecture_decision as archdec


def test_architecture_decision_template_is_incomplete_until_researched():
    record = archdec.template("runs/x/requirements.json")

    ok, missing = archdec.validate(record)

    assert ok is False
    assert "selected_architecture" in missing
    assert "selected_modules (at least one module id)" in missing
    assert "sources (at least one item)" in missing


def test_architecture_decision_complete_record_validates():
    record = {
        "requirements_file": "runs/x/requirements.json",
        "selected_architecture": "AWS managed lakehouse with MWAA orchestration",
        "decision_summary": "Chosen for batch orchestration, data quality, and schema governance.",
        "selected_modules": ["orchestrator-mwaa", "dq-great-expectations", "schema-registry-glue"],
        "alternatives": [
            {"name": "Step Functions", "decision": "rejected", "reason": "Less suitable for existing Airflow DAGs."}
        ],
        "assumptions": ["AWS is the target cloud."],
        "risks": ["MWAA cost must be checked during BCM estimate."],
        "sources": ["AWS MWAA documentation", "Terraform AWS provider registry"],
    }

    ok, missing = archdec.validate(record)

    assert ok is True
    assert missing == []


def test_architecture_decision_editor_builds_record(tmp_path):
    path = tmp_path / "architecture_decision.json"

    archdec.set_summary(
        str(path),
        selected_architecture="AWS managed lakehouse",
        decision_summary="Chosen for governed batch analytics.",
    )
    archdec.add_modules(str(path), ["storage-medallion-s3", "query-athena"])
    archdec.add_alternative(str(path), "Redshift-only", "rejected", "Does not fit lakehouse storage needs.")
    archdec.add_list_item(str(path), "assumptions", "AWS is approved.")
    archdec.add_list_item(str(path), "risks", "Athena cost needs guardrails.")
    record = archdec.add_list_item(str(path), "sources", "Terraform AWS provider registry")

    ok, missing = archdec.validate(record)

    assert ok is True
    assert missing == []
    assert record["selected_modules"] == ["storage-medallion-s3", "query-athena"]


def test_architecture_decision_rejects_unknown_module(tmp_path):
    path = tmp_path / "architecture_decision.json"

    try:
        archdec.add_modules(str(path), ["not-a-module"])
    except ValueError as exc:
        assert "unknown module id" in str(exc)
    else:
        raise AssertionError("expected unknown module rejection")


# ---------------------------------------------------------------------------
# novel_resources (docs/phase6_step1_authoring_scope.md section 1) -- additive field for a
# requirement no existing catalog module covers. Optional at the record level (a record with no
# novel resources needs none), but every entry present is held to the same completeness bar
# `alternatives` already gets.
# ---------------------------------------------------------------------------

def _complete_record(**overrides):
    record = {
        "requirements_file": "runs/x/requirements.json",
        "selected_architecture": "AWS managed lakehouse with MWAA orchestration",
        "decision_summary": "Chosen for batch orchestration, data quality, and schema governance.",
        "selected_modules": ["orchestrator-mwaa"],
        "alternatives": [
            {"name": "Step Functions", "decision": "rejected", "reason": "Less suitable here."}
        ],
        "assumptions": ["AWS is the target cloud."],
        "risks": ["Cost must be checked during BCM estimate."],
        "sources": ["AWS MWAA documentation"],
    }
    record.update(overrides)
    return record


def test_architecture_decision_with_no_novel_resources_still_validates():
    """Backward-compatible: a record with no novel_resources key at all (every pre-existing
    record, and every record for a requirement fully covered by the catalog) is unaffected."""
    ok, missing = archdec.validate(_complete_record())
    assert ok is True
    assert missing == []


def test_architecture_decision_complete_novel_resource_entry_validates():
    record = _complete_record(novel_resources=[{
        "resource_type": "aws_dynamodb_table",
        "justification": "Requirement needs a low-latency key-value store; no catalog module provides one.",
        "alternatives_considered": ["aws_elasticache_cluster (rejected: overkill for this access pattern)"],
        "grounding_examples": ["storage-medallion-s3"],
    }])
    ok, missing = archdec.validate(record)
    assert ok is True
    assert missing == []


def test_architecture_decision_incomplete_novel_resource_entry_fails_same_as_incomplete_alternative():
    record = _complete_record(novel_resources=[{
        "resource_type": "aws_dynamodb_table",
        "justification": "",
        "alternatives_considered": [],
    }])
    ok, missing = archdec.validate(record)
    assert ok is False
    assert any("novel_resources entry incomplete" in item for item in missing)


def test_architecture_decision_editor_add_novel_resource(tmp_path):
    path = tmp_path / "architecture_decision.json"

    archdec.set_summary(
        str(path),
        selected_architecture="AWS managed lakehouse",
        decision_summary="Chosen for governed batch analytics.",
    )
    archdec.add_modules(str(path), ["storage-medallion-s3"])
    archdec.add_alternative(str(path), "Redshift-only", "rejected", "Does not fit lakehouse storage needs.")
    archdec.add_list_item(str(path), "assumptions", "AWS is approved.")
    archdec.add_list_item(str(path), "risks", "Cost needs guardrails.")
    archdec.add_list_item(str(path), "sources", "Terraform AWS provider registry")
    record = archdec.add_novel_resource(
        str(path), "aws_dynamodb_table",
        "Requirement needs a low-latency key-value store; no catalog module provides one.",
        ["aws_elasticache_cluster (rejected: overkill)"],
        ["storage-medallion-s3"],
    )

    ok, missing = archdec.validate(record)
    assert ok is True
    assert missing == []
    assert record["novel_resources"][0]["resource_type"] == "aws_dynamodb_table"
