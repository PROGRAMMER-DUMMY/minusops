"""
Step 8 (MINUS-128/129/135): compute tiers, the stage reflector, and --based-on inheritance.

The reflector's whole value is that it re-derives from artifacts instead of trusting claims,
so these tests hand it real files on disk and check the verdict, never a mocked "result".
"""
import json
import os

import modules
import reflector
import synthesizer

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- MINUS-128: compute tier matrix -------------------------------------------------------

def test_tier_crossovers_are_where_the_cheaper_option_stops_being_cheaper():
    assert modules.compute_tier(500)["module_id"] == "compute-glue-etl"
    assert modules.compute_tier(1023)["module_id"] == "compute-glue-etl"
    assert modules.compute_tier(1024)["module_id"] == "compute-emr-serverless"
    assert modules.compute_tier(5000)["module_id"] == "compute-emr-serverless"
    assert modules.compute_tier(5120)["module_id"] == "compute-emr-ec2-spot"
    assert modules.compute_tier(50000)["module_id"] == "compute-emr-ec2-spot"


def test_undeclared_volume_gets_the_smallest_tier_not_a_guess():
    """Recommending an EMR cluster off no evidence is how a $40/month pipeline acquires a
    $4,000/month bill."""
    tier = modules.compute_tier(0)
    assert tier["module_id"] == "compute-glue-etl"
    assert "undeclared" in tier["reason"]


def test_flex_needs_an_sla_that_tolerates_an_unpredictable_start():
    assert modules.compute_tier(50, "nightly batch")["execution_class"] == "FLEX"
    assert modules.compute_tier(50, "runs overnight")["execution_class"] == "FLEX"
    assert modules.compute_tier(50, "real-time dashboard")["execution_class"] == "STANDARD"
    assert modules.compute_tier(50, "")["execution_class"] == "STANDARD"


def test_intolerant_wins_a_mixed_sla():
    """"hourly batch feeding a real-time dashboard" mentions both. FLEX would be wrong."""
    assert modules.compute_tier(50, "hourly batch feeding a real-time dashboard"
                                )["execution_class"] == "STANDARD"


def test_execution_class_is_only_meaningful_for_glue():
    assert modules.compute_tier(9000, "nightly")["execution_class"] is None


# --- MINUS-129: the reflector ------------------------------------------------------------

def _run(tmp_path, main_tf="", requirements=None):
    root = tmp_path / "runs" / "r1"
    (root / "terraform").mkdir(parents=True)
    (root / "terraform" / "main.tf").write_text(main_tf, encoding="utf-8")
    if requirements is not None:
        (root / "requirements.json").write_text(json.dumps(requirements), encoding="utf-8")
    return str(root)


def _gate(result, name):
    return next(g for g in result["gates"] if g["gate"] == name)


_WIRED_GLUE = '''module "compute_glue_etl" {
  source        = "./modules/compute-glue-etl"
  data_buckets  = values(module.storage_medallion_s3.bucket_names)
  kms_key_arn   = module.storage_medallion_s3.kms_key_arn
  source_bucket = module.storage_medallion_s3.bucket_names["bronze"]
  target_bucket = module.storage_medallion_s3.bucket_names["silver"]
}
'''


def test_unknown_is_never_reported_as_a_pass(tmp_path):
    """The failure mode this guards: a gate that could not run looking identical to a gate
    that ran and was satisfied."""
    result = reflector.reflect(_run(tmp_path))
    assert result["summary"][reflector.UNKNOWN] >= 3
    assert result["summary"][reflector.PASS] < 5
    assert "unknown is not a pass" in reflector.format_result(result)


def test_missing_cross_module_wiring_blocks(tmp_path):
    """The exact 2026-08-17 defect: the Glue module composed without the S3 write and KMS
    grants. Every resource plans fine; the pipeline dies on its first write."""
    root = _run(tmp_path, main_tf='module "compute_glue_etl" {\n  source = "./m"\n}\n')
    gate = _gate(reflector.reflect(root), "G2_wiring")
    assert gate["status"] == reflector.BLOCKED
    for field in ("data_buckets", "kms_key_arn", "source_bucket", "target_bucket"):
        assert f"compute_glue_etl.{field}" in gate["evidence"]["missing"]


def test_fully_wired_glue_passes(tmp_path):
    gate = _gate(reflector.reflect(_run(tmp_path, main_tf=_WIRED_GLUE)), "G2_wiring")
    assert gate["status"] == reflector.PASS


def test_wiring_notices_a_literal_where_a_module_reference_belongs(tmp_path):
    """Set-but-hardcoded is legal and sometimes deliberate, so it is reported rather than
    blocked -- but it must not be invisible."""
    hcl = _WIRED_GLUE.replace('module.storage_medallion_s3.bucket_names["silver"]',
                              '"some-hardcoded-bucket"')
    gate = _gate(reflector.reflect(_run(tmp_path, main_tf=hcl)), "G2_wiring")
    assert gate["status"] == reflector.PASS
    assert "compute_glue_etl.target_bucket" in gate["evidence"]["literal_only"]


def test_scope_blocks_when_volume_outgrew_the_composed_engine(tmp_path):
    root = _run(tmp_path, main_tf=_WIRED_GLUE,
                requirements={"data_pipeline": {"data_volume": "20 TB per day"}})
    gate = _gate(reflector.reflect(root), "G1_scope")
    assert gate["status"] == reflector.BLOCKED
    assert gate["evidence"]["expected"] == "compute-emr-ec2-spot"


def test_scope_passes_when_volume_matches(tmp_path):
    root = _run(tmp_path, main_tf=_WIRED_GLUE,
                requirements={"data_pipeline": {"data_volume": "50 GB per day"}})
    assert _gate(reflector.reflect(root), "G1_scope")["status"] == reflector.PASS


def test_security_gate_reports_its_denominator(tmp_path):
    """"No findings" from a scan that read nothing looks identical to "no findings" from a
    clean stack. Only one of those is a pass."""
    empty = _run(tmp_path)
    os.remove(os.path.join(empty, "terraform", "main.tf"))
    assert _gate(reflector.reflect(empty), "G3_security")["status"] == reflector.UNKNOWN

    clean = _run(tmp_path / "b", main_tf=_WIRED_GLUE)
    gate = _gate(reflector.reflect(clean), "G3_security")
    assert gate["status"] == reflector.PASS
    assert gate["evidence"]["scanned_files"] == 1


def test_cost_gate_will_not_call_a_missing_estimate_a_pass(tmp_path):
    root = _run(tmp_path, main_tf=_WIRED_GLUE,
                requirements={"non_functional": {"budget": "$500/mo"}})
    gate = _gate(reflector.reflect(root), "G4_cost")
    assert gate["status"] == reflector.UNKNOWN
    assert "do not assume" in gate["detail"]


def test_cost_gate_blocks_a_forecast_over_the_ceiling(tmp_path):
    root = _run(tmp_path, main_tf=_WIRED_GLUE,
                requirements={"non_functional": {"budget": "$500/mo"}})
    report = os.path.join(root, "reports", "abc123")
    os.makedirs(report)
    with open(os.path.join(report, "bcm-estimate.json"), "w", encoding="utf-8") as handle:
        json.dump({"totalCost": 900.0}, handle)
    gate = _gate(reflector.reflect(root), "G4_cost")
    assert gate["status"] == reflector.BLOCKED
    assert gate["evidence"]["forecast_usd"] == 900.0


def test_reflect_runs_every_gate_even_after_one_blocks(tmp_path):
    """An operator fixing three problems wants all three now, not one per round trip."""
    root = _run(tmp_path, main_tf='module "compute_glue_etl" {\n  source = "./m"\n}\n',
                requirements={"data_pipeline": {"data_volume": "20 TB per day"}})
    result = reflector.reflect(root)
    assert result["blocked"] is True
    assert len(result["gates"]) == 5


def test_module_block_parser_survives_nested_braces(tmp_path):
    """A non-greedy regex stops at the first inner `}`, which would silently truncate every
    module body containing a dynamic block or an object-typed input."""
    hcl = ('module "a" {\n  jobs = { x = "y" }\n  target_bucket = module.s.b\n}\n'
           'module "b" {\n  x = 1\n}\n')
    blocks = reflector._module_blocks(hcl)
    assert set(blocks) == {"a", "b"}
    assert "target_bucket" in blocks["a"]


# --- MINUS-135: --based-on ----------------------------------------------------------------

def test_inherits_organisational_settings_with_attribution(tmp_path):
    root = tmp_path / "runs" / "base"
    (root / "terraform" / "envs").mkdir(parents=True)
    (root / "terraform" / "terraform.tfvars").write_text(
        'name_prefix = "acme-dev"\nowner = "data-platform"\nregion = "eu-west-1"\n',
        encoding="utf-8")
    (root / "terraform" / "envs" / "prod.tfvars").write_text(
        'cost_center = "CC-4471"\ndata_classification = "confidential"\n', encoding="utf-8")
    (root / "architecture_decision.json").write_text(json.dumps({
        "selected_architecture": "AWS governed lakehouse",
        "selected_modules": ["storage-medallion-s3", "query-athena"],
    }), encoding="utf-8")

    inherited = synthesizer.inherit_from_run(str(root))
    values = inherited["values"]
    assert values["region"] == "eu-west-1"
    assert values["cost_center"] == "CC-4471"
    assert values["data_classification"] == "confidential"
    assert values["candidate_modules"] == ["storage-medallion-s3", "query-athena"]
    # Every inherited value says where it came from, so any of it can be rejected.
    assert inherited["sources"]["cost_center"] == "terraform/envs/prod.tfvars"
    assert inherited["sources"]["region"] == "terraform/terraform.tfvars"


def test_pipeline_shape_is_never_inherited(tmp_path):
    """Volume and latency are what make two pipelines different. Inheriting them would size
    the new pipeline for the old one's data."""
    root = tmp_path / "runs" / "base"
    (root / "terraform").mkdir(parents=True)
    (root / "requirements.json").write_text(json.dumps({
        "data_pipeline": {"data_volume": "9 TB per day"},
        "non_functional": {"latency": "real-time"},
    }), encoding="utf-8")
    values = synthesizer.inherit_from_run(str(root))["values"]
    assert "data_volume" not in values
    assert "latency" not in values
    assert not any("volume" in k or "latency" in k for k in values)


def test_review_required_placeholders_are_not_inherited(tmp_path):
    """Step 6 writes REVIEW_REQUIRED into staging/prod tfvars. Inheriting that literal would
    tag the new stack's spend to a cost centre named REVIEW_REQUIRED."""
    root = tmp_path / "runs" / "base"
    (root / "terraform" / "envs").mkdir(parents=True)
    (root / "terraform" / "envs" / "prod.tfvars").write_text(
        'cost_center = "REVIEW_REQUIRED"\n', encoding="utf-8")
    assert "cost_center" not in synthesizer.inherit_from_run(str(root))["values"]


def test_missing_base_run_files_are_not_an_error(tmp_path):
    """A partial inheritance is still worth having."""
    root = tmp_path / "runs" / "empty"
    root.mkdir(parents=True)
    inherited = synthesizer.inherit_from_run(str(root))
    assert inherited["values"] == {}
    assert "nothing inheritable" in synthesizer.format_inheritance(inherited)
