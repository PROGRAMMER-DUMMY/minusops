import os

import demo
import workflow


def test_synthetic_plan_contains_expected_pipeline_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow.runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(workflow.runs, "RUNS_DIR", str(tmp_path / "runs"))

    result = workflow.resolve_to_run(
        "create a governed AWS data pipeline",
        cloud="aws",
        inputs={"owner": "data-platform", "daily_data_gb": 50},
        generate=True,
    )
    plan = demo.synthetic_plan(result["run"]["terraform_dir"], result["inputs"])
    types = {item["type"] for item in plan["resource_changes"]}

    assert "aws_s3_bucket" in types
    assert "aws_glue_job" in types
    assert "aws_sfn_state_machine" in types
    assert "aws_athena_workgroup" in types
    assert "aws_budgets_budget" in types
    assert len(plan["resource_changes"]) >= 20
