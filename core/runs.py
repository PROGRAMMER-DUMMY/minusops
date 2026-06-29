"""
Run workspace manager.

Generated Terraform and reports should live under runs/<run-id>/ instead of
source-controlled template directories.
"""
import argparse
import datetime
import json
import os
import re
import sys

WORKSPACE = os.getcwd()
RUNS_DIR = os.path.join(WORKSPACE, "runs")


def _slug(value):
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value[:64] or "run"


def new_run(blueprint="manual", request="", cloud="aws"):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = f"{ts}-{_slug(blueprint)}"
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
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    for key in ("terraform_dir", "reports_dir", "bcm_dir"):
        os.makedirs(paths[key], exist_ok=True)
    with open(os.path.join(root, "run.json"), "w", encoding="utf-8") as f:
        json.dump(paths, f, indent=2)
        f.write("\n")
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


def main():
    ap = argparse.ArgumentParser(description="Manage generated run workspaces")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="create a fresh run workspace")
    n.add_argument("--blueprint", default="manual")
    n.add_argument("--request", default="")
    n.add_argument("--cloud", default="aws")
    sub.add_parser("list", help="list run workspaces")
    sub.add_parser("latest", help="print latest run workspace")
    args = ap.parse_args()

    if args.cmd == "new":
        print(json.dumps(new_run(args.blueprint, args.request, args.cloud), indent=2))
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
    return 1


if __name__ == "__main__":
    sys.exit(main())
