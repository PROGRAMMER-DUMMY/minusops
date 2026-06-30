import os

import synthesizer


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


def test_synthesize_creates_run_and_composes(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    res = synthesizer.synthesize("airflow pipeline with data quality and schema enforcement",
                                 owner="data-platform")
    assert os.path.isdir(res["run"]["terraform_dir"])
    assert "orchestrator-mwaa" in res["modules"]
    assert os.path.exists(os.path.join(res["out_dir"], "main.tf"))
