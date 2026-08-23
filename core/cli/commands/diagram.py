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

def run(args):
    # Retrieve plan json
    plan_json = {}
    
    if args.dir:
        plan_file = os.path.join(args.dir, "plan.json")
        if os.path.exists(plan_file):
            with open(plan_file, "r") as f:
                plan_json = json.load(f)
    elif args.run:
        plan_file = os.path.join("runs", args.run, "reports", "plan.json")
        if os.path.exists(plan_file):
            with open(plan_file, "r") as f:
                plan_json = json.load(f)
                
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
