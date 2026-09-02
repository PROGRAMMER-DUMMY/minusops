"""
The no-cloud demo's synthetic plan carries the resources the reports expect to find.

One test, deliberately. The demo exists so the governance loop can be shown without
credentials; what can regress is the synthetic plan drifting from the shape reporter.py
reads, which would fail far from here.

Depends on: core/generation/demo.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""

import demo


def test_synthetic_plan_contains_expected_pipeline_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(demo.runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(demo.runs, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(demo.reporter, "generate_from_plan_json", lambda *a, **k: str(tmp_path / "report"))

    result = demo.governed_data_pipeline("data-platform", 50)
    plan = demo.synthetic_plan(result["run"]["terraform_dir"], result["inputs"])
    types = {item["type"] for item in plan["resource_changes"]}

    assert result["demo"] is True
    assert result["run"]["blueprint"] == "demo/aws-data-pipeline-standard"
    assert "aws_s3_bucket" in types
    assert "aws_glue_job" in types
    assert "aws_sfn_state_machine" in types
    assert "aws_athena_workgroup" in types
    assert "aws_budgets_budget" in types
    assert len(plan["resource_changes"]) >= 20
