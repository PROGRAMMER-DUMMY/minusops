"""
Architecture synthesizer — compose vetted modules into a governed Terraform workspace.

This is the code half of the architect path: given requirements, it selects matching modules
from the registry (core/generation/modules.py), creates a run workspace, and writes a composed Terraform
root that wires the obvious shared inputs and flags the rest for review. The output is a
*scaffold the architect refines and the deploy gate validates* — never an apply-without-review
shortcut. It replaces the single hardcoded blueprint with requirement-driven composition.
"""
import hashlib
import os
import re
import json
import shutil
import sys
import getpass
import datetime

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)

import architecture_decision as archdec
import audit_chain
import modules as module_registry
import requirements as reqgate
import runs
import source_guard

LOG_DIR = os.path.join(os.getcwd(), ".agents", "logs")


def _audit_allow_incomplete_bypass(requirements_text, spec, decision, run):
    """The allow_incomplete override is documented as an 'audited' escape hatch — this is what
    actually makes that true. Writes into the SAME chain plan_gate/approval use (audit_chain.py's
    own doctrine: one continuous chain across the control plane), so a reviewer sees every
    bypass alongside every deploy decision, not in a separate, easy-to-miss log."""
    _, req_missing = reqgate.validate(spec or {})
    _, dec_missing = archdec.validate(decision or {})
    rec = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "operator": getpass.getuser(),
        "component": "synthesizer",
        "action": "synthesize",
        "status": "ALLOW_INCOMPLETE_BYPASS",
        "run_id": (run or {}).get("run_id", ""),
        "request": requirements_text,
        "requirements_missing": req_missing,
        "architecture_decision_missing": dec_missing,
    }
    os.makedirs(LOG_DIR, exist_ok=True)
    try:
        audit_chain.append(os.path.join(LOG_DIR, "audit.jsonl"), rec)
    except Exception as exc:
        print(f"[architect] WARNING: could not write audit record: {exc}", file=sys.stderr)

# A small set of obvious cross-module wirings applied when both modules are present.
# Module block labels use underscores (hyphens are awkward in HCL references).
_STORAGE = "module.storage_medallion_s3"
_NETWORKING = "module.networking_vpc"


def _label(module_id):
    return module_id.replace("-", "_")


def select_modules(requirements, explicit_ids=None, with_governance=True):
    """Pick modules for the requirements. Explicit ids win; otherwise match by keyword. A
    governance/observability baseline is added unless already chosen."""
    if explicit_ids:
        chosen = [module_registry.get_module(i) for i in explicit_ids]
        chosen = [m for m in chosen if m]
    else:
        chosen = module_registry.match_modules(requirements)
    ids = {m["id"] for m in chosen}
    if with_governance and "governance-observability" not in ids:
        gov = module_registry.get_module("governance-observability")
        if gov:
            chosen.append(gov)
    return chosen


_COMPUTE = "module.compute_glue_etl"


def _module_args(module_id, present_ids, monthly_budget_usd=0):
    args = {"name_prefix": "local.name_prefix", "tags": "local.tags"}
    has_storage = "storage-medallion-s3" in present_ids
    has_compute = "compute-glue-etl" in present_ids
    has_gov = "governance-observability" in present_ids
    has_networking = "networking-vpc" in present_ids
    _GOV = "module.governance_observability"
    if has_networking and module_id == "orchestrator-mwaa":
        # Closes a real gap: orchestrator-mwaa has never had a reusable networking module to
        # attach to (runs/manual-mwaa-network-scratch/ was hand-built specifically to work
        # around this, deliberately outside the governed catalog). With both modules present,
        # this now wires end-to-end with no `# REVIEW:` comment for either input.
        args["subnet_ids"] = f"{_NETWORKING}.private_subnet_ids"
        args["security_group_ids"] = f"[{_NETWORKING}.default_security_group_id]"
    if has_networking and module_id == "databricks-workspace":
        # Same wiring shape as orchestrator-mwaa above -- databricks_mws_networks (inside this
        # module) needs the identical vpc_id/subnet_ids/security_group_ids shape.
        args["vpc_id"] = f"{_NETWORKING}.vpc_id"
        args["subnet_ids"] = f"{_NETWORKING}.private_subnet_ids"
        args["security_group_ids"] = f"[{_NETWORKING}.default_security_group_id]"
    if module_id == "governance-observability" and monthly_budget_usd:
        # Wire the operator's actual stated budget constraint (requirements.json) into the
        # guardrail this module provisions -- previously this was always the module's own
        # unwired default, disconnected from anything the operator said (audit finding
        # 2026-07-04). Only set when a real number was parsed; otherwise stays a REVIEW item.
        args["monthly_budget_usd"] = f"{monthly_budget_usd:g}"
    if module_id in ("storage-medallion-s3", "dq-great-expectations", "query-athena"):
        # Folded into bucket names so two runs sharing a name_prefix don't collide
        # (2026-07-04 audit finding: account_id alone doesn't differentiate our own runs;
        # 2026-07-06: the same fix extended to dq-great-expectations/query-athena, which an
        # exhaustive read found had the identical unsuffixed bucket pattern left unfixed).
        args["run_id"] = "var.run_id"
    if has_storage and module_id == "compute-glue-etl":
        args["script_s3_bucket"] = f'{_STORAGE}.bucket_names["bronze"]'
        # A default starter job so the pipeline is complete-by-construction (a real Glue
        # job + uploaded starter script). The operator extends/renames it before production.
        args["jobs"] = '{ bronze_to_silver = "scripts/bronze_to_silver.py" }'
        if has_gov:
            # Route job failures to the governance alerts topic (BP 6.2/6.3).
            # enable_alarms is a separate static bool because Terraform count cannot
            # depend on the computed topic ARN.
            args["alarm_sns_topic_arn"] = f"{_GOV}.alerts_topic_arn"
            args["enable_alarms"] = "true"
    if has_storage and module_id == "query-athena":
        args["results_kms_key_arn"] = f"{_STORAGE}.kms_key_arn"
    if has_storage and module_id == "dq-great-expectations":
        args["target_buckets"] = f"values({_STORAGE}.bucket_names)"
        args["script_s3_bucket"] = f'{_STORAGE}.bucket_names["bronze"]'
    if has_compute and module_id == "orchestrator-stepfunctions":
        # Wire orchestration to the real Glue jobs (creates the dependency edge + a runnable
        # starter state machine, so conformance is not 'unwired' and the diagram edge is solid).
        args["glue_job_names"] = f"values({_COMPUTE}.glue_job_names)"
        args["task_role_arns"] = f"{_COMPUTE}.glue_job_arns"
    # Scale-tier modules (compaction / Iceberg / Firehose / EMR Serverless) wire onto the
    # medallion zones when storage is present.
    if has_storage and module_id == "compaction-glue":
        args["script_s3_bucket"] = f'{_STORAGE}.bucket_names["bronze"]'
        args["target_buckets"] = f"values({_STORAGE}.bucket_names)"
    if has_storage and module_id == "table-format-iceberg":
        args["table_bucket"] = f'{_STORAGE}.bucket_names["gold"]'
    if has_storage and module_id == "ingest-firehose":
        args["destination_bucket_arn"] = f'"arn:aws:s3:::${{{_STORAGE}.bucket_names["bronze"]}}"'
    if has_storage and module_id == "compute-emr-serverless":
        args["target_buckets"] = f"values({_STORAGE}.bucket_names)"
    return args


def _render_main(chosen, present_ids, monthly_budget_usd=0):
    lines = [
        "# Composed by MinusOps architect synthesis — vetted modules assembled for the gathered",
        "# requirements. Review the items marked REVIEW, then run the deploy gate:",
        "#   python core/governance/plan_gate.py verify --dir <this dir> --policy-mode production",
        "",
        "locals {",
        "  name_prefix = var.name_prefix",
        '  tags        = merge({ owner = var.owner, environment = var.environment, managed_by = "minusops" }, var.tags)',
        "}",
        "",
    ]
    for m in chosen:
        args = _module_args(m["id"], present_ids, monthly_budget_usd=monthly_budget_usd)
        review = [i for i in m["inputs"] if i not in args]
        lines.append(f'# {m["title"]}  ({", ".join(m["services"])})')
        lines.append(f'module "{_label(m["id"])}" {{')
        lines.append(f'  source = "./modules/{m["id"]}"')
        for k, v in args.items():
            lines.append(f"  {k} = {v}")
        for r in review:
            lines.append(f"  # REVIEW: set {r}")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


_VERSIONS_HEADER = '''terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
'''

_VERSIONS_FOOTER = '''  }
}
'''

_VERSIONS = _VERSIONS_HEADER + _VERSIONS_FOOTER

_PROVIDERS = '''provider "aws" {
  region = var.region
  default_tags {
    # Showback: every resource carries the owning team and the run that created it,
    # so Cost Explorer can attribute actual spend per pipeline (FinOps allocation).
    tags = {
      managed_by = "minusops"
      owner      = var.owner
      run_id     = var.run_id
    }
  }
}
'''

_VARIABLES = '''variable "name_prefix" {
  type        = string
  description = "Prefix for resource names, e.g. data-platform-dev."
}

variable "owner" {
  type        = string
  description = "Owning team / cost center (FinOps + audit)."
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "run_id" {
  type        = string
  default     = ""
  description = "MinusOps run id stamped onto every resource for per-pipeline cost showback."
}

variable "daily_data_gb" {
  type        = number
  default     = 0
  description = "Declared daily data volume in GB (from requirements). Drives the S3 usage estimate and cost-per-GB unit economics; 0 = undeclared."
}
'''

# databricks-workspace is the first module needing a non-AWS provider. Terraform child modules
# cannot declare their own `provider {}` blocks with configuration -- only the root composition
# can -- so these three templates append conditionally on present_ids rather than the module
# bringing its own. Every composition without databricks-workspace renders byte-identical output
# to the plain constants above (see test_synthesizer.py's regression test for this).
_DATABRICKS_VERSION = '''    databricks = {
      source  = "databricks/databricks"
      version = ">= 1.0"
    }
'''

_DATABRICKS_PROVIDER = '''
provider "databricks" {
  host       = "https://accounts.cloud.databricks.com"
  account_id = var.databricks_account_id
}
'''

_DATABRICKS_VARIABLE = '''
variable "databricks_account_id" {
  type        = string
  description = "Databricks account ID (top-right of https://accounts.cloud.databricks.com/)."
}
'''


def _render_versions(present_ids):
    if "databricks-workspace" not in present_ids:
        return _VERSIONS
    return _VERSIONS_HEADER + _DATABRICKS_VERSION + _VERSIONS_FOOTER


def _render_providers(present_ids):
    if "databricks-workspace" not in present_ids:
        return _PROVIDERS
    return _PROVIDERS + _DATABRICKS_PROVIDER


def _render_variables(present_ids):
    if "databricks-workspace" not in present_ids:
        return _VARIABLES
    return _VARIABLES + _DATABRICKS_VARIABLE


# Canonical volume/budget parsing lives with the requirements schema; re-exported for callers.
parse_daily_gb = reqgate.parse_daily_gb
parse_budget_usd = reqgate.parse_budget_usd


def compose(module_ids, name_prefix, out_dir, owner="", request="",
            run_id="", daily_data_gb=0, volume_source="",
            monthly_budget_usd=0, budget_source="", authored_resources=None):
    """Write a composed Terraform root into out_dir from the selected modules, plus any
    generation-time-authored novel resources (docs/phase6_step1_authoring_scope.md section 2).
    `authored_resources` is a list of {resource_type, content, justification, decision_source,
    content_hash} -- already lint-checked by the caller (synthesize()), never linted here; this
    function only writes what it's given."""
    authored_resources = authored_resources or []
    chosen = [module_registry.get_module(i) for i in module_ids]
    chosen = [m for m in chosen if m]
    if not chosen and not authored_resources:
        # Real, pre-existing gap fixed here: this check predates authored_resources (docs/
        # phase6_step1_authoring_scope.md) and was never updated for it -- a composition can be
        # entirely authored content with zero catalog picks (the Step 5 regression harness is
        # exactly this case), which is not "nothing valid to compose," only "nothing FROM THE
        # CATALOG to compose."
        raise ValueError("no valid modules or authored resources selected")
    present_ids = {m["id"] for m in chosen}

    os.makedirs(out_dir, exist_ok=True)
    dst_modules = os.path.join(out_dir, "modules")
    os.makedirs(dst_modules, exist_ok=True)
    for m in chosen:
        src = module_registry.module_dir(m["id"])
        dst = os.path.join(dst_modules, m["id"])
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

    def _w(name, text):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    _w("versions.tf", _render_versions(present_ids))
    _w("providers.tf", _render_providers(present_ids))
    _w("variables.tf", _render_variables(present_ids))
    _w("main.tf", _render_main(chosen, present_ids, monthly_budget_usd=monthly_budget_usd))

    # Resolved inputs for this run — plans work without hand-written tfvars, and the
    # declared volume flows into cost estimation (S3 GB-months, cost/GB economics).
    tfvars = [
        f'name_prefix = "{name_prefix}"',
        f'owner       = "{owner or "unknown"}"',
        f'run_id      = "{run_id}"',
    ]
    if daily_data_gb:
        tfvars.append(f"daily_data_gb = {daily_data_gb:g}"
                      + (f'  # from requirements: "{volume_source}" (upper bound)' if volume_source else ""))
    _w("terraform.tfvars", "\n".join(tfvars) + "\n")

    # Authored (novel) resources -- each gets its own file at the composition root: a discrete,
    # independently reviewable unit (docs/phase6_step1_authoring_scope.md section 2 item 1), not
    # folded into main.tf and not wrapped in a synthetic child module -- these are typically
    # standalone resources, and inventing a variables.tf/module-input contract for a resource
    # type nobody has designed call-site wiring for yet would be scope creep past what this
    # step actually needs. Written before the fmt pass below so authored HCL gets the same
    # fmt-clean treatment as every catalog module's rendered output.
    for entry in authored_resources:
        text = entry["content"]
        if not text.endswith("\n"):
            text += "\n"
        _w(f"authored_{entry['resource_type']}.tf", text)

    # Emit fmt-clean output so `plan_gate verify` (terraform fmt -check) passes without a
    # manual formatting step. Best-effort: composition still succeeds without terraform.
    try:
        import subprocess
        import toolpath
        tf_bin = toolpath.find_tool("terraform")
        if tf_bin:
            subprocess.run([tf_bin, "fmt", "-recursive", "."], cwd=out_dir,
                           capture_output=True, timeout=60)
    except Exception:
        pass

    review = []
    for m in chosen:
        args = _module_args(m["id"], present_ids, monthly_budget_usd=monthly_budget_usd)
        review += [f"{m['id']}: {i}" for i in m["inputs"] if i not in args]
    doc = ["# Composition", "", f"Request: {request or '(none)'}", "",
           "## Modules", ""]
    for m in chosen:
        doc.append(f"- **{m['id']}** — {m['title']} ({', '.join(m['services'])})")
    if monthly_budget_usd:
        doc += ["", "## Budget guardrail", "",
                f"`governance-observability.monthly_budget_usd` set to **{monthly_budget_usd:g}** "
                f"from requirements.json's stated budget: \"{budget_source}\"."]
    doc += ["", "## Review before deploy", "",
            "Wire these module inputs to real values (the architect/operator completes them):", ""]
    doc += [f"- `{r}`" for r in review] or ["- (none — common inputs auto-wired)"]
    if authored_resources:
        doc += ["", "## Authored (novel) resources", "",
                "Generated for a requirement no catalog module covers -- reviewed the same way "
                "a new module would be, not exempted for being newly authored:", ""]
        for entry in authored_resources:
            doc.append(f"- **{entry['resource_type']}** (`authored_{entry['resource_type']}.tf`) "
                       f"-- {entry.get('justification') or '(no justification recorded)'}")
    doc += ["", "## Next", "",
            "```bash", f"python core/governance/plan_gate.py verify --dir {out_dir} --policy-mode production",
            f"python core/governance/plan_gate.py plan   --dir {out_dir}", "```",
            "", "The composed Terraform is governed by the same gate (validate + native SEC scan + "
            "production external scanner evidence + plan-hash approval + BCM cost). Nothing applies without human review."]
    _w("COMPOSITION.md", "\n".join(doc) + "\n")

    return {
        "out_dir": out_dir,
        "modules": [m["id"] for m in chosen],
        "review": review,
        "authored_resources": [
            {"resource_type": e["resource_type"], "decision_source": e["decision_source"],
             "content_hash": e["content_hash"]}
            for e in authored_resources
        ],
    }


def _ensure_empty_or_overwrite(terraform_dir, overwrite=False):
    if not os.path.isdir(terraform_dir):
        return
    entries = [name for name in os.listdir(terraform_dir) if name not in {".terraform"}]
    if entries and not overwrite:
        raise ValueError(f"terraform directory is not empty: {terraform_dir}; pass --overwrite after review")


def _write_manifest(terraform_dir, result, requirements_text, decision=None):
    files = sorted(source_guard.source_hashes(terraform_dir).keys())
    if "minus-generated.json" not in files:
        files.append("minus-generated.json")
    manifest = {
        "blueprint": "synthesized",
        "terraform_dir": terraform_dir,
        "requirements": requirements_text,
        "architecture": (decision or {}).get("selected_architecture", ""),
        "modules": result["modules"],
        "authored_resources": result.get("authored_resources", []),
        "review": result["review"],
        "files": files,
    }
    with open(os.path.join(terraform_dir, "minus-generated.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    source_guard.write_baseline(terraform_dir, label="synthesized", extra={
        "modules": result["modules"],
        "authored_resources": result.get("authored_resources", []),
    })
    return manifest


def _update_workflow(run, result):
    path = os.path.join(run["root"], "workflow.json")
    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, json.JSONDecodeError):
        record = {"run": run}
    record["terraform_generated"] = True
    record["generation_blocked"] = False
    record["synthesized_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record["synthesis"] = {
        "modules": result["modules"],
        "manifest": os.path.join(run["terraform_dir"], "minus-generated.json"),
        "out_dir": result["out_dir"],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
    return record


def _validate_novel_resources(decision, authored_content):
    """Resolve architecture_decision.json's `novel_resources` (docs/
    phase6_step1_authoring_scope.md section 1) against caller-supplied `authored_content` --
    real HCL text for each declared novel resource type, keyed by `resource_type`. Fail-closed,
    unconditionally, before anything else runs (section 2 item 3):

      - A novel_resources entry with no matching authored_content -> hard block. This is what
        keeps `novel_resources` a human-reviewed DECISION record, never a generation trigger by
        itself: declaring intent to add a resource type is not the same as it being safe to
        write, and synthesis refuses to fill the gap silently.
      - Authored content declaring zero resource/data blocks at all -> hard block. gate_content()
        itself must stay silent on this for gate_module()'s sake (a hand-pinned module with zero
        blocks is a real, if rare, non-blocking case there) -- but an authoring-step call site
        was asked to produce exactly one resource, so zero blocks IS the failure, checked here
        rather than inside gate_content()'s own general contract.
      - Anything that parses and resolves to a real type flows into gate_content() (G2) for the
        actual schema-content check -- a hallucinated/nonexistent type surfaces there as
        `unknown_type`, already blocking.

    Returns the list of authored_resources dicts `compose()`/`_write_manifest()` expect.
    """
    # Imported lazily, not at module level, to avoid a real circular import (found running
    # tests/test_schema_watch.py standalone, pre-existing since Step 1, not introduced here):
    # schema_watch.py imports synthesizer; schema_lint.py imports FROM schema_watch
    # (_fetch_schema et al); a module-level `import schema_lint` here completes the cycle in
    # the one order that breaks (schema_watch imported first, before schema_lint has fully
    # initialized). module_provenance.py already uses this exact same lazy-import fix for the
    # identical reason -- see its own `pin` CLI handler.
    import schema_lint
    novel_resources = (decision or {}).get("novel_resources") or []
    authored_content = authored_content or {}
    authored_resources = []
    for i, entry in enumerate(novel_resources):
        resource_type = entry.get("resource_type", "")
        content = authored_content.get(resource_type)
        if content is None:
            raise ValueError(
                f"novel_resources entry '{resource_type}' has no matching authored_content -- "
                "fail-closed: synthesis refuses to proceed without authored HCL for every "
                "declared novel resource"
            )
        source_label = f"novel_resources[{i}]:{resource_type}"
        if not list(schema_lint.iter_hcl_blocks(content)):
            raise ValueError(
                f"authored content for novel resource '{resource_type}' declares no "
                f"resource/data blocks at all -- refusing to synthesize (source: {source_label})"
            )
        lint_result = schema_lint.gate_content(content, source_label)
        if lint_result["blocking"]:
            raise ValueError(
                f"authored content for novel resource '{resource_type}' failed G2 schema lint "
                f"({source_label}): {lint_result['findings']}"
            )
        authored_resources.append({
            "resource_type": resource_type,
            "content": content,
            "justification": entry.get("justification", ""),
            "decision_source": f"novel_resources[{i}]",
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        })
    return authored_resources


def synthesize(requirements_text, spec=None, decision=None, allow_incomplete=False,
               name_prefix=None, explicit_ids=None, owner="data-platform", cloud="aws",
               target_run=None, overwrite=False, validate=False, authored_content=None):
    """
    End-to-end: enforce the requirements and architecture decision gates -> select the modules
    approved in that decision -> create a run workspace -> compose Terraform into it, and record
    requirements.json / architecture_decision.json alongside the run.

    `spec` is the structured requirements record (from grill-me). Generation is **fail-closed**:
    without complete requirements and a complete architecture decision it raises the matching
    gate exception listing what's unanswered. `allow_incomplete` is an explicit, audited override
    (demo/testing only).

    `authored_content` (docs/phase6_step1_authoring_scope.md section 1/2) is an optional
    `{resource_type: hcl_text}` map supplying real, already-authored HCL for every entry in
    `decision["novel_resources"]` -- this function does not itself author anything; it only
    validates and composes what a caller's authoring step already produced. See
    `_validate_novel_resources()` for the fail-closed contract.
    """
    if not allow_incomplete:
        reqgate.require(spec or {})        # raises RequirementsIncomplete(missing) -> caller surfaces it
        archdec.require(decision or {})
    decision_module_ids = (decision or {}).get("selected_modules") or None
    if not allow_incomplete and explicit_ids and set(explicit_ids) != set(decision_module_ids or []):
        raise ValueError("--module overrides must match architecture_decision.json selected_modules")
    chosen = select_modules(requirements_text, explicit_ids=decision_module_ids or explicit_ids)
    requested_ids = set(decision_module_ids or explicit_ids or [])
    chosen_ids = {m["id"] for m in chosen}
    unknown_ids = sorted(requested_ids - chosen_ids)
    if unknown_ids:
        raise ValueError("unknown selected module(s): " + ", ".join(unknown_ids))
    if not chosen:
        raise ValueError("no modules matched the requirements; refine the request or pass --module")
    authored_resources = _validate_novel_resources(decision, authored_content)
    run = target_run or runs.new_run(blueprint="synthesized", request=requirements_text, cloud=cloud)
    if allow_incomplete:
        _audit_allow_incomplete_bypass(requirements_text, spec, decision, run)
    _ensure_empty_or_overwrite(run["terraform_dir"], overwrite=overwrite)
    if spec:
        reqgate.write(run["root"], spec, gathered_by=owner)
    if decision:
        archdec.write(run["root"], decision, decided_by=owner)
    prefix = name_prefix or f"{module_registry._WORD.findall(owner.lower())[0] if owner else 'app'}-dev"
    daily_gb, volume_source = parse_daily_gb(spec)
    budget_usd, budget_source = parse_budget_usd(spec)
    result = compose([m["id"] for m in chosen], prefix, run["terraform_dir"], owner=owner,
                     request=requirements_text, run_id=run.get("run_id", ""),
                     daily_data_gb=daily_gb, volume_source=volume_source,
                     monthly_budget_usd=budget_usd, budget_source=budget_source,
                     authored_resources=authored_resources)
    result["manifest"] = _write_manifest(run["terraform_dir"], result, requirements_text, decision=decision)
    result["workflow"] = _update_workflow(run, result)
    result["run"] = run
    result["requirements_recorded"] = bool(spec)
    result["architecture_decision_recorded"] = bool(decision)
    if validate:
        # Non-mutating, credential-free self-check: prove the composed config is well-formed
        # before it reaches the deploy gate. Never fatal here — recorded for readiness.
        import tf_validate
        result["validation"] = tf_validate.validate_and_record(run["terraform_dir"])
    return result


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Compose vetted modules into governed Terraform")
    ap.add_argument("requirements", help="free-text requirements summary (from grill-me)")
    ap.add_argument("--requirements-file", default=None,
                    help="path to the requirements.json gathered by grill-me (required unless --allow-incomplete)")
    ap.add_argument("--decision-file", default=None,
                    help="path to architecture_decision.json with researched choice and selected modules (required unless --allow-incomplete)")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="audited override: synthesize without a complete requirements record (demo/testing)")
    ap.add_argument("--name", default=None, help="resource name prefix")
    ap.add_argument("--owner", default="data-platform")
    ap.add_argument("--module", action="append", default=[], help="force a specific module id (repeatable)")
    ap.add_argument("--run", default=None, help="existing run id/prefix to synthesize into instead of creating a new run")
    ap.add_argument("--overwrite", action="store_true", help="overwrite a non-empty target Terraform directory after review")
    ap.add_argument("--no-validate", action="store_true",
                    help="skip the offline `terraform validate` self-check after composing")
    args = ap.parse_args(argv)

    spec = reqgate.load(args.requirements_file) if args.requirements_file else None
    decision = archdec.load(args.decision_file) if args.decision_file else None
    target_run = runs.get_run(args.run) if args.run else None
    if args.run and not target_run:
        print(f"[architect] REFUSED - run not found: {args.run}")
        return 2
    try:
        res = synthesize(args.requirements, spec=spec, decision=decision, allow_incomplete=args.allow_incomplete,
                         name_prefix=args.name, explicit_ids=args.module or None, owner=args.owner,
                         target_run=target_run, overwrite=args.overwrite, validate=not args.no_validate)
    except reqgate.RequirementsIncomplete as exc:
        print("[architect] REFUSED — requirements gate. Run grill-me first; unanswered:")
        for m in exc.missing:
            print(f"    - {m}")
        print("    (or pass --requirements-file <requirements.json>, or --allow-incomplete for a demo)")
        return 2
    except archdec.ArchitectureDecisionIncomplete as exc:
        print("[architect] REFUSED - architecture decision gate. Research and record the choice first; unanswered:")
        for m in exc.missing:
            print(f"    - {m}")
        print("    (or pass --decision-file <architecture_decision.json>, or --allow-incomplete for a demo)")
        return 2
    print("[architect] composed modules:", ", ".join(res["modules"]))
    print("[architect] terraform     :", res["out_dir"])
    if res["review"]:
        print("[architect] review inputs :")
        for r in res["review"]:
            print(f"    - {r}")
    if res.get("validation"):
        import tf_validate
        print("[architect] " + tf_validate._format(res["validation"]))
    print(f"[architect] next          : python core/governance/plan_gate.py verify --dir {res['out_dir']} --policy-mode production")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
