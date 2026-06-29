"""
Request-to-run workflow.

This is the safe entrypoint for agent-driven creation:
  request -> blueprint -> required inputs -> run workspace -> optional Terraform generation

It never runs terraform and never calls cloud APIs.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intent_resolver  # noqa: E402
import runs  # noqa: E402
import terraform_generator  # noqa: E402


def _input_defaults(blueprint):
    return {
        item["name"]: item.get("default")
        for item in blueprint["required_inputs"]
        if item.get("default") is not None
    }


def parse_input(values):
    parsed = {}
    for item in values or []:
        if "=" not in item:
            raise ValueError(f"input must be name=value: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key == "daily_data_gb":
            value = float(value)
        parsed[key] = value
    return parsed


def missing_required(blueprint, inputs):
    missing = []
    for item in blueprint["required_inputs"]:
        value = inputs.get(item["name"])
        if value is None or value == "":
            missing.append(item)
    return missing


def resolve_to_run(query, cloud=None, inputs=None, generate=False):
    result = intent_resolver.resolve(query, cloud=cloud)
    if result["intent"] != "BLUEPRINT":
        return {
            "ok": False,
            "resolution": result,
            "error": result["recommendation"],
        }
    blueprint = result["blueprint"]
    resolved_inputs = _input_defaults(blueprint)
    resolved_inputs.update(inputs or {})
    missing = missing_required(blueprint, resolved_inputs)
    run = runs.new_run(blueprint=blueprint["id"], request=query, cloud=result["cloud"])
    run_record = {
        "ok": not missing,
        "resolution": result,
        "run": run,
        "inputs": resolved_inputs,
        "missing_inputs": missing,
        "terraform_generated": False,
    }
    with open(os.path.join(run["root"], "workflow.json"), "w", encoding="utf-8") as f:
        json.dump(run_record, f, indent=2)
        f.write("\n")
    if missing:
        return run_record
    if generate:
        generated = terraform_generator.generate(blueprint, resolved_inputs, run["terraform_dir"])
        run_record["terraform_generated"] = True
        run_record["generated"] = generated
        with open(os.path.join(run["root"], "workflow.json"), "w", encoding="utf-8") as f:
            json.dump(run_record, f, indent=2)
            f.write("\n")
    return run_record


def format_result(record):
    lines = []
    resolution = record["resolution"]
    lines.append("[WORKFLOW] Request resolved")
    lines.append(f"  intent      : {resolution['intent']}")
    if resolution.get("blueprint"):
        lines.append(f"  blueprint   : {resolution['blueprint']['id']}")
    if record.get("run"):
        lines.append(f"  run         : {record['run']['run_id']}")
        lines.append(f"  terraform   : {record['run']['terraform_dir']}")
        lines.append(f"  reports     : {record['run']['reports_dir']}")
    if record.get("missing_inputs"):
        lines.append("  missing inputs:")
        for item in record["missing_inputs"]:
            lines.append(f"    - {item['name']}: {item['prompt']} ({item['reason']})")
        lines.append("  next step   : rerun with --input name=value for each missing input")
    else:
        lines.append("  inputs      : complete")
        lines.append(f"  generated   : {record.get('terraform_generated', False)}")
        lines.append("  safe next   : run plan_gate.py verify against the terraform directory")
    return os.linesep.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Resolve a request into a clean run workspace")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="resolve request and create run")
    r.add_argument("query")
    r.add_argument("--cloud", default=None)
    r.add_argument("--input", action="append", default=[], help="Blueprint input as name=value")
    r.add_argument("--generate", action="store_true", help="Generate Terraform into the run workspace")
    r.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "resolve":
        record = resolve_to_run(args.query, cloud=args.cloud, inputs=parse_input(args.input), generate=args.generate)
        if args.json:
            print(json.dumps(record, indent=2))
        else:
            print(format_result(record))
        return 0 if record.get("ok") else 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
