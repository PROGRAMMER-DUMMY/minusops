"""
Step 5 (MINUS-110/119/120): the catalog database, the dbt scaffold, and dbt-only mode.

Fast by construction -- these exercise the pure renderers and the selection rule, not
Terraform. The end-to-end composition is covered by the slow tests in test_synthesizer.py.
"""
import os

import synthesizer

_MODULES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "modules")


def test_dbt_schema_matches_the_database_name_the_athena_module_creates():
    """The one invariant that silently breaks everything if it drifts: dbt's `schema:` must
    name the database `query-athena` actually provisions. Asserted against the module's own
    HCL, so changing either side without the other fails here instead of at `dbt run`."""
    hcl = open(os.path.join(_MODULES_DIR, "query-athena", "main.tf"), encoding="utf-8").read()
    assert 'name         = "${replace(lower(var.name_prefix), "-", "_")}_gold"' in hcl
    # Same transformation, expressed in Python.
    assert synthesizer.dbt_schema("Demo-Dev") == "demo_dev_gold"


def test_profiles_yml_carries_workgroup_schema_and_env_backed_staging(tmp_path):
    synthesizer.write_dbt_project(str(tmp_path), "demo-dev")
    profiles = (tmp_path / "src" / "dbt" / "profiles.yml").read_text(encoding="utf-8")

    assert "type: athena" in profiles
    assert "work_group: demo-dev-analysts" in profiles
    assert "schema: demo_dev_gold" in profiles
    assert "database: awsdatacatalog" in profiles
    # Account id and run hash are unknowable at synthesis time, so these must stay env-backed
    # rather than being baked in wrong.
    assert "env_var('DBT_ATHENA_S3_STAGING_DIR')" in profiles

    assert (tmp_path / "src" / "dbt" / "dbt_project.yml").exists()
    assert (tmp_path / "src" / "dbt" / "models").is_dir()


def test_transform_engine_defaults_to_glue_not_dbt():
    """Omitting the field must never be read as "dbt": dropping the compute module is a real
    architecture change and has to be stated."""
    assert synthesizer.transform_engine(None) == ""
    assert synthesizer.transform_engine({}) == ""
    assert synthesizer.transform_engine({"transform_engine": "  DBT "}) == "dbt"


def test_outputs_only_reference_modules_that_are_present():
    """An output referencing an absent module is a hard `terraform validate` failure, so the
    renderer is keyed off present_ids rather than emitting a fixed block."""
    storage_only = synthesizer._render_outputs({"storage-medallion-s3"})
    assert "gold_bucket" in storage_only
    assert "module.query_athena" not in storage_only
    assert "module.compute_glue_etl" not in storage_only

    with_athena = synthesizer._render_outputs({"storage-medallion-s3", "query-athena"})
    assert "glue_catalog_database" in with_athena


def test_dbt_only_mode_drops_glue_and_requires_athena(tmp_path, monkeypatch):
    """MINUS-120. Glue is dropped even when explicitly selected -- keeping both is exactly
    the contradiction `transform_engine` exists to resolve -- and dbt without a workgroup is
    refused rather than composed into something that cannot run."""
    import runs

    decision = {
        "selected_modules": ["storage-medallion-s3", "compute-glue-etl", "query-athena"],
        "transform_engine": "dbt",
    }
    monkeypatch.chdir(tmp_path)
    result = synthesizer.synthesize(
        "dbt-only lakehouse", decision=decision, allow_incomplete=True,
        name_prefix="sqlonly-dev", validate=False)

    assert "compute-glue-etl" not in result["modules"]
    assert "query-athena" in result["modules"]
    assert result["transform_engine"] == "dbt"
    assert os.path.isdir(os.path.join(result["run"]["root"], "src", "dbt"))

    main_tf = open(os.path.join(result["out_dir"], "main.tf"), encoding="utf-8").read()
    assert "compute_glue_etl" not in main_tf


def test_dbt_without_athena_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    try:
        synthesizer.synthesize(
            "dbt-only lakehouse", decision={"selected_modules": ["storage-medallion-s3"],
                                            "transform_engine": "dbt"},
            allow_incomplete=True, name_prefix="sqlonly-dev", validate=False)
    except ValueError as exc:
        assert "query-athena" in str(exc)
    else:
        raise AssertionError("dbt-athena with no workgroup must be refused, not composed")
