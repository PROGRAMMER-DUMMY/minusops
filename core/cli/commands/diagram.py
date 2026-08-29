import os
import json
import argparse
from typing import List
from core.reporting.drawio_generator import generate_drawio_from_plan

def add_parser(subparsers):
    parser = subparsers.add_parser("diagram", help="Generate Draw.io architecture diagrams from Terraform plan")
    parser.add_argument("--run", help="Target run workspace (defaults to active session context)")
    parser.add_argument("--dir", help="Explicit Terraform directory")
    parser.add_argument("--format", choices=["all", "drawio", "url", "ledger"], default="all", help="Output artifacts. No svg: this engine emits Draw.io XML; reporter.py renders architecture.svg")
    parser.add_argument("--out-dir", help="Target output directory")
    parser.add_argument("--json", action="store_true", help="Structured JSON output containing file paths and the 1-click URL")
    parser.set_defaults(func=run)

import core.cli.context as context


def _find_plan(root):
    if not root:
        return {}
    for candidate in (
        os.path.join(root, "plan.json"),
        os.path.join(root, "reports", "plan.json"),
    ):
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    reports_dir = os.path.join(root, "reports") if os.path.isdir(os.path.join(root, "reports")) else root
    if os.path.isdir(reports_dir):
        subdirs = [os.path.join(reports_dir, d) for d in os.listdir(reports_dir)
                   if os.path.isdir(os.path.join(reports_dir, d))]
        subdirs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for s in subdirs:
            candidate = os.path.join(s, "plan.json")
            if os.path.isfile(candidate):
                try:
                    with open(candidate, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
    return {}


def run(args):
    # Retrieve plan json
    plan_json = {}

    if args.dir:
        plan_json = _find_plan(args.dir)
    elif args.run:
        plan_json = _find_plan(os.path.join("runs", args.run))
    else:
        try:
            active_run = context.active_run()
            if active_run:
                plan_json = _find_plan(os.path.join("runs", active_run))
        except Exception:
            pass

    result = generate_drawio_from_plan(plan_json)
    
    if args.json:
        print(json.dumps(result))
    else:
        print("Generated Diagram URL:")
        print(result["url"])
        
    out_dir = args.out_dir or "."
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
        
    if args.format in ["all", "drawio"]:
        with open(os.path.join(out_dir, "architecture.drawio"), "w") as f:
            f.write(result["xml"])
            
    if args.format in ["all", "url"]:
        with open(os.path.join(out_dir, "architecture_url.txt"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write(result["url"])

    # `ledger` was an advertised format that wrote nothing and still exited 0 -- the worst
    # shape of CLI bug, because the operator has no way to tell it did not work.
    if args.format in ["all", "ledger"]:
        with open(os.path.join(out_dir, "architecture_ledger.md"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write(result["ledger_markdown"] + "\n")

    return 0
