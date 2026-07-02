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
    monkeypatch.setattr(minusctl.accelerators.runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(minusctl.accelerators.runs, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(minusctl.demo.runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(minusctl.demo.runs, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(minusctl.demo.reporter, "generate_from_plan_json", lambda *a, **k: str(tmp_path / "report"))


def test_minusctl_create_writes_requirements_first_run(tmp_path, monkeypatch):
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
    assert data["terraform_generated"] is False
    assert data["generation_blocked"] is True
    assert data["run"]["blueprint"] == "requirements-first"
    assert os.path.exists(data["requirements_file"])
    assert not os.path.exists(os.path.join(data["run"]["terraform_dir"], "main.tf"))


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
    assert "requirements:" in output
    assert "decision   :" in output
    assert "python core/minusctl.py decision template --write" in output
    assert "--run" in output
    assert "--decision-file" in output
    assert "do not generate Terraform from demo fixtures for production" in output


def test_minusctl_decision_template_writes_control_record(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _capture(["create", "create a governed AWS data pipeline"])

    code, output = _capture(["decision", "template", "--write", "--json"])
    data = json.loads(output)

    assert code == 0
    assert data["written"] is True
    assert os.path.exists(data["path"])
    assert data["record"]["selected_modules"] == []

    check_code, check_output = _capture(["decision", "check"])
    assert check_code == 2
    assert "selected_modules" in check_output


def test_minusctl_accelerator_writes_lakehouse_artifacts(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _capture(["create", "create a governed AWS lakehouse"])

    code, output = _capture([
        "accelerator",
        "aws-lakehouse",
        "--owner", "data-platform",
        "--daily-data-gb", "75",
        "--force",
        "--json",
    ])
    data = json.loads(output)

    assert code == 0
    assert os.path.exists(data["requirements_file"])
    assert os.path.exists(data["decision_file"])
    assert "storage-medallion-s3" in data["decision"]["selected_modules"]
    assert "python core/synthesizer.py" in data["next"]


def test_minusctl_guard_detects_manual_edit(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _, output = _capture(["demo", "governed-data-pipeline", "--json"])
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
    _, output = _capture(["demo", "governed-data-pipeline", "--json"])
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
    _capture(["demo", "governed-data-pipeline"])

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

    _capture(["demo", "governed-data-pipeline"])
    code, output = _capture(["prove", "--json"])
    data = json.loads(output)

    assert os.path.exists(data["paths"]["json"]) and os.path.exists(data["paths"]["markdown"])
    assert isinstance(data["offline_chain_proven"], bool)
    assert data["aws_connected"] is False
    assert "next_aws_steps" in data and data["next_aws_steps"]
    bundle = open(data["paths"]["markdown"], encoding="utf-8").read()
    assert "Evidence Bundle" in bundle and "Remaining AWS-gated steps" in bundle


def test_minusctl_conformance_helper_and_formatter(tmp_path):
    rdir = tmp_path / "rep"
    rdir.mkdir()
    plan = {
        "resource_changes": [
            {"address": 'aws_s3_bucket.zone["bronze"]', "type": "aws_s3_bucket", "name": "zone",
             "module_address": "module.storage", "mode": "managed", "change": {"actions": ["create"]}},
        ],
        "configuration": {"root_module": {"module_calls": {}}},
    }
    (rdir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    reports = [{"id": "abc", "path": str(rdir), "has_plan_json": True}]

    report = minusctl._conformance_for_run({"run_id": "r"}, reports=reports)
    assert report and report["layers"]["storage"]["present"] is True
    assert 0 <= report["score"] <= 100
    # no report -> graceful None
    assert minusctl._conformance_for_run({"run_id": "r"}, reports=[]) is None
    # formatter
    text = minusctl._format_conformance(report)
    assert "Reference-architecture conformance" in text
    assert "storage" in text
    assert "no plan to analyze" in minusctl._format_conformance(None)


def test_minusctl_readiness_blocks_on_manual_source_edit(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _, output = _capture(["demo", "governed-data-pipeline", "--json"])
    data = json.loads(output)
    with open(os.path.join(data["run"]["terraform_dir"], "main.tf"), "a", encoding="utf-8") as f:
        f.write("\n# manual edit\n")

    code, output = _capture(["readiness", "--json"])
    readiness = json.loads(output)

    assert code == 0
    assert readiness["status"] == "BLOCKED"
    assert any(item["name"] == "source is current" and not item["ok"] for item in readiness["blockers"])


def test_minusctl_readiness_rejects_comment_stub_core_files(tmp_path, monkeypatch):
    # Loophole #5 regression: an agent once satisfied "core Terraform files present" by
    # writing one-line comment stubs. Content must contain real Terraform blocks.
    _patch_runs(tmp_path, monkeypatch)
    _, output = _capture(["demo", "governed-data-pipeline", "--json"])
    run = json.loads(output)["run"]
    run_id = run["run_id"]
    tf_dir = run["terraform_dir"]

    # A healthy composed workspace passes the check.
    code, out = _capture(["readiness", "--run", run_id, "--json"])
    data = json.loads(out)
    core = next(c for c in data["checks"] if c["name"] == "core Terraform files present")
    assert core["ok"], core

    # Add a comment-only stub file -> the check must fail (gaming detection).
    with open(os.path.join(tf_dir, "s3_extra.tf"), "w", encoding="utf-8") as f:
        f.write("# S3 buckets managed within storage module\n")
    code, out = _capture(["readiness", "--run", run_id, "--json"])
    data = json.loads(out)
    core = next(c for c in data["checks"] if c["name"] == "core Terraform files present")
    assert not core["ok"]
    assert "s3_extra.tf" in core["detail"] and "stub" in core["detail"]


def test_guard_refresh_requires_acknowledgment(tmp_path, monkeypatch):
    # Loophole #2: re-baselining generated code must be an explicit, audited act.
    _patch_runs(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    _, output = _capture(["demo", "governed-data-pipeline", "--json"])
    run_id = json.loads(output)["run"]["run_id"]

    code, _ = _capture(["guard", "refresh", "--run", run_id, "--label", "reviewed"])
    assert code == 2                          # refused without the acknowledgment

    code, _ = _capture(["guard", "refresh", "--run", run_id, "--label", "reviewed",
                        "--ack-manual-edits", "shubh reviewed the diff; alarm fix is correct"])
    assert code == 0
    audit = (tmp_path / ".agents" / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    assert "guard_refresh" in audit and "alarm fix is correct" in audit
