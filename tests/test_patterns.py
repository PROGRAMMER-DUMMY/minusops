import patterns


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(patterns, "WORKSPACE", str(tmp_path))


def test_capture_and_list(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    p = patterns.capture_pattern(
        "airflow lakehouse with data quality",
        ["storage-medallion-s3", "orchestrator-mwaa", "dq-great-expectations"],
        name="airflow-dq-lake", plan_hash="abc123", approver="alice")
    assert p["id"] == "airflow-dq-lake"
    assert "orchestrator-mwaa" in p["modules"]
    assert patterns.get_pattern("airflow-dq-lake")["plan_hash"] == "abc123"
    # invalid module ids are dropped, not stored
    p2 = patterns.capture_pattern("x", ["query-athena", "not-a-module"])
    assert p2["modules"] == ["query-athena"]


def test_match_reuses_a_prior_approved_pattern(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    patterns.capture_pattern(
        "airflow lakehouse with data quality and schema enforcement",
        ["storage-medallion-s3", "orchestrator-mwaa", "dq-great-expectations",
         "schema-registry-glue", "governance-observability"],
        name="airflow-dq")
    # a near-identical new request should surface the captured pattern for reuse
    hits = patterns.match_patterns(
        "managed airflow data lake with data quality checks and schema enforcement")
    assert hits and hits[0]["id"] == "airflow-dq"
    assert hits[0]["reuse_score"] > 0.5


def test_reuse_is_scored_against_modules_the_request_names_not_token_noise(tmp_path,
                                                                          monkeypatch):
    """Regression, 2026-08-22.

    `match_modules` is a weak-signal RANKER: any single shared token scores +1, which is how
    `governance-lakeformation` ("lake formation") surfaces on any request containing "data
    lake". Building the reuse target from every ranked hit meant each newly added module
    diluted the Jaccard denominator, and every stored pattern's reuse_score drifted down --
    a catalog that grows would silently stop reusing approved compositions.

    The docstring already promised "the module set those requirements *would* select".
    Single-token overlap is not selection.
    """
    _isolate(tmp_path, monkeypatch)
    patterns.capture_pattern(
        "airflow lakehouse with data quality and schema enforcement",
        ["storage-medallion-s3", "orchestrator-mwaa", "dq-great-expectations",
         "schema-registry-glue", "governance-observability"],
        name="airflow-dq")

    request = "managed airflow data lake with data quality checks and schema enforcement"
    import modules as module_registry

    ranked = {m["id"] for m in module_registry.match_modules(request)}
    targeted = patterns._reuse_target(request)

    assert "governance-lakeformation" in ranked, "the ranker still surfaces it (by design)"
    assert "governance-lakeformation" not in targeted, "selection must not inherit the noise"
