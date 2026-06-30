"""
Architecture synthesizer — compose vetted modules into a governed Terraform workspace.

This is the code half of the architect path: given requirements, it selects matching modules
from the registry (core/modules.py), creates a run workspace, and writes a composed Terraform
root that wires the obvious shared inputs and flags the rest for review. The output is a
*scaffold the architect refines and the deploy gate validates* — never an apply-without-review
shortcut. It replaces the single hardcoded blueprint with requirement-driven composition.
"""
import os
import shutil

import modules as module_registry
import requirements as reqgate
import runs

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


def _module_args(module_id, present_ids):
    args = {"name_prefix": "local.name_prefix", "tags": "local.tags"}
    has_storage = "storage-medallion-s3" in present_ids
    if has_storage and module_id == "compute-glue-etl":
        args["script_s3_bucket"] = f'{_STORAGE}.bucket_names["bronze"]'
    if has_storage and module_id == "query-athena":
        args["results_kms_key_arn"] = f"{_STORAGE}.kms_key_arn"
    if has_storage and module_id == "dq-great-expectations":
        args["target_buckets"] = f"values({_STORAGE}.bucket_names)"
        args["script_s3_bucket"] = f'{_STORAGE}.bucket_names["bronze"]'
    return args


def _render_main(chosen, present_ids):
    lines = [
        "# Composed by MinusOps architect synthesis — vetted modules assembled for the gathered",
        "# requirements. Review the items marked REVIEW, then run the deploy gate:",
        "#   python core/plan_gate.py verify --dir <this dir>",
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
            "```bash", f"python core/plan_gate.py verify --dir {out_dir}",
            f"python core/plan_gate.py plan   --dir {out_dir}", "```",
            "", "The composed Terraform is governed by the same gate (validate + SEC scan + "
            "plan-hash approval + BCM cost). Nothing applies without human review."]
    _w("COMPOSITION.md", "\n".join(doc) + "\n")

    return {"out_dir": out_dir, "modules": [m["id"] for m in chosen], "review": review}


def synthesize(requirements_text, spec=None, allow_incomplete=False,
               name_prefix=None, explicit_ids=None, owner="data-platform", cloud="aws"):
    """
    End-to-end: enforce the requirements gate -> select modules -> create a run workspace ->
    compose Terraform into it, and record requirements.json alongside the run.

    `spec` is the structured requirements record (from grill-me). Generation is **fail-closed**:
    without a complete record it raises requirements.RequirementsIncomplete listing what's
    unanswered — a vague request cannot be silently turned into infrastructure. `allow_incomplete`
    is an explicit, audited override (demo/testing only).
    """
    if not allow_incomplete:
        reqgate.require(spec or {})        # raises RequirementsIncomplete(missing) -> caller surfaces it
    chosen = select_modules(requirements_text, explicit_ids=explicit_ids)
    if not chosen:
        raise ValueError("no modules matched the requirements; refine the request or pass --module")
    run = runs.new_run(blueprint="synthesized", request=requirements_text, cloud=cloud)
    if spec:
        reqgate.write(run["root"], spec, gathered_by=owner)
    prefix = name_prefix or f"{module_registry._WORD.findall(owner.lower())[0] if owner else 'app'}-dev"
    result = compose([m["id"] for m in chosen], prefix, run["terraform_dir"], owner=owner, request=requirements_text)
    result["run"] = run
    result["requirements_recorded"] = bool(spec)
    return result


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Compose vetted modules into governed Terraform")
    ap.add_argument("requirements", help="free-text requirements summary (from grill-me)")
    ap.add_argument("--requirements-file", default=None,
                    help="path to the requirements.json gathered by grill-me (required unless --allow-incomplete)")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="audited override: synthesize without a complete requirements record (demo/testing)")
    ap.add_argument("--name", default=None, help="resource name prefix")
    ap.add_argument("--owner", default="data-platform")
    ap.add_argument("--module", action="append", default=[], help="force a specific module id (repeatable)")
    args = ap.parse_args(argv)

    spec = reqgate.load(args.requirements_file) if args.requirements_file else None
    try:
        res = synthesize(args.requirements, spec=spec, allow_incomplete=args.allow_incomplete,
                         name_prefix=args.name, explicit_ids=args.module or None, owner=args.owner)
    except reqgate.RequirementsIncomplete as exc:
        print("[architect] REFUSED — requirements gate. Run grill-me first; unanswered:")
        for m in exc.missing:
            print(f"    - {m}")
        print("    (or pass --requirements-file <requirements.json>, or --allow-incomplete for a demo)")
        return 2
    print("[architect] composed modules:", ", ".join(res["modules"]))
    print("[architect] terraform     :", res["out_dir"])
    if res["review"]:
        print("[architect] review inputs :")
        for r in res["review"]:
            print(f"    - {r}")
    print(f"[architect] next          : python core/plan_gate.py verify --dir {res['out_dir']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
