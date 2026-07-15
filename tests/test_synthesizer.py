import json
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


def test_compose_without_databricks_renders_byte_identical_root_templates(tmp_path):
    # databricks-workspace is the first module needing a non-AWS provider, which made
    # versions.tf/providers.tf/variables.tf conditional on present_ids instead of static
    # strings. Every composition that doesn't select it must be completely unaffected.
    out = tmp_path / "tf"
    synthesizer.compose(["storage-medallion-s3", "governance-observability"], "acme-dev",
                        str(out), owner="acme")
    assert (out / "versions.tf").read_text(encoding="utf-8") == synthesizer._VERSIONS
    assert (out / "providers.tf").read_text(encoding="utf-8") == synthesizer._PROVIDERS
    assert (out / "variables.tf").read_text(encoding="utf-8") == synthesizer._VARIABLES


def test_compose_with_databricks_adds_provider_and_account_id_variable(tmp_path):
    out = tmp_path / "tf"
    synthesizer.compose(["networking-vpc", "databricks-workspace"], "acme-dev",
                        str(out), owner="acme")
    versions = (out / "versions.tf").read_text(encoding="utf-8")
    providers = (out / "providers.tf").read_text(encoding="utf-8")
    variables = (out / "variables.tf").read_text(encoding="utf-8")
    assert 'source  = "databricks/databricks"' in versions
    assert 'provider "databricks"' in providers
    assert 'host       = "https://accounts.cloud.databricks.com"' in providers
    assert 'variable "databricks_account_id"' in variables
    # aws provider/variables are unaffected -- purely additive
    assert 'provider "aws"' in providers
    assert 'variable "name_prefix"' in variables


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


def test_allow_incomplete_bypass_is_actually_audited(tmp_path, monkeypatch):
    # Audit findings, 2026-07-03: allow_incomplete was documented as an "audited override" but
    # made zero audit_chain calls. This proves the bypass now writes a real, chained record —
    # not just that the docstring claims it does.
    import runs
    import audit_chain
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(synthesizer, "LOG_DIR", str(tmp_path / "logs"))

    res = synthesizer.synthesize("vague pipeline request", spec={"goal": "x"}, allow_incomplete=True)

    log_path = os.path.join(str(tmp_path / "logs"), "audit.jsonl")
    assert os.path.exists(log_path)
    ok, errors = audit_chain.verify(log_path)
    assert ok, errors
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8") if line.strip()]
    bypass = next(e for e in entries if e["status"] == "ALLOW_INCOMPLETE_BYPASS")
    assert bypass["component"] == "synthesizer"
    assert bypass["run_id"] == res["run"]["run_id"]
    assert "system_class" in bypass["requirements_missing"]
    assert bypass["architecture_decision_missing"]  # decision was never supplied either


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


# ---------------------------------------------------------------------------
# novel_resources / authored_content (docs/phase6_step1_authoring_scope.md sections 1/2):
# nothing a generator produces auto-ships on its first real appearance -- synthesize() itself
# only validates and composes what a caller's authoring step already produced, fail-closed on
# every way that step's own output could be wrong. Real `opa`/`terraform` not needed for these
# (gate_content()'s schema check needs the real `terraform providers schema -json` fetch, same
# skip condition schema_lint's own tests use).
# ---------------------------------------------------------------------------
import toolpath

TERRAFORM = toolpath.find_tool("terraform")

_NOVEL_DECISION = dict(COMPLETE_DECISION, novel_resources=[{
    "resource_type": "aws_dynamodb_table",
    "justification": "Requirement needs a low-latency key-value store; no catalog module provides one.",
    "alternatives_considered": ["aws_elasticache_cluster (rejected: overkill for this access pattern)"],
    "grounding_examples": ["storage-medallion-s3"],
}])

_VALID_DYNAMODB_HCL = (
    'resource "aws_dynamodb_table" "novel" {\n'
    '  name         = "novel-table"\n'
    '  billing_mode = "PAY_PER_REQUEST"\n'
    '  hash_key     = "id"\n'
    '  attribute {\n'
    '    name = "id"\n'
    '    type = "S"\n'
    '  }\n'
    '}\n'
)


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_synthesize_composes_a_valid_authored_novel_resource(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))

    res = synthesizer.synthesize(
        "airflow pipeline needing a low-latency lookup table",
        spec=COMPLETE_SPEC, decision=_NOVEL_DECISION, owner="data-platform",
        authored_content={"aws_dynamodb_table": _VALID_DYNAMODB_HCL},
    )

    authored_file = os.path.join(res["out_dir"], "authored_aws_dynamodb_table.tf")
    assert os.path.exists(authored_file)
    assert 'resource "aws_dynamodb_table" "novel"' in open(authored_file, encoding="utf-8").read()
    assert res["authored_resources"][0]["resource_type"] == "aws_dynamodb_table"
    assert res["authored_resources"][0]["decision_source"] == "novel_resources[0]"
    assert res["manifest"]["authored_resources"] == res["authored_resources"]


def test_synthesize_refuses_novel_resource_with_no_matching_authored_content(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))

    with pytest.raises(ValueError) as exc:
        synthesizer.synthesize(
            "airflow pipeline needing a low-latency lookup table",
            spec=COMPLETE_SPEC, decision=_NOVEL_DECISION, owner="data-platform",
            authored_content=None,
        )

    assert "no matching authored_content" in str(exc.value)
    assert not os.path.isdir(tmp_path / "runs")  # fails BEFORE any run workspace is created


def test_synthesize_refuses_authored_content_with_no_declared_blocks(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))

    with pytest.raises(ValueError) as exc:
        synthesizer.synthesize(
            "airflow pipeline needing a low-latency lookup table",
            spec=COMPLETE_SPEC, decision=_NOVEL_DECISION, owner="data-platform",
            authored_content={"aws_dynamodb_table": "# not actually any HCL block at all\n"},
        )

    assert "declares no" in str(exc.value)


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_synthesize_refuses_authored_content_with_hallucinated_type(tmp_path, monkeypatch):
    """The authoring step's own output resolving to a type that doesn't exist in any live
    provider schema this repo tracks -- stricter than G2's usual unknown_attribute case, this is
    the type itself being nonexistent (a typo/hallucination), not merely unreviewed."""
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))

    with pytest.raises(ValueError) as exc:
        synthesizer.synthesize(
            "airflow pipeline needing a low-latency lookup table",
            spec=COMPLETE_SPEC, decision=_NOVEL_DECISION, owner="data-platform",
            authored_content={"aws_dynamodb_table": (
                'resource "aws_totally_made_up_type" "novel" {\n  name = "x"\n}\n'
            )},
        )

    assert "failed G2 schema lint" in str(exc.value)


# ---------------------------------------------------------------------------
# Phase 7 Item 1 (docs/phase7_item1_module_unit_scope.md) -- authored_content's module-shaped
# unit. The flat str form above is completely unchanged (see tests above, all still passing
# untouched); these cover the new dict form: its own variable/output/locals namespace and
# path.module resolution via a real Terraform module boundary, not the flat root-file shape.
# The fixture mirrors modules/compute-glue-etl/main.tf's own aws_s3_object.script shape exactly
# (already proven G2-clean by test_schema_lint.py's real-catalog sweep) so these tests exercise
# the new checks, not an incidental G2 finding on invented HCL.
# ---------------------------------------------------------------------------

_MODULE_UNIT_DECISION = dict(COMPLETE_DECISION, novel_resources=[{
    "resource_type": "aws_s3_object",
    "justification": "A self-contained script-upload unit needing its own bucket input and a "
                      "companion asset file; no catalog module fits standalone.",
    "alternatives_considered": ["compute-glue-etl (rejected: needs the whole Glue job, not just this)"],
    "grounding_examples": ["compute-glue-etl"],
}])

_MODULE_UNIT_HCL = (
    'variable "name_prefix" {\n'
    '  type = string\n'
    '}\n\n'
    'variable "script_s3_bucket" {\n'
    '  type        = string\n'
    '  description = "Bucket to hold the uploaded script."\n'
    '}\n\n'
    'resource "aws_s3_object" "script" {\n'
    '  bucket = var.script_s3_bucket\n'
    '  key    = "${var.name_prefix}/scripts/etl.py"\n'
    '  source = "${path.module}/scripts/etl.py"\n'
    '  etag   = filemd5("${path.module}/scripts/etl.py")\n'
    '}\n'
)

_MODULE_UNIT_ASSETS = {"scripts/etl.py": "# placeholder starter script\n"}


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_validate_novel_resources_module_unit_blocks_missing_path_module_asset():
    decision = _MODULE_UNIT_DECISION
    authored_content = {"aws_s3_object": {
        "content": _MODULE_UNIT_HCL,
        "assets": {},  # missing scripts/etl.py -- the reference has nothing to resolve against
        "module_args": {"script_s3_bucket": '"test-bucket"'},
    }}
    with pytest.raises(ValueError) as exc:
        synthesizer._validate_novel_resources(decision, authored_content)
    assert "path.module-relative asset" in str(exc.value)
    assert "scripts/etl.py" in str(exc.value)


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_validate_novel_resources_module_unit_blocks_unwired_required_variable():
    decision = _MODULE_UNIT_DECISION
    authored_content = {"aws_s3_object": {
        "content": _MODULE_UNIT_HCL,
        "assets": dict(_MODULE_UNIT_ASSETS),
        "module_args": {},  # script_s3_bucket has no default, isn't well-known -- must block
    }}
    with pytest.raises(ValueError) as exc:
        synthesizer._validate_novel_resources(decision, authored_content)
    assert "required variable" in str(exc.value)
    assert "script_s3_bucket" in str(exc.value)
    # name_prefix is well-known-auto-wired and must NOT be reported as unresolved
    assert "name_prefix" not in str(exc.value)


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_validate_novel_resources_module_unit_passes_with_assets_and_module_args():
    decision = _MODULE_UNIT_DECISION
    authored_content = {"aws_s3_object": {
        "content": _MODULE_UNIT_HCL,
        "assets": dict(_MODULE_UNIT_ASSETS),
        "module_args": {"script_s3_bucket": '"test-bucket"'},
    }}
    authored_resources = synthesizer._validate_novel_resources(decision, authored_content)
    assert authored_resources[0]["form"] == "module"
    assert authored_resources[0]["assets"] == _MODULE_UNIT_ASSETS
    assert authored_resources[0]["module_args"] == {"script_s3_bucket": '"test-bucket"'}


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_compose_writes_module_unit_into_its_own_directory_with_assets_and_call_block(tmp_path):
    authored_resources = synthesizer._validate_novel_resources(_MODULE_UNIT_DECISION, {
        "aws_s3_object": {
            "content": _MODULE_UNIT_HCL,
            "assets": dict(_MODULE_UNIT_ASSETS),
            "module_args": {"script_s3_bucket": '"test-bucket"'},
        }
    })
    out = tmp_path / "tf"
    synthesizer.compose([], "acme-dev", str(out), owner="acme", authored_resources=authored_resources)

    unit_main = out / "authored_modules" / "aws_s3_object" / "main.tf"
    unit_asset = out / "authored_modules" / "aws_s3_object" / "scripts" / "etl.py"
    call_file = out / "authored_aws_s3_object.tf"
    assert unit_main.read_text(encoding="utf-8") == _MODULE_UNIT_HCL  # already newline-terminated
    assert unit_asset.read_text(encoding="utf-8") == "# placeholder starter script\n"
    # terraform fmt (compose()'s best-effort formatting pass) aligns `=` signs with padding --
    # check content, not exact spacing.
    call_text = call_file.read_text(encoding="utf-8")
    assert 'module "authored_aws_s3_object" {' in call_text
    assert '"./authored_modules/aws_s3_object"' in call_text
    # name_prefix auto-wired to the root local, script_s3_bucket wired via the explicit override
    assert "local.name_prefix" in call_text
    assert '"test-bucket"' in call_text


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_synthesize_composes_a_valid_module_shaped_authored_unit(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))

    res = synthesizer.synthesize(
        "airflow pipeline needing a standalone script-upload unit",
        spec=COMPLETE_SPEC, decision=_MODULE_UNIT_DECISION, owner="data-platform",
        authored_content={"aws_s3_object": {
            "content": _MODULE_UNIT_HCL,
            "assets": dict(_MODULE_UNIT_ASSETS),
            "module_args": {"script_s3_bucket": '"test-bucket"'},
        }},
    )

    assert res["authored_resources"][0]["form"] == "module"
    unit_dir = os.path.join(res["out_dir"], "authored_modules", "aws_s3_object")
    assert os.path.isdir(unit_dir)
    assert os.path.exists(os.path.join(unit_dir, "scripts", "etl.py"))
    assert res["manifest"]["authored_resources"] == res["authored_resources"]


# ---------------------------------------------------------------------------
# Phase 7 Item 2 (docs/phase7_generation_engine_plan.md) -- synthesize()'s zero-catalog path.
# Before this fix, the ONLY way to get a catalog-free, purely-authored composition was to bypass
# select_modules() and call compose() directly (tests/test_teardown_regression_harness.py's own
# workaround, explicitly documented there as a deliberate bypass of select_modules() "which would
# run match_modules() ... plus the always-added governance-observability module"). These tests
# call the real public synthesize() entry point the same way that harness called the private
# path, and confirm it now reaches an equivalent catalog-free result on its own.
# ---------------------------------------------------------------------------

_ZERO_CATALOG_DECISION = dict(COMPLETE_DECISION, selected_modules=[], novel_resources=[{
    "resource_type": "aws_s3_object",
    "justification": "Requirement fully covered by a standalone authored unit; no catalog "
                      "module needed at all.",
    "alternatives_considered": ["compute-glue-etl (rejected: needs the whole Glue job, not just this)"],
    "grounding_examples": ["compute-glue-etl"],
}])


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_synthesize_composes_catalog_free_via_explicit_empty_selection(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))

    res = synthesizer.synthesize(
        "standalone script-upload unit, no catalog modules needed",
        spec=COMPLETE_SPEC, decision=_ZERO_CATALOG_DECISION, owner="data-platform",
        authored_content={"aws_s3_object": {
            "content": _MODULE_UNIT_HCL,
            "assets": dict(_MODULE_UNIT_ASSETS),
            "module_args": {"script_s3_bucket": '"test-bucket"'},
        }},
    )

    # Zero catalog modules -- no keyword-matched picks, and no silently-added
    # governance-observability, unlike an INFERRED (no explicit selection at all) composition.
    assert res["modules"] == []
    assert "governance-observability" not in res["modules"]
    assert res["authored_resources"][0]["form"] == "module"
    unit_dir = os.path.join(res["out_dir"], "authored_modules", "aws_s3_object")
    assert os.path.isdir(unit_dir)
    assert os.path.exists(os.path.join(unit_dir, "scripts", "etl.py"))


def test_synthesize_refuses_when_nothing_selected_and_nothing_authored(tmp_path, monkeypatch):
    """selected_modules=[] with no novel_resources at all is caught even earlier than
    synthesize()'s own guard: architecture_decision.py's own gate (docs/
    phase7_generation_engine_plan.md item 2's companion fix -- a decision must select
    SOMETHING, catalog or authored) refuses the record itself as incomplete, before synthesize()
    gets far enough to run its own "nothing to compose" check."""
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    decision = dict(COMPLETE_DECISION, selected_modules=[])

    with pytest.raises(archdec.ArchitectureDecisionIncomplete) as exc:
        synthesizer.synthesize("nothing at all", spec=COMPLETE_SPEC, decision=decision)

    assert any("selected_modules" in m and "novel_resources" in m for m in exc.value.missing)
    assert not os.path.isdir(tmp_path / "runs")


def test_synthesize_refuses_zero_catalog_with_only_invalid_novel_resources(tmp_path, monkeypatch):
    """A decision can name novel_resources to pass archdec's own gate, but if none of those
    entries ever resolve to real authored_content, synthesize()'s existing
    _validate_novel_resources() fail-closed check still catches it -- this proves the two gates
    compose correctly rather than one silently papering over the other."""
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    decision = dict(COMPLETE_DECISION, selected_modules=[], novel_resources=[{
        "resource_type": "aws_dynamodb_table",
        "justification": "test",
        "alternatives_considered": ["x"],
    }])

    with pytest.raises(ValueError) as exc:
        synthesizer.synthesize("nothing at all", spec=COMPLETE_SPEC, decision=decision,
                               authored_content=None)

    assert "no matching authored_content" in str(exc.value)
    assert not os.path.isdir(tmp_path / "runs")


def test_select_modules_explicit_empty_list_selects_nothing():
    # explicit_ids=[] is checked by identity, not truthiness (the actual Item 2 fix) -- it must
    # take the explicit-selection branch (chosen=[]), not fall through to keyword matching.
    # with_governance is a separate, synthesize()-level decision (bool(explicit_selection)),
    # not this function's own default -- passed explicitly here to isolate what's under test.
    chosen = synthesizer.select_modules("irrelevant request text", explicit_ids=[], with_governance=False)
    assert chosen == []


def test_synthesize_with_no_novel_resources_is_unaffected(tmp_path, monkeypatch):
    """Backward-compatible: a decision with no novel_resources key at all (every requirement
    fully covered by the catalog, which is every requirement before this scope existed) composes
    exactly as before -- authored_resources is simply empty."""
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))

    res = synthesizer.synthesize(
        "airflow pipeline with data quality and schema enforcement",
        spec=COMPLETE_SPEC, decision=COMPLETE_DECISION, owner="data-platform",
    )

    assert res["authored_resources"] == []
    assert res["manifest"]["authored_resources"] == []


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


def test_synthesize_wires_stated_budget_into_governance_module(tmp_path, monkeypatch):
    # Audit finding 2026-07-04: requirements.json's stated budget was captured as evidence
    # and never wired anywhere -- governance-observability always used its own $100 default.
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    spec = {**COMPLETE_SPEC, "non_functional": {**COMPLETE_SPEC["non_functional"], "budget": "$1.00/mo hard cap"}}
    res = synthesizer.synthesize("airflow pipeline", spec=spec, decision=COMPLETE_DECISION, owner="sandbox-test")
    main_tf = open(os.path.join(res["out_dir"], "main.tf"), encoding="utf-8").read()
    assert "monthly_budget_usd = 1" in main_tf
    assert "monthly_budget_usd" not in res["review"]  # no longer an unwired REVIEW item
    comp_md = open(os.path.join(res["out_dir"], "COMPOSITION.md"), encoding="utf-8").read()
    assert "1.00/mo hard cap" in comp_md   # source text recorded as evidence


def test_synthesize_leaves_budget_as_review_when_unparseable(tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    spec = {**COMPLETE_SPEC, "non_functional": {**COMPLETE_SPEC["non_functional"],
                                                 "budget": "deferred: pending finance sign-off cycle"}}
    res = synthesizer.synthesize("airflow pipeline", spec=spec, decision=COMPLETE_DECISION, owner="sandbox-test")
    main_tf = open(os.path.join(res["out_dir"], "main.tf"), encoding="utf-8").read()
    assert "monthly_budget_usd =" not in main_tf  # never guessed -- no wired assignment
    assert "# REVIEW: set monthly_budget_usd" in main_tf  # stays an explicit review item
    assert any("monthly_budget_usd" in r for r in res["review"])
