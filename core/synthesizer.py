"""
Architecture synthesizer — compose vetted modules into a governed Terraform workspace.

This is the code half of the architect path: given requirements, it selects matching modules
from the registry (core/modules.py), creates a run workspace, and writes a composed Terraform
root that wires the obvious shared inputs and flags the rest for review. The output is a
*scaffold the architect refines and the deploy gate validates* — never an apply-without-review
shortcut. It replaces the single hardcoded blueprint with requirement-driven composition.
"""
import os
import json
import shutil
import datetime

import architecture_decision as archdec
import modules as module_registry
import requirements as reqgate
import runs
import source_guard

# A small set of obvious cross-module wirings applied when both modules are present.
# Module block labels use underscores (hyphens are awkward in HCL references).
_STORAGE = "module.storage_medallion_s3"


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


def _module_args(module_id, present_ids):
    args = {"name_prefix": "local.name_prefix", "tags": "local.tags"}
    has_storage = "storage-medallion-s3" in present_ids
    has_compute = "compute-glue-etl" in present_ids
    has_gov = "governance-observability" in present_ids
    _GOV = "module.governance_observability"
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
    return args


def _render_main(chosen, present_ids):
    lines = [
        "# Composed by MinusOps architect synthesis — vetted modules assembled for the gathered",
        "# requirements. Review the items marked REVIEW, then run the deploy gate:",
        "#   python core/plan_gate.py verify --dir <this dir> --policy-mode production",
        "",
        "locals {",
        "  name_prefix = var.name_prefix",
        '  tags        = merge({ owner = var.owner, environment = var.environment, managed_by = "minusops" }, var.tags)',
        "}",
        "",
    ]
    for m in chosen:
        args = _module_args(m["id"], present_ids)
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


_VERSIONS = '''terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}
'''

_PROVIDERS = '''provider "aws" {
  region = var.region
  default_tags {
    tags = {
      managed_by = "minusops"
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
'''


def compose(module_ids, name_prefix, out_dir, owner="", request=""):
    """Write a composed Terraform root into out_dir from the selected modules."""
    chosen = [module_registry.get_module(i) for i in module_ids]
    chosen = [m for m in chosen if m]
    if not chosen:
        raise ValueError("no valid modules selected")
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

    _w("versions.tf", _VERSIONS)
    _w("providers.tf", _PROVIDERS)
    _w("variables.tf", _VARIABLES)
    _w("main.tf", _render_main(chosen, present_ids))

    review = []
    for m in chosen:
        args = _module_args(m["id"], present_ids)
        review += [f"{m['id']}: {i}" for i in m["inputs"] if i not in args]
    doc = ["# Composition", "", f"Request: {request or '(none)'}", "",
           "## Modules", ""]
    for m in chosen:
        doc.append(f"- **{m['id']}** — {m['title']} ({', '.join(m['services'])})")
    doc += ["", "## Review before deploy", "",
            "Wire these module inputs to real values (the architect/operator completes them):", ""]
    doc += [f"- `{r}`" for r in review] or ["- (none — common inputs auto-wired)"]
    doc += ["", "## Next", "",
            "```bash", f"python core/plan_gate.py verify --dir {out_dir} --policy-mode production",
            f"python core/plan_gate.py plan   --dir {out_dir}", "```",
            "", "The composed Terraform is governed by the same gate (validate + native SEC scan + "
            "production external scanner evidence + plan-hash approval + BCM cost). Nothing applies without human review."]
    _w("COMPOSITION.md", "\n".join(doc) + "\n")

    return {"out_dir": out_dir, "modules": [m["id"] for m in chosen], "review": review}


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
        "review": result["review"],
        "files": files,
    }
    with open(os.path.join(terraform_dir, "minus-generated.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    source_guard.write_baseline(terraform_dir, label="synthesized", extra={"modules": result["modules"]})
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


def synthesize(requirements_text, spec=None, decision=None, allow_incomplete=False,
               name_prefix=None, explicit_ids=None, owner="data-platform", cloud="aws",
               target_run=None, overwrite=False, validate=False):
    """
    End-to-end: enforce the requirements and architecture decision gates -> select the modules
    approved in that decision -> create a run workspace -> compose Terraform into it, and record
    requirements.json / architecture_decision.json alongside the run.

    `spec` is the structured requirements record (from grill-me). Generation is **fail-closed**:
    without complete requirements and a complete architecture decision it raises the matching
    gate exception listing what's unanswered. `allow_incomplete` is an explicit, audited override
    (demo/testing only).
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
    run = target_run or runs.new_run(blueprint="synthesized", request=requirements_text, cloud=cloud)
    _ensure_empty_or_overwrite(run["terraform_dir"], overwrite=overwrite)
    if spec:
        reqgate.write(run["root"], spec, gathered_by=owner)
    if decision:
        archdec.write(run["root"], decision, decided_by=owner)
    prefix = name_prefix or f"{module_registry._WORD.findall(owner.lower())[0] if owner else 'app'}-dev"
    result = compose([m["id"] for m in chosen], prefix, run["terraform_dir"], owner=owner, request=requirements_text)
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
    print(f"[architect] next          : python core/plan_gate.py verify --dir {res['out_dir']} --policy-mode production")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
