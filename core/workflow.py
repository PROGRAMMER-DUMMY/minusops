"""
Request-to-run workflow.

This is the safe entrypoint for agent-driven creation:
  request -> requirements record -> architecture decision -> governed Terraform generation

It never runs terraform and never calls cloud APIs.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intent_resolver  # noqa: E402
import requirements as reqgate  # noqa: E402
import runs  # noqa: E402


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
    if result["intent"] == "OPERATION":
        return {
            "ok": False,
            "resolution": result,
            "error": result["recommendation"],
        }

    run = runs.new_run(blueprint="requirements-first", request=query, cloud=result["cloud"])
    requirements_record = reqgate.template()
    requirements_record["goal"] = query
    reqgate.write(run["root"], requirements_record, gathered_by="minusctl")
    _, missing_requirements = reqgate.validate(requirements_record)

    run_record = {
        "ok": True,
        "resolution": result,
        "run": run,
        "inputs": inputs or {},
        "missing_inputs": [],
        "requirements_file": os.path.join(run["root"], reqgate.FILENAME),
        "missing_requirements": missing_requirements,
        "architecture_decision_required": True,
        "terraform_generated": False,
        "generation_blocked": bool(generate),
    }
    if generate:
        run_record["generation_block_reason"] = (
            "Production creation is requirements-first. Complete requirements.json, "
            "record an architecture decision, then synthesize Terraform through the architect path."
        )
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
    if record.get("requirements_file"):
        lines.append(f"  requirements: {record['requirements_file']}")
    if record.get("missing_requirements"):
        lines.append("  requirements missing:")
        for item in record["missing_requirements"]:
            lines.append(f"    - {item}")
        lines.append("  next step   : complete requirements.json, then run architect synthesis")
    elif record.get("missing_inputs"):
        lines.append("  missing inputs:")
        for item in record["missing_inputs"]:
            lines.append(f"    - {item['name']}: {item['prompt']} ({item['reason']})")
        lines.append("  next step   : rerun with --input name=value for each missing input")
    else:
        lines.append("  inputs      : complete")
        lines.append(f"  generated   : {record.get('terraform_generated', False)}")
        lines.append("  safe next   : run architect synthesis, then plan_gate.py verify")
    if record.get("generation_blocked"):
        lines.append(f"  generate    : blocked - {record.get('generation_block_reason')}")
    return os.linesep.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Resolve a request into a clean run workspace")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="resolve request and create run")
    r.add_argument("query")
    r.add_argument("--cloud", default=None)
    r.add_argument("--input", action="append", default=[], help="Captured request input as name=value")
    r.add_argument("--generate", action="store_true", help="Compatibility flag; production Terraform generation is blocked until requirements and architecture decision are complete")
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
