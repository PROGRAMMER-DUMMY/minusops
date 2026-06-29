import os

import workflow


def test_resolve_creates_run_and_reports_missing_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow.runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(workflow.runs, "RUNS_DIR", str(tmp_path / "runs"))

    result = workflow.resolve_to_run("create a governed AWS data pipeline", cloud="aws")

    assert result["ok"] is False
    assert result["resolution"]["blueprint"]["id"] == "aws-data-pipeline-standard"
    assert {item["name"] for item in result["missing_inputs"]} == {"owner", "daily_data_gb"}
    assert os.path.isdir(result["run"]["terraform_dir"])


def test_resolve_can_generate_terraform_into_run(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow.runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(workflow.runs, "RUNS_DIR", str(tmp_path / "runs"))

    result = workflow.resolve_to_run(
        "create a governed AWS data pipeline",
        cloud="aws",
        inputs={"owner": "data-platform", "daily_data_gb": 50},
        generate=True,
    )

    tf_dir = result["run"]["terraform_dir"]
    assert result["ok"] is True
    assert result["terraform_generated"] is True
    assert os.path.exists(os.path.join(tf_dir, "provider.tf"))
    assert os.path.exists(os.path.join(tf_dir, "s3.tf"))
    assert os.path.exists(os.path.join(tf_dir, "minus-generated.json"))
    assert os.path.exists(os.path.join(tf_dir, ".minus", "baseline.json"))
    assert os.path.exists(os.path.join(tf_dir, ".minus", "source_snapshot", "main.tf"))
