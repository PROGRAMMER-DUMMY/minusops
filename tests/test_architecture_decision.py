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
