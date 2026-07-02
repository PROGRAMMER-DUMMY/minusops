"""
Tests for the data-pipeline reference model + conformance analysis.

Covers: generic (cloud-agnostic) role classification with an honest fallback, the
role->layer mapping, layer coverage, and the conformance checks (missing layers,
unwired orchestration via real references, and the Well-Architected checks).
"""
import architecture_model as am


# --- classification --------------------------------------------------------
def test_classifies_aws_data_pipeline_types_into_expected_roles():
    cases = {
        'aws_s3_bucket.zone["bronze"]': ("aws_s3_bucket", "stage"),
        "aws_s3_bucket.results": ("aws_s3_bucket", "store_other"),
        "aws_glue_job.dq": ("aws_glue_job", "transform"),
        "aws_glue_registry.this": ("aws_glue_registry", "catalog"),
        "aws_glue_catalog_database.db": ("aws_glue_catalog_database", "catalog"),
        "aws_sfn_state_machine.this": ("aws_sfn_state_machine", "orchestrate"),
        "aws_athena_workgroup.this": ("aws_athena_workgroup", "consume"),
        "aws_redshift_cluster.wh": ("aws_redshift_cluster", "consume"),
        "aws_iam_role.glue": ("aws_iam_role", "security"),
        "aws_kms_key.lake": ("aws_kms_key", "security"),
        "aws_cloudwatch_metric_alarm.spend": ("aws_cloudwatch_metric_alarm", "observability"),
        "aws_budgets_budget.monthly": ("aws_budgets_budget", "observability"),
        "aws_kinesis_firehose_delivery_stream.s": ("aws_kinesis_firehose_delivery_stream", "ingest"),
    }
    for addr, (rtype, expected) in cases.items():
        assert am.classify_role(rtype, am._instance_key(addr)) == expected, addr


def test_classification_is_cloud_agnostic():
    assert am.classify_role("azurerm_storage_account") == "store_other"
    assert am.classify_role("google_bigquery_dataset") == "consume"
    # Azure / GCP data services now classify meaningfully (not just fallback)
    assert am.classify_role("azurerm_data_factory") == "ingest"
    assert am.classify_role("google_pubsub_topic") == "ingest"
    assert am.classify_role("google_dataproc_cluster") == "transform"
    assert am.classify_role("azurerm_synapse_workspace") == "consume"
    assert am.layer_of(am.classify_role("google_bigtable_instance")) == "storage"


def test_unknown_type_falls_back_to_other_without_raising():
    assert am.classify_role("aws_totally_made_up_thing") == "other"
    assert am.layer_of(am.classify_role("frobnicator_widget")) == "other"


def test_role_to_layer_mapping():
    assert am.layer_of("stage") == "storage"
    assert am.layer_of("transform") == "processing"
    assert am.layer_of("orchestrate") == "processing"
    assert am.layer_of("catalog") == "catalog"
    assert am.layer_of("consume") == "consumption"
    assert am.layer_of("security") == "governance"
    assert am.layer_of("observability") == "governance"


# --- plan extraction + coverage -------------------------------------------
def _plan(types_with_modules, module_calls=None):
    rcs = []
    for addr, rtype, module in types_with_modules:
        rcs.append({"address": addr, "type": rtype, "name": addr.split(".")[-1].split("[")[0],
                    "module_address": module, "mode": "managed", "change": {"actions": ["create"]}})
    cfg = {"root_module": {"module_calls": module_calls or {}}}
    return {"resource_changes": rcs, "configuration": cfg}


def test_extract_and_layer_coverage():
    plan = _plan([
        ('aws_s3_bucket.zone["bronze"]', "aws_s3_bucket", "module.storage"),
        ("aws_glue_job.dq", "aws_glue_job", "module.compute"),
        ("aws_athena_workgroup.this", "aws_athena_workgroup", "module.query"),
        ("aws_iam_role.glue", "aws_iam_role", "module.compute"),
    ])
    res = am.extract_resources(plan)
    cov = am.layer_coverage(res)
    assert [r["role"] for r in res if r["type"] == "aws_s3_bucket"] == ["stage"]
    assert cov["storage"] and cov["processing"] and cov["consumption"] and cov["governance"]
    assert not cov["ingestion"]


# --- conformance -----------------------------------------------------------
def test_conformance_flags_missing_layers():
    plan = _plan([('aws_s3_bucket.zone["bronze"]', "aws_s3_bucket", "module.storage")])
    rep = am.conformance(plan)
    ids = {f["id"] for f in rep["findings"]}
    assert "ARCH-LAYER-INGESTION" in ids
    assert "ARCH-LAYER-PROCESSING" in ids
    assert "ARCH-LAYER-CONSUMPTION" in ids
    assert rep["layers"]["storage"]["present"] is True
    assert 0 <= rep["score"] <= 100


def test_conformance_detects_unwired_orchestration():
    # Orchestrator module exists and a transform module exists, but the orchestrator's
    # configuration references NO module -> unwired.
    plan = _plan(
        [("aws_sfn_state_machine.this", "aws_sfn_state_machine", "module.orchestrator"),
         ("aws_glue_job.dq", "aws_glue_job", "module.compute")],
        module_calls={
            "orchestrator": {"expressions": {"definition": {"constant_value": "{}"}}},
            "compute": {"expressions": {"name": {"references": ["var.name"]}}},
        })
    ids = {f["id"] for f in am.conformance(plan)["findings"]}
    assert "ARCH-ORCH-UNWIRED" in ids


def test_conformance_wired_orchestration_is_not_flagged():
    plan = _plan(
        [("aws_sfn_state_machine.this", "aws_sfn_state_machine", "module.orchestrator"),
         ("aws_glue_job.dq", "aws_glue_job", "module.compute")],
        module_calls={
            "orchestrator": {"expressions": {"jobs": {"references": ["module.compute.glue_job_names",
                                                                     "module.compute"]}}},
            "compute": {"expressions": {"name": {"references": ["var.name"]}}},
        })
    ids = {f["id"] for f in am.conformance(plan)["findings"]}
    assert "ARCH-ORCH-UNWIRED" not in ids


def test_conformance_well_architected_security_checks():
    # Storage with no KMS / no SSE / no versioning should raise the WA security + DR findings.
    plan = _plan([('aws_s3_bucket.zone["bronze"]', "aws_s3_bucket", "module.storage")])
    ids = {f["id"] for f in am.conformance(plan)["findings"]}
    assert "WA-SEC-KMS" in ids
    assert "WA-SEC-SSE" in ids
    assert "WA-REL-DR" in ids
    assert "WA-OPS-MONITORING" in ids


def test_conformance_encryption_satisfied_when_kms_and_sse_present():
    plan = _plan([
        ('aws_s3_bucket.zone["bronze"]', "aws_s3_bucket", "module.storage"),
        ('aws_s3_bucket_server_side_encryption_configuration.zone["bronze"]',
         "aws_s3_bucket_server_side_encryption_configuration", "module.storage"),
        ("aws_kms_key.lake", "aws_kms_key", "module.storage"),
    ])
    ids = {f["id"] for f in am.conformance(plan)["findings"]}
    assert "WA-SEC-KMS" not in ids
    assert "WA-SEC-SSE" not in ids


def test_score_is_deterministic():
    plan = _plan([('aws_s3_bucket.zone["bronze"]', "aws_s3_bucket", "module.storage")])
    assert am.conformance(plan)["score"] == am.conformance(plan)["score"]
