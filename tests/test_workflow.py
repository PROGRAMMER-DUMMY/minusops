import os

import workflow


def test_resolve_creates_requirements_first_run(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow.runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(workflow.runs, "RUNS_DIR", str(tmp_path / "runs"))

    result = workflow.resolve_to_run("create a governed AWS data pipeline", cloud="aws")

    assert result["ok"] is True
    assert result["resolution"]["intent"] == "REQUIREMENTS"
    assert result["run"]["blueprint"] == "requirements-first"
    assert result["missing_inputs"] == []
    assert result["missing_requirements"]
    assert os.path.exists(result["requirements_file"])
    assert os.path.isdir(result["run"]["terraform_dir"])


def test_resolve_refuses_generation_from_hardcoded_blueprint(tmp_path, monkeypatch):
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
    assert result["terraform_generated"] is False
    assert result["generation_blocked"] is True
    assert "requirements-first" in result["generation_block_reason"]
    assert not os.path.exists(os.path.join(tf_dir, "provider.tf"))
    assert not os.path.exists(os.path.join(tf_dir, "minus-generated.json"))
