import os

import pytest

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
    assert 'source = "./modules/compute-glue-etl"' in main
    # obvious cross-module wiring is done automatically
    assert "module.storage_medallion_s3.kms_key_arn" in main      # athena results encryption
    assert 'module.storage_medallion_s3.bucket_names["bronze"]' in main  # glue script bucket


def test_synthesize_refuses_without_complete_requirements(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    # A vague request with no requirements record is BLOCKED, not guessed into infrastructure.
    with pytest.raises(reqgate.RequirementsIncomplete) as exc:
        synthesizer.synthesize("airflow pipeline", spec={"goal": "x"})
    assert "system_class" in exc.value.missing
    assert not os.path.isdir(tmp_path / "runs")    # nothing generated


def test_synthesize_creates_run_records_requirements_and_composes(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    res = synthesizer.synthesize("airflow pipeline with data quality and schema enforcement",
                                 spec=COMPLETE_SPEC, owner="data-platform")
    assert os.path.isdir(res["run"]["terraform_dir"])
    assert "orchestrator-mwaa" in res["modules"]
    assert os.path.exists(os.path.join(res["out_dir"], "main.tf"))
    # the requirements record is written alongside the run as audit evidence
    assert os.path.exists(os.path.join(res["run"]["root"], "requirements.json"))
    assert res["requirements_recorded"]
