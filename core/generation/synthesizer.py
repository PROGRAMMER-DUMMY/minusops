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


def write_authoring_record(run, resource_type, justification, schema_block, grounding_examples,
                            raw_output, verdict, detail="", driving_agent=""):
    """Phase 7 Item 5 (docs/phase7_item5_authoring_scope.md section 1): the record of one
    authoring attempt -- the context supplied (schema + grounding), the content returned, and the
    gate verdict on it -- written so a specific authoring decision is reconstructable after the
    fact even though a different attempt might not reproduce the same bytes. `verdict` is
    `"authored"` (passed every check) or `"blocked"` (section 4's fail-closed table fired);
    `detail` names which check, when blocked. `driving_agent` is a free-text, caller-supplied
    label for whatever was driving the session when this content was authored (e.g.
    `"claude-code"`, `"codex"`, `"human"`) -- recorded for provenance, never validated or
    required to match a known set: this project doesn't verify WHO or WHAT produced the content
    (it can't, and shouldn't try -- see the field's own note below), only that a real context was
    supplied and a real gate verdict was reached. No retries happen at this layer or above it
    (decided in scope, not left to whoever calls this): a blocked attempt is a hard stop, and
    this function's whole job is making sure that stop is not a silent one.

    Deliberately agent-neutral: nothing in this record's shape assumes an API response object
    (no token-usage field, no request/header/credential field of any kind) -- `raw_output` is
    whatever text the caller hands in, regardless of whether a human typed it, an agentic CLI's
    own model authored it, or any other source. Provenance is this record's job (the audit
    chain's own hash-verified pointer, permanent and reviewable); proving WHO/WHAT authored the
    bytes is not a property this function checks or can check.

    Bulk artifacts (`schema_block`, `grounding_examples`, `raw_output`) are written as real files
    under the run's own workspace, NOT inlined into the hash-chained audit log itself -- measured,
    not assumed: a single type's live schema can run ~9KB, grounding examples several more on top
    (docs/phase7_item5_authoring_scope.md section 1's own measurements). This matches this
    project's own established pattern for bulky artifacts (`source_guard.py`'s baseline
    manifests, `requirements.json`/`architecture_decision.json` themselves) -- the audit chain
    entry carries small, hash-verified pointers; the real content lives in real files a reviewer
    can open directly."""
    authoring_dir = os.path.join(run["root"], "authoring")
    os.makedirs(authoring_dir, exist_ok=True)

    def _write(name, text):
        rel_path = os.path.join("authoring", f"{resource_type}-{name}")
        with open(os.path.join(run["root"], rel_path), "w", encoding="utf-8") as f:
            f.write(text)
        return rel_path.replace(os.sep, "/"), hashlib.sha256(text.encode("utf-8")).hexdigest()

    schema_rel, schema_hash = _write("schema.json", json.dumps(schema_block, sort_keys=True, indent=2))
    grounding_rel, grounding_hash = _write(
        "grounding.json", json.dumps(grounding_examples, sort_keys=True, indent=2))
    output_rel, output_hash = _write("output.txt", raw_output or "")

    rec = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "operator": getpass.getuser(),
        "component": "synthesizer.authoring",
        "action": "author_resource",
        "run_id": (run or {}).get("run_id", ""),
        "resource_type": resource_type,
        "justification": justification,
        "driving_agent": driving_agent,
        "verdict": verdict,
        "detail": detail,
        "schema_ref": schema_rel, "schema_hash": schema_hash,
        "grounding_ref": grounding_rel, "grounding_hash": grounding_hash,
        "output_ref": output_rel, "output_hash": output_hash,
    }
    os.makedirs(LOG_DIR, exist_ok=True)
    try:
        return audit_chain.append(os.path.join(LOG_DIR, "audit.jsonl"), rec)
    except Exception as exc:
        print(f"[architect] WARNING: could not write authoring audit record: {exc}", file=sys.stderr)
        return rec


# Phase 7 Item 5 (docs/phase7_item5_authoring_scope.md section 1, revised): the authoring
# mechanism is NOT an API call this project makes on its own. MinusOps is operated THROUGH an
# agentic CLI tool (Claude Code, Codex, agy, etc.) -- that driving agent already has full
# authoring capability; it does not need MinusOps to embed its own separate LLM client,
# credentials, or model choice. What it DOES need is the same real, live context a human author
# would want: the declared type's actual provider schema and real grounding examples from this
# codebase's own reviewed modules. `assemble_authoring_context()` is exactly that surface -- a
# thin, callable (and CLI-exposed, see `main()`'s `author-context` subcommand) function that
# returns this context as plain JSON, so whatever agent is driving the session reads it, writes
# the HCL itself, and hands it back through the SAME `authored_content` interface every other
# caller of synthesize() already uses. Nothing about that interface changes -- this only answers
# "where does an authoring agent get the schema+grounding it needs," not "who authors."
def assemble_authoring_context(resource_type, justification, requirements_text, provider="aws"):
    """Returns {resource_type, justification, schema, grounding_examples, blocked, detail}.

    `blocked=True` (schema is None) means the pre-authoring schema-exists check (docs/
    phase7_item5_authoring_scope.md section 4) already fired -- the declared type does not exist
    in the live provider schema, so there is nothing to author against and no context is worth
    handing to an authoring agent; `detail` names why. This is the SAME check
    `_validate_novel_resources()` runs later for any caller's authored_content, surfaced here
    up front so an authoring agent (or a human) finds out before spending effort writing HCL for
    a type that will hard-block regardless."""
    import schema_watch
    schema_block = schema_watch.get_type_schema(provider, resource_type)
    if schema_block is None:
        return {
            "resource_type": resource_type, "justification": justification,
            "schema": None, "grounding_examples": [], "blocked": True,
            "detail": f"resource_type '{resource_type}' does not exist in the live provider schema",
        }
    grounding_examples = module_registry.retrieve_grounding_examples(requirements_text)
    return {
        "resource_type": resource_type, "justification": justification,
        "schema": schema_block, "grounding_examples": grounding_examples,
        "blocked": False, "detail": "",
    }


# A small set of obvious cross-module wirings applied when both modules are present.
# Module block labels use underscores (hyphens are awkward in HCL references).
_STORAGE = "module.storage_medallion_s3"
_NETWORKING = "module.networking_vpc"


def _label(module_id):
    return module_id.replace("-", "_")


def select_modules(requirements, explicit_ids=None, with_governance=True):
    """Pick modules for the requirements. Explicit ids win; otherwise match by keyword. A
    governance/observability baseline is added unless already chosen.

    `explicit_ids=None` means "no override, infer by keyword" (unchanged). `explicit_ids=[]` --
    distinct from None, checked explicitly rather than by truthiness (docs/
    phase7_generation_engine_plan.md item 2) -- means "explicitly chosen: zero catalog modules,"
    or the same as `None` would have been, and it's what an authored-only composition (a real
    architecture_decision.json with `"selected_modules": []`) needs to actually reach `compose()`
    with zero catalog picks instead of silently falling through to keyword matching."""
    if explicit_ids is not None:
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

    # Authored (novel) resources. Two forms (docs/phase7_item1_module_unit_scope.md):
    #   - "flat" (docs/phase6_step1_authoring_scope.md section 2 item 1): a standalone resource
    #     with no input contract of its own gets its own file at the composition root, sharing
    #     the root's variables/locals directly -- unchanged since Step 1.
    #   - "module": a unit that declares its own variable/output/locals needs a real module
    #     boundary (Terraform's own scoping, not one this project invents) so `path.module`
    #     resolves against ITS directory and its variables don't collide with the root's --
    #     written into authored_modules/<key>/, plus any companion assets its HCL references,
    #     called from a root-level `module "authored_<key>" { ... }` block.
    # Written before the fmt pass below so authored HCL gets the same fmt-clean treatment as
    # every catalog module's rendered output.
    for entry in authored_resources:
        text = entry["content"]
        if not text.endswith("\n"):
            text += "\n"
        if entry.get("form") == "module":
            unit_key = entry["resource_type"]
            unit_dir = os.path.join(out_dir, "authored_modules", unit_key)
            os.makedirs(unit_dir, exist_ok=True)
            with open(os.path.join(unit_dir, "main.tf"), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            for rel_path, asset_content in entry.get("assets", {}).items():
                asset_path = os.path.join(unit_dir, rel_path)
                os.makedirs(os.path.dirname(asset_path), exist_ok=True)
                if isinstance(asset_content, bytes):
                    with open(asset_path, "wb") as f:
                        f.write(asset_content)
                else:
                    with open(asset_path, "w", encoding="utf-8", newline="\n") as f:
                        f.write(asset_content)
            _w(f"authored_{unit_key}.tf",
               _render_authored_module_call(unit_key, text, entry.get("module_args", {})))
        else:
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
            {"resource_type": e["resource_type"], "form": e.get("form", "flat"),
             "decision_source": e["decision_source"], "content_hash": e["content_hash"]}
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


# Well-known root-level values every composition already declares (compose()'s own _VARIABLES /
# _render_main()'s locals block) -- docs/phase7_item1_module_unit_scope.md section 3, decision
# (b): a module-shaped authored unit's own variable gets auto-wired to the matching root value
# when its name matches one of these exactly, so a future authoring step can't get this wrong by
# emitting a variable name without also emitting a matching wiring entry (that mismatch would be
# a new failure class that doesn't exist for catalog modules, which get the identical auto-wire
# treatment via _module_args() above). Anything not name-matched needs an explicit module_args
# entry or a default; option (a), the override, still applies for those.
_AUTO_WIRE_ROOT_VALUES = {
    "name_prefix": "local.name_prefix",
    "tags": "local.tags",
    "owner": "var.owner",
    "environment": "var.environment",
    "region": "var.region",
    "run_id": "var.run_id",
    "daily_data_gb": "var.daily_data_gb",
}

_VARIABLE_BLOCK_RE = re.compile(r'^variable\s+"([^"]+)"\s*\{', re.MULTILINE)
# Matches the interpolated-string form this repo's real modules actually use
# (`filemd5("${path.module}/scripts/etl.py")`) -- the only form found in the catalog.
_PATH_MODULE_ASSET_RE = re.compile(r'\$\{path\.module\}/([^"\'\s)]+)')


def _matching_brace_offset(content, start):
    """Same brace-depth walk as schema_lint._matching_brace -- duplicated rather than imported
    (a private helper) since this is a handful of lines and avoids coupling this module's parsing
    to schema_lint's internals."""
    depth = 1
    i = start
    while depth > 0 and i < len(content):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
        i += 1
    return i


def _iter_variable_blocks(content):
    """Yield (name, body) for every top-level `variable "name" { ... }` block in an authored
    module unit's HCL -- used to decide what needs wiring at the call site (auto-wire /
    module_args / has-a-default) and, in compose(), what to actually emit in the module call."""
    for m in _VARIABLE_BLOCK_RE.finditer(content):
        end = _matching_brace_offset(content, m.end())
        yield m.group(1), content[m.end():end - 1]


def _variable_has_default(body):
    return re.search(r"^\s*default\s*=", body, re.MULTILINE) is not None


def _path_module_asset_refs(content):
    return set(_PATH_MODULE_ASSET_RE.findall(content))


def _render_authored_module_call(unit_key, hcl_text, module_args):
    """The root-level `module "authored_<x>" { source = "./authored_modules/<unit_key>" ... }`
    block wiring a module-shaped authored unit's own declared variables -- explicit module_args
    wins, then the well-known auto-wire set, then (validated already in
    _validate_novel_resources()) the variable's own default. Only variables the unit actually
    declares are emitted -- Terraform rejects a module argument with no matching input variable."""
    lines = [f'module "authored_{_label(unit_key)}" {{', f'  source = "./authored_modules/{unit_key}"']
    for var_name, _body in _iter_variable_blocks(hcl_text):
        if var_name in module_args:
            lines.append(f"  {var_name} = {module_args[var_name]}")
        elif var_name in _AUTO_WIRE_ROOT_VALUES:
            lines.append(f"  {var_name} = {_AUTO_WIRE_ROOT_VALUES[var_name]}")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


_DATA_PREFIX = "data."


def _split_resource_type(resource_type):
    """('resource'|'data', bare_type) from a declared resource_type, honoring the existing
    'data.'-prefix convention (docs/phase6_step1_authoring_scope.md section 1: authored_content
    is keyed by resource type, "optionally data.-prefixed")."""
    if resource_type.startswith(_DATA_PREFIX):
        return "data", resource_type[len(_DATA_PREFIX):]
    return "resource", resource_type


def _infer_provider(bare_type):
    return "databricks" if bare_type.startswith("databricks_") else "aws"


def _resource_type_exists_live(resource_type):
    """Phase 7 Item 5 (docs/phase7_item5_authoring_scope.md section 4): a declared
    novel_resources resource_type must exist in the REAL, live provider schema before anything
    is trusted for it -- the cheapest possible check, and (for a real authoring step, not built
    here) the only one that can save an authoring call entirely: a type that doesn't exist can't
    be authored correctly no matter what produces the content. Uses get_type_schema() (Item 4)
    directly."""
    # Imported lazily for the same reason schema_lint's own import in this function is lazy:
    # schema_watch.py imports synthesizer, so a module-level import here would complete the same
    # circular-import cycle module_provenance.py and this function already work around.
    import schema_watch
    kind, bare_type = _split_resource_type(resource_type)
    provider = _infer_provider(bare_type)
    return schema_watch.get_type_schema(provider, bare_type, kind=kind) is not None


def _authored_type_matches_declared(content, resource_type):
    """Phase 7 Item 5: the declared resource_type must actually be what's authored -- a caller
    (an LLM, eventually; any caller in principle) declaring 'aws_dynamodb_table' but authoring a
    DIFFERENT type's content is authoring malfunction, not legitimate novel output. Every prior
    caller of this mechanism (a human, or a test standing in for one) naturally authored content
    matching what they declared, so this was never a real failure mode until a caller that can
    get it wrong exists -- checked now, before one does.

    Scoped to the flat (str) form only, by design: a module-shaped unit can legitimately bundle
    several resource/data types under one caller-chosen key that is not itself a literal type
    string (confirmed against the real Step 5 harness, which keys a whole decomposed module's
    novel_resources entry by module_id, e.g. "compute-glue-etl" -- not a Terraform type). What
    "the content addresses the declared need" means for a multi-type unit is a real, harder
    question this item does not resolve (docs/phase7_item5_authoring_scope.md's own "not solved
    here" section); this check only fires for the single-type flat form, where resource_type IS
    unambiguously supposed to be a literal type name."""
    import schema_lint  # lazy -- see _validate_novel_resources()'s own identical import note
    kind, bare_type = _split_resource_type(resource_type)
    return any(
        block_kind == kind and type_name == bare_type
        for block_kind, type_name, _name, _body in schema_lint.iter_hcl_blocks(content)
    )


def _validate_novel_resources(decision, authored_content, verify_type_exists=True):
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

    An `authored_content` entry may also be a module-shaped unit (docs/
    phase7_item1_module_unit_scope.md), not just a plain HCL string: a dict with `content` (the
    HCL text -- can declare its own resource/data/variable/output/locals blocks together, exactly
    like a real catalog module's own main.tf), optional `assets` (relative path -> file content,
    for anything the HCL's own `path.module` references need), and optional `module_args`
    (explicit wiring for a declared variable, overriding the auto-wire set). Two additional
    fail-closed checks apply ONLY to this form, both new with this extension:

      - A `path.module`-relative asset reference with no matching `assets` entry -> hard block.
        This is the exact gap the Step 5 regression harness named as a real, structural blocker
        (compute-glue-etl/compaction-glue's `filemd5("${path.module}/scripts/....py")`) -- a
        composition that silently omitted the referenced file would produce HCL that fails at
        plan time, which is the same "parses fine, still wrong" shape every other fail-closed
        check in this project exists to catch.
      - A REQUIRED variable (no default) that is neither in `module_args` nor a name-matched
        auto-wire value -> hard block, NOT a `# REVIEW:` placeholder. `_render_main()`'s
        REVIEW-comment convention is correct for catalog modules (a human wrote and is reviewing
        them); it is wrong here -- an authoring step, not a human, would be the one leaving a
        required input unfilled, and composing anyway would either fail at plan or silently take
        an unintended default. Same fail-closed posture as every other check in this function.

    `verify_type_exists` (default `True`, flat form only): whether the schema-exists check
    (above) runs. Real cost, measured, not assumed: each check is a full, uncached live schema
    fetch (`get_type_schema()`, Item 4) -- ~30 seconds per call in this environment, since each
    call does its own fresh `terraform init` with no shared provider plugin cache. Left ON by
    default because every REAL caller (a human, or eventually an authoring step) is declaring a
    type that has not already been independently proven real, so the check is exactly the
    protection Item 5 exists to provide. The ONE narrow, explicit exception:
    `tests/test_teardown_regression_harness.py`'s own `_new_path_plan()` decomposes ALREADY-REAL,
    ALREADY-PINNED catalog module content across potentially dozens of unique types per run --
    re-verifying "does this type exist" via another live fetch is pure redundant overhead there
    (the type obviously exists; it's copied verbatim from a real, tested module), not a
    meaningful safety check, and at that scale turns a ~20-minute test suite into one that
    doesn't finish in a reasonable CI window. That one call site passes `verify_type_exists=
    False` explicitly, with this exact reasoning repeated at its own call site -- never as a
    silent default anywhere else.

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
        raw = authored_content.get(resource_type)
        if raw is None:
            raise ValueError(
                f"novel_resources entry '{resource_type}' has no matching authored_content -- "
                "fail-closed: synthesis refuses to proceed without authored HCL for every "
                "declared novel resource"
            )
        source_label = f"novel_resources[{i}]:{resource_type}"
        is_module_unit = isinstance(raw, dict)
        if is_module_unit:
            content = raw.get("content", "")
            assets = raw.get("assets") or {}
            module_args = raw.get("module_args") or {}
        else:
            content = raw
            assets = {}
            module_args = {}
        # Cheapest, fully-offline check first (unchanged position): empty content is empty
        # regardless of whether the declared type is even real, and every prior caller of this
        # path (several tests) relies on this needing no terraform/network access.
        if not list(schema_lint.iter_hcl_blocks(content)):
            raise ValueError(
                f"authored content for novel resource '{resource_type}' declares no "
                f"resource/data blocks at all -- refusing to synthesize (source: {source_label})"
            )
        # Both checks below are scoped to the flat form only (see each function's own
        # docstring) -- a module-shaped unit's key isn't necessarily a literal type string (the
        # real Step 5 harness keys one by module_id), so neither applies there. Schema-exists
        # runs before the type-match check: a type that doesn't exist at all makes "does the
        # content match the declared type" a moot question.
        if not is_module_unit:
            if verify_type_exists and not _resource_type_exists_live(resource_type):
                raise ValueError(
                    f"novel_resources entry '{resource_type}' does not exist in the live "
                    f"provider schema -- fail-closed before authoring/composing anything for "
                    f"it (source: {source_label})"
                )
            if not _authored_type_matches_declared(content, resource_type):
                raise ValueError(
                    f"authored content for novel resource '{resource_type}' does not declare a "
                    f"matching resource/data block -- authoring produced content for a "
                    f"different type than what was declared (source: {source_label})"
                )
        lint_result = schema_lint.gate_content(content, source_label)
        if lint_result["blocking"]:
            raise ValueError(
                f"authored content for novel resource '{resource_type}' failed G2 schema lint "
                f"({source_label}): {lint_result['findings']}"
            )
        if is_module_unit:
            referenced = _path_module_asset_refs(content)
            missing_assets = sorted(referenced - set(assets.keys()))
            if missing_assets:
                raise ValueError(
                    f"authored module unit '{resource_type}' references path.module-relative "
                    f"asset(s) with no matching entry in 'assets' ({source_label}): "
                    f"{missing_assets}"
                )
            unresolved_required = [
                var_name for var_name, body in _iter_variable_blocks(content)
                if not _variable_has_default(body)
                and var_name not in module_args
                and var_name not in _AUTO_WIRE_ROOT_VALUES
            ]
            if unresolved_required:
                raise ValueError(
                    f"authored module unit '{resource_type}' has required variable(s) with no "
                    f"default, no module_args entry, and no well-known auto-wire match "
                    f"({source_label}): {sorted(unresolved_required)}"
                )
        authored_resources.append({
            "resource_type": resource_type,
            "form": "module" if is_module_unit else "flat",
            "content": content,
            "assets": assets,
            "module_args": module_args,
            "justification": entry.get("justification", ""),
            "decision_source": f"novel_resources[{i}]",
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        })
    return authored_resources


def synthesize(requirements_text, spec=None, decision=None, allow_incomplete=False,
               name_prefix=None, explicit_ids=None, owner="data-platform", cloud="aws",
               target_run=None, overwrite=False, validate=False, authored_content=None,
               verify_novel_resource_types=True):
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

    A catalog-free, purely authored composition is a real, supported call: set
    `decision["selected_modules"] = []` explicitly (distinct from omitting the key entirely,
    which still infers by keyword) and supply `authored_content`/`novel_resources` for
    everything to compose (docs/phase7_generation_engine_plan.md item 2).

    `verify_novel_resource_types` (default True) forwards to `_validate_novel_resources()`'s own
    `verify_type_exists` -- see its docstring for the real, measured cost and the one narrow,
    named exception (the Step 5 regression harness's internal decomposition use). Leave this on
    for every real call; it exists to be off only there.
    """
    if not allow_incomplete:
        reqgate.require(spec or {})        # raises RequirementsIncomplete(missing) -> caller surfaces it
        archdec.require(decision or {})
    decision_module_ids = (decision or {}).get("selected_modules")
    if not allow_incomplete and explicit_ids and set(explicit_ids) != set(decision_module_ids or []):
        raise ValueError("--module overrides must match architecture_decision.json selected_modules")
    # None (key absent) -> no override, infer by keyword (unchanged). An explicit [] -- checked
    # by identity, not truthiness, same fix as select_modules() itself -- means "architect
    # decided: zero catalog modules" (docs/phase7_generation_engine_plan.md item 2: the real
    # public synthesize() entry point previously had no way to reach a catalog-free composition
    # without bypassing select_modules() the way the Step 5 regression harness had to). Respected
    # exactly, including skipping the governance-observability auto-add an INFERRED composition
    # still gets -- an explicit decision that names nothing shouldn't have something silently
    # added back in.
    explicit_selection = decision_module_ids if decision_module_ids is not None else explicit_ids
    if explicit_selection is not None:
        chosen = select_modules(requirements_text, explicit_ids=explicit_selection,
                                with_governance=bool(explicit_selection))
    else:
        chosen = select_modules(requirements_text)
    requested_ids = set(explicit_selection or [])
    chosen_ids = {m["id"] for m in chosen}
    unknown_ids = sorted(requested_ids - chosen_ids)
    if unknown_ids:
        raise ValueError("unknown selected module(s): " + ", ".join(unknown_ids))
    authored_resources = _validate_novel_resources(
        decision, authored_content, verify_type_exists=verify_novel_resource_types)
    if not chosen and not authored_resources:
        # Predates authored_resources, same fix compose()'s own identical guard already got in
        # Step 1: a composition can be entirely authored content with zero catalog picks (an
        # explicit selected_modules: [] plus novel_resources), which is not "nothing matched,"
        # only "nothing FROM THE CATALOG."
        raise ValueError("no modules matched the requirements; refine the request or pass --module")
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


def _main_author_context(argv):
    """`synthesizer.py author-context <resource_type> <requirements> [--justification ...]` --
    prints the live schema + grounding examples an authoring agent needs (docs/
    phase7_item5_authoring_scope.md section 1, revised). Makes no external call and authors
    nothing itself; the driving agent (Claude Code, Codex, agy, etc.) reads this JSON, writes
    the HCL, and feeds it back into synthesize()'s existing `authored_content` interface."""
    import argparse
    ap = argparse.ArgumentParser(
        prog="synthesizer.py author-context",
        description="Print the live schema + grounding examples for a declared novel resource "
                     "type, for whatever agent is driving this session to author against.")
    ap.add_argument("resource_type", help="e.g. aws_dynamodb_table")
    ap.add_argument("requirements", help="free-text requirements summary, for grounding retrieval")
    ap.add_argument("--justification", default="",
                    help="the human-reviewed justification from architecture_decision.json's novel_resources entry")
    ap.add_argument("--provider", default="aws")
    args = ap.parse_args(argv)
    context = assemble_authoring_context(
        args.resource_type, args.justification, args.requirements, provider=args.provider)
    print(json.dumps(context, indent=2))
    return 1 if context["blocked"] else 0


def main(argv=None):
    import argparse
    peek = argv if argv is not None else sys.argv[1:]
    if peek and peek[0] == "author-context":
        return _main_author_context(peek[1:])
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
