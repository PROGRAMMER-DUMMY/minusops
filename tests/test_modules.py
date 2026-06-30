import modules


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
