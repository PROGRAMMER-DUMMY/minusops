import os

import pytest

import architecture_decision as archdec
import requirements as reqgate
import synthesizer

COMPLETE_SPEC = {
    "goal": "governed airflow data pipeline for analysts",
    "system_class": "data-pipeline",
    "functional": ["analysts run SQL over curated data"],
    "non_functional": {
        "latency": "hourly batch", "scale": "50 GB/day", "availability": "99.9%",
        "retention": "deferred: pending legal", "security": "KMS + scoped IAM", "budget": "$500/mo",
    },
}

COMPLETE_DECISION = {
    "requirements_file": "requirements.json",
    "selected_architecture": "AWS managed lakehouse with MWAA orchestration",
    "decision_summary": "Chosen for Airflow orchestration, data quality, and schema governance.",
    "selected_modules": ["orchestrator-mwaa", "dq-great-expectations", "schema-registry-glue"],
    "alternatives": [
        {"name": "Step Functions", "decision": "rejected", "reason": "Existing team prefers Airflow DAGs."}
    ],
    "assumptions": ["AWS is the target cloud."],
    "risks": ["MWAA cost must be checked during BCM estimate."],
    "sources": ["AWS MWAA documentation", "Terraform AWS provider registry"],
}


def test_select_modules_adds_governance_baseline():
    chosen = synthesizer.select_modules("a data lake with athena for analysts")
    ids = [m["id"] for m in chosen]
    assert "storage-medallion-s3" in ids and "query-athena" in ids
    assert "governance-observability" in ids   # always governed


def test_compose_writes_a_governed_terraform_root(tmp_path):
    out = tmp_path / "tf"
    res = synthesizer.compose(
        ["storage-medallion-s3", "compute-glue-etl", "query-athena"],
        "acme-dev", str(out), owner="acme", request="lakehouse")
    for f in ("versions.tf", "providers.tf", "variables.tf", "main.tf", "COMPOSITION.md"):
        assert (out / f).exists()
    # selected modules are vendored in for a self-contained, reviewable dir
    assert (out / "modules" / "storage-medallion-s3" / "main.tf").exists()
    main = (out / "main.tf").read_text(encoding="utf-8")
    assert 'module "storage_medallion_s3"' in main
    assert '"./modules/compute-glue-etl"' in main   # fmt may realign 'source ='
    # obvious cross-module wiring is done automatically
    assert "module.storage_medallion_s3.kms_key_arn" in main      # athena results encryption
    assert 'module.storage_medallion_s3.bucket_names["bronze"]' in main  # glue script bucket


def test_compose_wires_orchestration_to_glue_jobs(tmp_path):
    # B' loop-close: the orchestrator must reference the compute module (so it is not
    # 'unwired' in conformance), and compute must get a default runnable job.
    out = tmp_path / "tf"
    synthesizer.compose(
        ["storage-medallion-s3", "compute-glue-etl", "orchestrator-stepfunctions"],
        "acme-dev", str(out), owner="acme", request="lakehouse")
    main = (out / "main.tf").read_text(encoding="utf-8")
    assert "module.compute_glue_etl.glue_job_names" in main   # orchestration wired to jobs
    assert "module.compute_glue_etl.glue_job_arns" in main    # sfn can act on the jobs
    assert "bronze_to_silver" in main                         # a default job is composed
    # the orchestrator module builds a real definition from the wired jobs (no mock required)
    orch_main = (out / "modules" / "orchestrator-stepfunctions" / "main.tf").read_text(encoding="utf-8")
    assert "effective_definition" in orch_main
    assert "startJobRun" in orch_main


def test_synthesize_refuses_without_complete_requirements(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    # A vague request with no requirements record is BLOCKED, not guessed into infrastructure.
    with pytest.raises(reqgate.RequirementsIncomplete) as exc:
        synthesizer.synthesize("airflow pipeline", spec={"goal": "x"})
    assert "system_class" in exc.value.missing
    assert not os.path.isdir(tmp_path / "runs")    # nothing generated


def test_synthesize_refuses_without_architecture_decision(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    with pytest.raises(archdec.ArchitectureDecisionIncomplete) as exc:
        synthesizer.synthesize("airflow pipeline", spec=COMPLETE_SPEC)
    assert "selected_architecture" in exc.value.missing
    assert not os.path.isdir(tmp_path / "runs")


def test_synthesize_creates_run_records_requirements_and_composes(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    res = synthesizer.synthesize("airflow pipeline with data quality and schema enforcement",
                                 spec=COMPLETE_SPEC, decision=COMPLETE_DECISION, owner="data-platform")
    assert os.path.isdir(res["run"]["terraform_dir"])
    assert "orchestrator-mwaa" in res["modules"]
    assert "dq-great-expectations" in res["modules"]
    assert "schema-registry-glue" in res["modules"]
    assert os.path.exists(os.path.join(res["out_dir"], "main.tf"))
    # the requirements record is written alongside the run as audit evidence
    assert os.path.exists(os.path.join(res["run"]["root"], "requirements.json"))
    assert os.path.exists(os.path.join(res["run"]["root"], "architecture_decision.json"))
    assert res["requirements_recorded"]
    assert res["architecture_decision_recorded"]


def test_synthesize_into_existing_run_writes_manifest_and_baseline(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    run = runs.new_run(blueprint="requirements-first", request="create airflow pipeline")

    res = synthesizer.synthesize(
        "airflow pipeline with data quality and schema enforcement",
        spec=COMPLETE_SPEC,
        decision=COMPLETE_DECISION,
        owner="data-platform",
        target_run=run,
    )

    assert res["run"]["run_id"] == run["run_id"]
    assert os.path.exists(os.path.join(run["terraform_dir"], "main.tf"))
    assert os.path.exists(os.path.join(run["terraform_dir"], "minus-generated.json"))
    assert os.path.exists(os.path.join(run["terraform_dir"], ".minus", "baseline.json"))
    assert res["manifest"]["blueprint"] == "synthesized"
    assert "minus-generated.json" in res["manifest"]["files"]
    assert res["workflow"]["terraform_generated"] is True
    assert res["workflow"]["generation_blocked"] is False


def test_synthesize_refuses_to_overwrite_nonempty_target_run(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    run = runs.new_run(blueprint="requirements-first", request="create airflow pipeline")
    with open(os.path.join(run["terraform_dir"], "main.tf"), "w", encoding="utf-8") as f:
        f.write("# existing\n")

    with pytest.raises(ValueError) as exc:
        synthesizer.synthesize(
            "airflow pipeline",
            spec=COMPLETE_SPEC,
            decision=COMPLETE_DECISION,
            target_run=run,
        )

    assert "pass --overwrite" in str(exc.value)


def test_synthesize_refuses_unknown_decision_module(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    decision = dict(COMPLETE_DECISION)
    decision["selected_modules"] = ["orchestrator-mwaa", "not-a-module"]

    with pytest.raises(ValueError) as exc:
        synthesizer.synthesize("airflow pipeline", spec=COMPLETE_SPEC, decision=decision)

    assert "unknown selected module" in str(exc.value)


def test_parse_daily_gb_takes_upper_bound_and_converts_tb():
    import synthesizer as s
    gb, src = s.parse_daily_gb({"data_pipeline": {"data_volume": "Large: 10 to 100 GB of sales data per day"}})
    assert gb == 100 and "10 to 100" in src
    gb, _ = s.parse_daily_gb({"data_pipeline": {"data_volume": "about 2 TB daily"}})
    assert gb == 2048
    gb, src = s.parse_daily_gb({"data_pipeline": {"data_volume": "unknown for now"}})
    assert gb == 0 and src == ""


def test_compose_writes_tfvars_with_showback_and_volume(tmp_path):
    import synthesizer as s
    out = tmp_path / "tf"
    s.compose(["storage-medallion-s3", "governance-observability"], "acme-dev", str(out),
              owner="data-platform", run_id="20260702-x", daily_data_gb=100,
              volume_source="10 to 100 GB per day")
    tfvars = (out / "terraform.tfvars").read_text(encoding="utf-8")
    assert '= "20260702-x"' in tfvars               # fmt realigns keys
    assert "daily_data_gb = 100" in tfvars and "10 to 100 GB per day" in tfvars
    providers = (out / "providers.tf").read_text(encoding="utf-8")
    assert "run_id     = var.run_id" in providers   # showback tag on every resource
