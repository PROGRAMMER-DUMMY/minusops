import io
import json
import os
from contextlib import redirect_stdout

import minusctl
import workflow


def _capture(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = minusctl.main(argv)
    return code, out.getvalue()


def _patch_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow.runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(workflow.runs, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(minusctl.runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(minusctl.runs, "RUNS_DIR", str(tmp_path / "runs"))


def test_minusctl_create_generates_workspace(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)

    code, output = _capture([
        "create",
        "create a governed AWS data pipeline",
        "--input", "owner=data-platform",
        "--input", "daily_data_gb=50",
        "--generate",
        "--json",
    ])
    data = json.loads(output)

    assert code == 0
    assert data["terraform_generated"] is True
    assert os.path.exists(os.path.join(data["run"]["terraform_dir"], "main.tf"))
    assert os.path.exists(os.path.join(data["run"]["terraform_dir"], ".minus", "baseline.json"))


def test_minusctl_next_reports_safe_commands(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    code, _ = _capture([
        "create",
        "create a governed AWS data pipeline",
        "--input", "owner=data-platform",
        "--input", "daily_data_gb=50",
        "--generate",
    ])
    assert code == 0

    code, output = _capture(["next"])

    assert code == 0
    assert "python core/plan_gate.py verify --dir" in output
    assert "do not apply until a reviewed plan hash is approved" in output


def test_minusctl_guard_detects_manual_edit(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _, output = _capture([
        "create",
        "create a governed AWS data pipeline",
        "--input", "owner=data-platform",
        "--input", "daily_data_gb=50",
        "--generate",
        "--json",
    ])
    data = json.loads(output)
    main_tf = os.path.join(data["run"]["terraform_dir"], "main.tf")
    with open(main_tf, "a", encoding="utf-8") as f:
        f.write("\n# manual edit\n")

    code, status = _capture(["guard", "status", "--json"])
    diff_code, diff = _capture(["guard", "diff"])

    assert code == 0
    assert json.loads(status)["status"] == "STALE"
    assert diff_code == 0
    assert "# manual edit" in diff


def test_minusctl_package_writes_enterprise_summary(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _, output = _capture([
        "create",
        "create a governed AWS data pipeline",
        "--input", "owner=data-platform",
        "--input", "daily_data_gb=50",
        "--generate",
        "--json",
    ])
    data = json.loads(output)

    code, package_output = _capture(["package", "--json"])
    package = json.loads(package_output)
    md_path = os.path.join(data["run"]["root"], "enterprise-package.md")
    json_path = os.path.join(data["run"]["root"], "enterprise-package.json")

    assert code == 0
    assert os.path.exists(md_path)
    assert os.path.exists(json_path)
    assert package["paths"]["markdown"] == md_path
    assert "main.tf" in package["generated_files"]
    text = open(md_path, encoding="utf-8").read()
    assert "MinusOps Enterprise Run Package" in text
    assert "do not apply until a reviewed plan hash is approved" in text
    assert "Readiness Checks" in text


def test_minusctl_readiness_scores_missing_report_evidence(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _capture([
        "create",
        "create a governed AWS data pipeline",
        "--input", "owner=data-platform",
        "--input", "daily_data_gb=50",
        "--generate",
    ])

    code, output = _capture(["readiness", "--json"])
    strict_code, _ = _capture(["readiness", "--strict"])
    data = json.loads(output)

    assert code == 0
    assert strict_code == 2
    assert data["status"] == "NEEDS_EVIDENCE"
    assert data["score"] < 100
    assert any(item["name"] == "report exists" and not item["ok"] for item in data["checks"])


def test_minusctl_prove_writes_evidence_bundle(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    import providers.base as pb

    class _P:
        def credential_posture(self):
            return {"connected": False, "type": "unknown"}
    monkeypatch.setattr(pb, "get_provider", lambda *a, **k: _P())

    _capture(["create", "create a governed AWS data pipeline",
              "--input", "owner=data-platform", "--input", "daily_data_gb=50", "--generate"])
    code, output = _capture(["prove", "--json"])
    data = json.loads(output)

    assert os.path.exists(data["paths"]["json"]) and os.path.exists(data["paths"]["markdown"])
    assert isinstance(data["offline_chain_proven"], bool)
    assert data["aws_connected"] is False
    assert "next_aws_steps" in data and data["next_aws_steps"]
    bundle = open(data["paths"]["markdown"], encoding="utf-8").read()
    assert "Evidence Bundle" in bundle and "Remaining AWS-gated steps" in bundle


def test_minusctl_readiness_blocks_on_manual_source_edit(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _, output = _capture([
        "create",
        "create a governed AWS data pipeline",
        "--input", "owner=data-platform",
        "--input", "daily_data_gb=50",
        "--generate",
        "--json",
    ])
    data = json.loads(output)
    with open(os.path.join(data["run"]["terraform_dir"], "main.tf"), "a", encoding="utf-8") as f:
        f.write("\n# manual edit\n")

    code, output = _capture(["readiness", "--json"])
    readiness = json.loads(output)

    assert code == 0
    assert readiness["status"] == "BLOCKED"
    assert any(item["name"] == "source is current" and not item["ok"] for item in readiness["blockers"])
