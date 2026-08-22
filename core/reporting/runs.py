"""
Run workspace manager: creates and locates `runs/<run-id>/` workspaces, and maintains the
central registry (`runs/index.json`, `runs/INDEX.md`).

Generated Terraform and reports live under `runs/<run-id>/` rather than in
source-controlled template directories, so a run is disposable and two runs never
overwrite each other. `RUNS_DIR` is resolved from the process CWD at import time, so
everything here is relative to wherever the CLI was invoked.

Two id shapes exist and both stay valid (PRD-ARCH-2026-005, FR-01). A run created with a
`name` gets a semantic id, `<domain>-<name>-<orchestrator>_<YYYYMMDD_HHMMSS>`; a run created
without one keeps the original `<YYYYMMDD-HHMMSS>-<blueprint>`. Nothing here parses an id to
find a run -- `list_runs()` discovers workspaces by the presence of `run.json` -- so the two
shapes coexist without a migration.

The registry is rebuilt from those `run.json` files on every `new_run()` and swapped into
place with `os.replace`, never written in situ: two runs created in parallel would otherwise
let a reader catch a half-written `index.json`. The registry reports a cost only when the run
already carries evidenced BCM figures; absent evidence the column is null, because a 0.0
there reads as "this pipeline is free" rather than "nobody priced it".

Depends on: nothing (stdlib only)
Shells out to: nothing
Used by: core/reporting/minusctl.py, core/reporting/cli_diagnostics.py,
    core/reporting/export.py, core/generation/accelerators.py, core/generation/demo.py,
    core/generation/synthesizer.py, core/generation/workflow.py,
    app/dashboard_app.py, tests/test_runs.py and other test modules
"""
import argparse
import datetime
import json
import os
import re
import sys
import tempfile

WORKSPACE = os.getcwd()
RUNS_DIR = os.path.join(WORKSPACE, "runs")

INDEX_JSON = "index.json"
INDEX_MD = "INDEX.md"

# Registry fields carried straight off run.json. Anything absent stays None rather than
# being defaulted -- see the module docstring on the cost column.
_INDEX_FIELDS = ("owner", "domain", "orchestrator", "compute_engine", "storage_zones",
                 "governance_status", "estimated_monthly_cost", "target_repo")


def _slug(value):
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value[:64] or "run"


def _semantic_id(name, domain, orchestrator, stamp):
    """`<domain>-<name>-<orchestrator>_<stamp>`, skipping the parts that were not supplied.

    Empty parts collapse rather than leaving `--`: `minusctl create --name x` supplies no
    domain, and the id becomes a directory name and an S3 prefix segment downstream."""
    parts = [_slug(part) for part in (domain, name, orchestrator) if part]
    return "-".join(parts) + "_" + stamp


def new_run(blueprint="manual", request="", cloud="aws", name=None, domain=None,
            orchestrator=None, owner=None, target_repo=None):
    now = datetime.datetime.now(datetime.timezone.utc)
    if name:
        run_id = _semantic_id(name, domain, orchestrator, now.strftime("%Y%m%d_%H%M%S"))
    else:
        run_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{_slug(blueprint)}"
    root = os.path.join(RUNS_DIR, run_id)
    paths = {
        "run_id": run_id,
        "root": root,
        "terraform_dir": os.path.join(root, "terraform"),
        "reports_dir": os.path.join(root, "reports"),
        "bcm_dir": os.path.join(root, "bcm"),
        "cloud": cloud,
        "blueprint": blueprint,
        "request": request,
        "name": name,
        "domain": domain,
        "orchestrator": orchestrator,
        "owner": owner,
        "target_repo": target_repo,
        "created_at": now.isoformat(),
    }
    for key in ("terraform_dir", "reports_dir", "bcm_dir"):
        os.makedirs(paths[key], exist_ok=True)
    with open(os.path.join(root, "run.json"), "w", encoding="utf-8") as f:
        json.dump(paths, f, indent=2)
        f.write("\n")
    sync_index()
    return paths


def list_runs():
    if not os.path.isdir(RUNS_DIR):
        return []
    runs = []
    for name in os.listdir(RUNS_DIR):
        root = os.path.join(RUNS_DIR, name)
        meta = os.path.join(root, "run.json")
        if not os.path.isdir(root) or not os.path.exists(meta):
            continue
        try:
            with open(meta, encoding="utf-8") as f:
                item = json.load(f)
        except Exception:
            continue
        runs.append(item)
    runs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return runs


def latest_run():
    runs = list_runs()
    return runs[0] if runs else None


def get_run(run_id=None):
    if not run_id or run_id == "latest":
        return latest_run()
    for item in list_runs():
        if item.get("run_id") == run_id or item.get("run_id", "").startswith(run_id):
            return item
    return None


# --- Central registry (FR-02) ---------------------------------------------------------

def _index_entry(item):
    run_id = item.get("run_id", "")
    entry = {
        "run_name": run_id,
        "pipeline_name": item.get("name") or item.get("blueprint"),
        "created_at": item.get("created_at"),
        "path": f"{os.path.basename(RUNS_DIR)}/{run_id}",
    }
    for field in _INDEX_FIELDS:
        entry[field] = item.get(field)
    entry.setdefault("governance_status", None)
    entry["governance_status"] = item.get("governance_status") or "GENERATED"
    entry["storage_zones"] = item.get("storage_zones") or []
    return entry


def _atomic_write(path, text):
    """Write via a sibling temp file and `os.replace`, so a concurrent reader sees either
    the old file or the new one and never a truncated one."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _cost_cell(value):
    return "not priced" if value is None else f"${value:,.2f}"


def _render_index_markdown(entries):
    lines = [
        "# MinusOps Run Registry",
        "",
        "Generated by `core/reporting/runs.py` on every run creation. Do not edit by hand --",
        "the next `minusctl create` rewrites it from each run's `run.json`.",
        "",
        "Cost is reported only from AWS BCM Pricing Calculator evidence. `not priced` means",
        "no estimate has been generated for that run, not that the run is free.",
        "",
        "| Run | Pipeline | Domain | Orchestrator | Owner | Est. monthly cost | Status | Created |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in entries:
        run_id = entry["run_name"]
        lines.append("| [{run}]({run}/) | {pipeline} | {domain} | {orch} | {owner} | "
                     "{cost} | {status} | {created} |".format(
                         run=run_id,
                         pipeline=entry.get("pipeline_name") or "-",
                         domain=entry.get("domain") or "-",
                         orch=entry.get("orchestrator") or "-",
                         owner=entry.get("owner") or "-",
                         cost=_cost_cell(entry.get("estimated_monthly_cost")),
                         status=entry.get("governance_status") or "-",
                         created=entry.get("created_at") or "-"))
    if not entries:
        lines.append("| _no runs_ | | | | | | | |")
    lines.append("")
    return "\n".join(lines)


def sync_index():
    """Rebuild `runs/index.json` and `runs/INDEX.md` from the run workspaces on disk.

    Rebuilt rather than appended: a run directory deleted by hand must drop out, and a
    corrupt `run.json` must cost only its own row (both are handled by `list_runs`)."""
    entries = [_index_entry(item) for item in list_runs()]
    if not os.path.isdir(RUNS_DIR):
        return entries
    _atomic_write(os.path.join(RUNS_DIR, INDEX_JSON), json.dumps(entries, indent=2) + "\n")
    _atomic_write(os.path.join(RUNS_DIR, INDEX_MD), _render_index_markdown(entries))
    return entries


def main():
    ap = argparse.ArgumentParser(description="Manage generated run workspaces")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="create a fresh run workspace")
    n.add_argument("--blueprint", default="manual")
    n.add_argument("--request", default="")
    n.add_argument("--cloud", default="aws")
    n.add_argument("--name", help="workload name; produces a semantic run id")
    n.add_argument("--domain", help="owning data domain, e.g. marketing")
    n.add_argument("--orchestrator", help="orchestrator id, e.g. mwaa")
    n.add_argument("--owner", help="owning team contact recorded in the registry")
    sub.add_parser("list", help="list run workspaces")
    sub.add_parser("latest", help="print latest run workspace")
    sub.add_parser("index", help="rebuild runs/index.json and runs/INDEX.md")
    args = ap.parse_args()

    if args.cmd == "new":
        print(json.dumps(new_run(args.blueprint, args.request, args.cloud, name=args.name,
                                 domain=args.domain, orchestrator=args.orchestrator,
                                 owner=args.owner), indent=2))
        return 0
    if args.cmd == "list":
        for item in list_runs():
            print(f"{item['run_id']}\t{item.get('blueprint', '-')}\t{item.get('terraform_dir', '-')}")
        return 0
    if args.cmd == "latest":
        item = latest_run()
        if not item:
            print("no runs")
            return 1
        print(json.dumps(item, indent=2))
        return 0
    if args.cmd == "index":
        entries = sync_index()
        print(f"{len(entries)} run(s) indexed in {os.path.join(RUNS_DIR, INDEX_JSON)}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
