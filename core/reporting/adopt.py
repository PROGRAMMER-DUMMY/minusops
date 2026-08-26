"""
Brownfield adoption: bring an existing Terraform directory under governance.

Enterprises do not start from an empty directory. They have Terraform that predates any of
this, applied by hand, and the question is not "generate me a stack" but "what is in here, and
what has to change before the deploy gate will accept it".

So this is a **read-then-anchor** operation, in that order:

  1. inventory  -- what resources and providers this directory actually declares
  2. scan       -- optimize_analyzer's SEC/COST/OBS findings, unmodified
  3. baseline   -- source_guard anchors the CURRENT files, so from now on every manual edit
                   shows up as drift against the state at adoption
  4. next steps -- the exact gate commands, in order

Step 3 is the only write, it lands in `.minus/` inside the target directory, and it is
**opt-in** (`--anchor`). Anchoring is a claim that what is on disk is the reviewed starting
point; doing it automatically during a look-around would silently bless whatever was there,
including the wildcard IAM policy the scan is about to report.

Nothing here touches AWS, runs Terraform, or modifies a single `.tf` file.

Depends on: core/reporting/optimize_analyzer.py (scan + SKIP_DIRS + strip_comments),
    core/governance/source_guard.py (baseline anchoring)
Shells out to: nothing — no cloud CLI, no `terraform`, no network. It calls
    optimize_analyzer's native scan only, never `run_external_scanners`.
Used by: core/reporting/minusctl.py (`minusctl adopt`), tests/test_seed_adopt.py
"""
import argparse
import json
import os
import re
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)

import optimize_analyzer  # noqa: E402
import source_guard  # noqa: E402

_RESOURCE_RE = re.compile(r'^\s*resource\s+"([A-Za-z0-9_]+)"\s+"([A-Za-z0-9_-]+)"', re.M)
_MODULE_RE = re.compile(r'^\s*module\s+"([A-Za-z0-9_-]+)"', re.M)
_BACKEND_RE = re.compile(r'^\s*backend\s+"([a-z0-9]+)"', re.M)
_PROVIDER_RE = re.compile(r'^\s*provider\s+"([a-z0-9]+)"', re.M)

# Types where a destroy is data loss rather than an inconvenience. Reported separately at
# adoption because they decide how carefully the first governed plan has to be read.
_STATEFUL = ("aws_s3_bucket", "aws_db_instance", "aws_rds_cluster", "aws_dynamodb_table",
             "aws_kms_key", "aws_efs_file_system", "aws_elasticache_cluster",
             "aws_redshift_cluster", "aws_glue_catalog_database")


def _read_tf(source_dir):
    """Every .tf in the directory tree, as (relative_path, text)."""
    out = []
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if d not in optimize_analyzer.SKIP_DIRS]
        for name in sorted(files):
            if not name.endswith(".tf"):
                continue
            path = os.path.join(root, name)
            try:
                out.append((os.path.relpath(path, source_dir),
                            open(path, encoding="utf-8", errors="replace").read()))
            except OSError:
                continue
    return out


def inventory(source_dir):
    """What this directory declares. Parsed from the source, not from state: adoption happens
    before anyone has been trusted with the state file."""
    files = _read_tf(source_dir)
    resources, modules, providers, backends = [], set(), set(), set()
    for rel, text in files:
        clean = optimize_analyzer.strip_comments(text)
        for rtype, rname in _RESOURCE_RE.findall(clean):
            resources.append({"type": rtype, "name": rname, "file": rel})
        modules.update(_MODULE_RE.findall(clean))
        providers.update(_PROVIDER_RE.findall(clean))
        backends.update(_BACKEND_RE.findall(clean))

    by_type = {}
    for item in resources:
        by_type[item["type"]] = by_type.get(item["type"], 0) + 1
    return {
        "files": len(files),
        "resources": len(resources),
        "by_type": dict(sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))),
        "stateful": sorted({r["type"] for r in resources if r["type"] in _STATEFUL}),
        "modules": sorted(modules),
        "providers": sorted(providers),
        "backends": sorted(backends),
    }


def adopt(source_dir, anchor=False, label="adopted"):
    """Inventory + scan, and optionally anchor the source baseline.

    Returns {"ok", "dir", "inventory", "findings", "blocking", "baseline", "next_steps"}.
    `ok` is False when the directory has SEC findings: those must reach zero before the
    production-mode gate will accept it, so calling adoption successful would be misleading.
    """
    source_dir = os.path.abspath(source_dir)
    if not os.path.isdir(source_dir):
        raise NotADirectoryError(f"not a directory: {source_dir}")

    inv = inventory(source_dir)
    if not inv["files"]:
        return {"ok": False, "dir": source_dir, "inventory": inv, "findings": [],
                "blocking": [], "baseline": None,
                "next_steps": [], "error": "no .tf files found -- nothing to adopt"}

    findings = optimize_analyzer.scan_hcl_files(source_dir)
    blocking = [f for f in findings if str(f.get("id", "")).startswith("SEC")]

    baseline = None
    if anchor:
        baseline = source_guard.write_baseline(source_dir, label=label, extra={
            "adopted": True,
            "resources_at_adoption": inv["resources"],
            "sec_findings_at_adoption": len(blocking),
        })

    next_steps = []
    if blocking:
        next_steps.append(
            f"Resolve {len(blocking)} SEC finding(s) -- the production-mode gate blocks on them: "
            f"python core/reporting/optimize_analyzer.py --source-dir {source_dir}")
    if not anchor:
        next_steps.append(
            f"Anchor the reviewed starting point: python core/reporting/adopt.py "
            f"--dir {source_dir} --anchor")
    if not inv["backends"]:
        next_steps.append(
            "No backend block: this directory keeps state locally, which blocks CI and "
            "concurrent operators (TerraShark FM-03).")
    next_steps.append(f"minusctl gate verify --dir {source_dir} "
                      "--policy-mode production")
    next_steps.append(f"minusctl gate plan --dir {source_dir}")

    return {
        "ok": not blocking,
        "dir": source_dir,
        "inventory": inv,
        "findings": [{"id": f.get("id"), "title": f.get("title"), "resource": f.get("resource")}
                     for f in findings],
        "blocking": [f.get("id") for f in blocking],
        "baseline": baseline,
        "next_steps": next_steps,
        "error": None,
    }


def format_result(result):
    inv = result["inventory"]
    lines = [f"minusctl adopt - {result['dir']}", ""]
    if result.get("error"):
        lines.append(f"[ERR] {result['error']}")
        return "\n".join(lines)

    lines.append(f"  {inv['resources']} resources in {inv['files']} .tf file(s)")
    top = list(inv["by_type"].items())[:8]
    for rtype, count in top:
        lines.append(f"    {count:>4}  {rtype}")
    if len(inv["by_type"]) > len(top):
        lines.append(f"    ...   {len(inv['by_type']) - len(top)} more type(s)")
    if inv["stateful"]:
        lines.append("")
        lines.append("  Destroy here is DATA LOSS, not an inconvenience: "
                     + ", ".join(inv["stateful"]))
    lines.append("")
    lines.append(f"  providers: {', '.join(inv['providers']) or '(none declared)'}")
    lines.append(f"  backend:   {', '.join(inv['backends']) or 'LOCAL STATE'}")
    lines.append("")

    if result["blocking"]:
        lines.append(f"  [BLOCK] {len(result['blocking'])} SEC finding(s): "
                     + ", ".join(sorted(set(result["blocking"]))))
    else:
        lines.append(f"  [OK] no SEC findings across {len(result['findings'])} finding(s) scanned")
    if result["baseline"]:
        lines.append("  [OK] source baseline anchored; manual edits now show as drift")

    lines.append("")
    lines.append("Next:")
    for step in result["next_steps"]:
        lines.append(f"  - {step}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Bring an existing Terraform directory under MinusOps governance.")
    ap.add_argument("--dir", required=True, help="existing Terraform directory")
    ap.add_argument("--anchor", action="store_true",
                    help="write the source baseline, claiming these files as the reviewed "
                         "starting point (the only write this command makes)")
    ap.add_argument("--label", default="adopted")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        result = adopt(args.dir, anchor=args.anchor, label=args.label)
    except NotADirectoryError as exc:
        print(f"[adopt] REFUSED - {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else format_result(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
