import json
import os

import modules


def test_module_dir_prefers_explicit_runtime_asset_root(tmp_path, monkeypatch):
    root = tmp_path / "runtime-modules"
    mod = root / "query-athena"
    mod.mkdir(parents=True)
    (mod / "main.tf").write_text("# packaged module\n", encoding="utf-8")

    monkeypatch.setenv("MINUSOPS_MODULES_DIR", str(root))

    assert modules.module_dir("query-athena") == str(mod)


def test_module_dir_can_find_installed_data_files(tmp_path, monkeypatch):
    data_root = tmp_path / "venv-data"
    mod = data_root / "modules" / "query-athena"
    mod.mkdir(parents=True)
    (mod / "main.tf").write_text("# installed module\n", encoding="utf-8")

    monkeypatch.delenv("MINUSOPS_MODULES_DIR", raising=False)
    (tmp_path / "empty-workdir").mkdir()
    monkeypatch.chdir(tmp_path / "empty-workdir")
    monkeypatch.setattr(modules, "REPO_ROOT", str(tmp_path / "not-a-checkout"))
    monkeypatch.setattr(modules.sysconfig, "get_path", lambda key: str(data_root) if key == "data" else "")

    assert modules.module_dir("query-athena") == str(mod)


def test_output_root_prefers_explicit_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("MINUSOPS_OUTPUT_DIR", str(tmp_path / "explicit"))

    assert modules.output_root() == str(tmp_path / "explicit")


def test_output_root_uses_cwd_when_it_looks_like_a_checkout(tmp_path, monkeypatch):
    monkeypatch.delenv("MINUSOPS_OUTPUT_DIR", raising=False)
    checkout = tmp_path / "checkout"
    (checkout / "modules").mkdir(parents=True)
    monkeypatch.chdir(checkout)
    monkeypatch.setattr(modules, "REPO_ROOT", str(tmp_path / "not-a-checkout"))

    assert modules.output_root() == str(checkout)


def test_output_root_falls_back_to_repo_root_when_cwd_is_not_a_checkout(tmp_path, monkeypatch):
    monkeypatch.delenv("MINUSOPS_OUTPUT_DIR", raising=False)
    (tmp_path / "empty-workdir").mkdir()
    monkeypatch.chdir(tmp_path / "empty-workdir")
    checkout = tmp_path / "real-checkout"
    (checkout / "modules").mkdir(parents=True)
    monkeypatch.setattr(modules, "REPO_ROOT", str(checkout))

    assert modules.output_root() == str(checkout)


def test_output_root_never_resolves_into_an_installed_wheel(tmp_path, monkeypatch):
    # The actual bug this fixes: an installed wheel's REPO_ROOT is naked dirname math off
    # modules.py's own location (e.g. .../site-packages), which has no modules/ or
    # pyproject.toml of its own. cwd is also just "wherever the user happened to run the
    # command from" -- neither looks like a real MinusOps checkout. Must NOT return either one;
    # must fall back to a guaranteed-writable per-user location instead.
    monkeypatch.delenv("MINUSOPS_OUTPUT_DIR", raising=False)
    fake_site_packages = tmp_path / "site-packages"
    fake_site_packages.mkdir()
    fake_cwd = tmp_path / "some-random-directory"
    fake_cwd.mkdir()
    monkeypatch.chdir(fake_cwd)
    monkeypatch.setattr(modules, "REPO_ROOT", str(fake_site_packages))

    result = modules.output_root()

    assert result != str(fake_site_packages)
    assert result != str(fake_cwd)
    assert result == os.path.join(os.path.expanduser("~"), ".minusops")


def test_registry_is_valid_and_terraform_exists():
    # Every module passes schema validation AND has its Terraform on disk.
    assert modules.validate_modules() == []
    assert len(modules.list_modules()) >= 8


def test_match_selects_the_company_specific_modules():
    # The exact axes a real company varies on must resolve to the right building blocks.
    req = "airflow orchestration, lambda architecture with a streaming speed layer, data quality checks, and schema enforcement"
    ids = [m["id"] for m in modules.match_modules(req)]
    assert "orchestrator-mwaa" in ids          # airflow
    assert "speed-layer-kinesis" in ids        # lambda architecture / streaming
    assert "dq-great-expectations" in ids      # data quality
    assert "schema-registry-glue" in ids       # schema enforcement


def test_match_is_explainable_and_scored():
    matches = modules.match_modules("athena sql for analysts")
    top = matches[0]
    assert top["id"] == "query-athena"
    assert top["score"] > 0 and top["matched"]  # selection is justified, not a black box


def test_get_and_categories():
    assert modules.get_module("storage-medallion-s3")["category"] == "storage"
    assert modules.get_module("does-not-exist") is None
    assert "orchestration" in modules.categories()


# ---------------------------------------------------------------------------
# retrieve_grounding_examples() (docs/phase6_step5_teardown_scope.md section 3): match_modules()
# repurposed toward retrieval-for-grounding, additive -- the scorer itself is untouched, this is
# just a different consumer of its ranking.
# ---------------------------------------------------------------------------

def test_retrieve_grounding_examples_ranks_the_same_as_match_modules():
    req = "athena sql for analysts"
    ranked_ids = [m["id"] for m in modules.match_modules(req)]
    examples = modules.retrieve_grounding_examples(req, top_n=5)
    assert [e["id"] for e in examples] == ranked_ids[:5]


def test_retrieve_grounding_examples_includes_real_module_content():
    examples = modules.retrieve_grounding_examples("athena sql for analysts", top_n=1)
    assert len(examples) == 1
    assert examples[0]["id"] == "query-athena"
    with open(os.path.join(modules.module_dir("query-athena"), "main.tf"), encoding="utf-8") as f:
        real_content = f.read()
    assert examples[0]["content"] == real_content


def test_retrieve_grounding_examples_respects_top_n():
    examples = modules.retrieve_grounding_examples(
        "airflow pipeline with data quality and schema enforcement", top_n=2)
    assert len(examples) <= 2


def test_retrieve_grounding_examples_never_selects_by_itself():
    """Repurposing toward retrieval must never become a selection decision on its own (docs/
    phase6_scope.md section 2.1's own non-negotiable) -- match_modules() itself is completely
    untouched by this addition, still usable for final-selection exactly as before."""
    req = "airflow pipeline with data quality and schema enforcement"
    before = modules.match_modules(req)
    modules.retrieve_grounding_examples(req)
    after = modules.match_modules(req)
    assert before == after


# ---------------------------------------------------------------------------
# derive_module_ids() (Phase 7 Item 3, docs/phase7_generation_engine_plan.md):
# Read structured requirements fields (not free-text keyword scoring) to recommend
# module ids deterministically. Fixes the live bug where match_modules() on free text
# containing "ingest" pulled in streaming modules even for batch requests.
# ---------------------------------------------------------------------------

def test_derive_module_ids_picks_athena_for_sql_consumption_not_redshift():
    data = {
        "data_pipeline": {
            "consumption": "ad-hoc SQL access for analysts over curated S3 data",
        },
        "non_functional": {
            "latency": "batch job, results needed within 4 hours",
        },
    }
    picks = modules.derive_module_ids(data)
    picked_ids = {p["module_id"] for p in picks}
    assert "query-athena" in picked_ids
    assert "consumption-redshift-serverless" not in picked_ids


def test_derive_module_ids_excludes_streaming_modules_for_batch_latency():
    # This is the exact live bug this plan fixes: match_modules() on a free-text sentence
    # containing "ingest" pulled in speed-layer-kinesis/ingest-firehose even for a plain
    # batch request. derive_module_ids reads the structured latency field instead and must
    # not recommend either streaming module when latency describes a batch cadence.
    data = {
        "data_pipeline": {
            "consumption": "ad-hoc SQL access for analysts over curated S3 data",
        },
        "non_functional": {
            "latency": "batch job, results needed within 4 hours",
        },
    }
    picks = modules.derive_module_ids(data)
    picked_ids = {p["module_id"] for p in picks}
    assert "speed-layer-kinesis" not in picked_ids
    assert "ingest-firehose" not in picked_ids


def test_derive_module_ids_picks_streaming_modules_for_real_time_latency():
    data = {
        "data_pipeline": {},
        "non_functional": {
            "latency": "sub-second streaming ingest, near real-time delivery required",
        },
    }
    picks = modules.derive_module_ids(data)
    picked_ids = {p["module_id"] for p in picks}
    assert "speed-layer-kinesis" in picked_ids
    assert "ingest-firehose" in picked_ids


def test_derive_module_ids_picks_redshift_for_high_concurrency_bi():
    data = {
        "data_pipeline": {
            "consumption": "BI dashboards at scale for many analysts, high concurrency",
        },
    }
    picks = modules.derive_module_ids(data)
    picked_ids = {p["module_id"] for p in picks}
    assert "consumption-redshift-serverless" in picked_ids
    assert "query-athena" not in picked_ids


def test_derive_module_ids_picks_mwaa_for_airflow_orchestration():
    data = {
        "data_pipeline": {
            "orchestration": "managed Apache Airflow for DAG scheduling",
        },
    }
    picks = modules.derive_module_ids(data)
    picked_ids = {p["module_id"] for p in picks}
    assert "orchestrator-mwaa" in picked_ids
    assert "orchestrator-stepfunctions" not in picked_ids


def test_derive_module_ids_picks_schema_registry_for_data_contracts():
    data = {
        "data_pipeline": {
            "catalog": "schema registry with enforced data contracts, Avro compatibility",
        },
    }
    picks = modules.derive_module_ids(data)
    picked_ids = {p["module_id"] for p in picks}
    assert "schema-registry-glue" in picked_ids


def test_derive_module_ids_skips_deferred_and_blank_fields():
    data = {
        "data_pipeline": {
            "consumption": "deferred: not decided yet, revisit after pilot",
            "orchestration": "",
            "catalog": "   ",
        },
        "non_functional": {
            "latency": "deferred: revisit after load testing",
        },
    }
    assert modules.derive_module_ids(data) == []


def test_derive_module_ids_reports_reason_and_source_field():
    data = {"data_pipeline": {"catalog": "schema registry with data contracts"}}
    picks = modules.derive_module_ids(data)
    assert len(picks) == 1
    assert picks[0]["module_id"] == "schema-registry-glue"
    assert picks[0]["source_field"] == "data_pipeline.catalog"
    assert "data_pipeline.catalog" in picks[0]["reason"]


def test_derive_module_ids_picks_more_specific_match_when_consumption_mentions_both():
    # Real requirements answers can plausibly mention both Athena-ish and Redshift-ish
    # language. This must not recommend both -- the more specific match (more matched
    # phrases) should win alone.
    data = {
        "data_pipeline": {
            "consumption": (
                "ad-hoc SQL access, but also BI dashboards at scale for many analysts "
                "with high concurrency"
            ),
        },
    }
    picks = modules.derive_module_ids(data)
    picked_ids = {p["module_id"] for p in picks}
    assert picked_ids == {"consumption-redshift-serverless"}


def test_main_preplan_prints_recommendations(tmp_path, capsys):
    req_path = tmp_path / "requirements.json"
    req_path.write_text(json.dumps({
        "data_pipeline": {"catalog": "schema registry with data contracts"},
    }), encoding="utf-8")

    exit_code = modules.main(["preplan", str(req_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "schema-registry-glue" in out
    assert "data_pipeline.catalog" in out


def test_main_preplan_reports_no_recommendations(tmp_path, capsys):
    req_path = tmp_path / "requirements.json"
    req_path.write_text(json.dumps({"data_pipeline": {}, "non_functional": {}}), encoding="utf-8")

    exit_code = modules.main(["preplan", str(req_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "no enumerable-field recommendations" in out


def test_derive_module_ids_surfaces_both_alternatives_on_a_genuine_tie():
    # "interactive BI for many analysts" ties: query-athena matches "bi" (score 1),
    # consumption-redshift-serverless matches "many analysts" (score 1). A prior review found
    # a tie silently returned both with no signal they're alternatives -- this locks in the
    # fix: both are returned, each reason explicitly says they're tied alternatives.
    data = {"data_pipeline": {"consumption": "interactive BI for many analysts"}}
    picks = modules.derive_module_ids(data)
    picked_ids = {p["module_id"] for p in picks}
    assert picked_ids == {"query-athena", "consumption-redshift-serverless"}
    for p in picks:
        assert "tied with" in p["reason"]
        assert "mutually-exclusive alternatives" in p["reason"]
