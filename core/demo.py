"""
No-cloud demo generator.

Creates a fresh run workspace, generates Terraform from the governed data
pipeline blueprint, synthesizes a Terraform-like plan JSON, and renders reports.
It never runs Terraform and never calls AWS.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blueprints  # noqa: E402
import reporter  # noqa: E402
import runs  # noqa: E402
import terraform_generator  # noqa: E402


RESOURCE_TYPES = {
    "kms.tf": [
        ("aws_kms_key", "pipeline"),
        ("aws_kms_alias", "pipeline"),
    ],
    "s3.tf": [
        ("aws_s3_bucket", "zone"),
        ("aws_s3_bucket_public_access_block", "zone"),
        ("aws_s3_bucket_server_side_encryption_configuration", "zone"),
        ("aws_s3_bucket_versioning", "zone"),
        ("aws_s3_bucket_lifecycle_configuration", "zone"),
    ],
    "iam.tf": [
        ("aws_iam_role", "glue_role"),
        ("aws_iam_role_policy", "glue_policy"),
        ("aws_iam_role", "sfn_role"),
        ("aws_iam_role_policy", "sfn_policy"),
    ],
    "glue.tf": [
        ("aws_glue_catalog_database", "pipeline"),
        ("aws_glue_job", "bronze_to_silver"),
        ("aws_glue_job", "silver_to_gold"),
    ],
    "scripts.tf": [
        ("aws_s3_object", "bronze_to_silver_script"),
        ("aws_s3_object", "silver_to_gold_script"),
    ],
    "step_functions.tf": [("aws_sfn_state_machine", "pipeline")],
    "athena.tf": [("aws_athena_workgroup", "pipeline")],
    "monitoring.tf": [
        ("aws_cloudwatch_metric_alarm", "sfn_failures"),
        ("aws_budgets_budget", "monthly"),
    ],
}


def _expand_address(rtype, name):
    if name == "zone" and rtype.startswith("aws_s3_"):
        return [f'{rtype}.{name}["{key}"]' for key in ("bronze", "silver", "gold", "athena_results")]
    return [f"{rtype}.{name}"]


def synthetic_plan(tf_dir, inputs):
    changes = []
    for filename, resources in RESOURCE_TYPES.items():
        if not os.path.exists(os.path.join(tf_dir, filename)):
            continue
        for rtype, name in resources:
            for address in _expand_address(rtype, name):
                changes.append({
                    "address": address,
                    "type": rtype,
                    "name": name,
                    "change": {
                        "actions": ["create"],
                        "after": {"name": name},
                        "after_unknown": {},
                    },
                })
    return {
        "format_version": "1.2",
        "terraform_version": "demo",
        "variables": {
            key: {"value": value}
            for key, value in inputs.items()
        },
        "resource_changes": changes,
        "output_changes": {
            "bronze_bucket": {"sensitive": False, "change": {"actions": ["create"], "after_unknown": True}},
            "silver_bucket": {"sensitive": False, "change": {"actions": ["create"], "after_unknown": True}},
            "gold_bucket": {"sensitive": False, "change": {"actions": ["create"], "after_unknown": True}},
            "kms_key_arn": {"sensitive": False, "change": {"actions": ["create"], "after_unknown": True}},
            "step_function_arn": {"sensitive": False, "change": {"actions": ["create"], "after_unknown": True}},
        },
    }


def governed_data_pipeline(owner, daily_data_gb):
    inputs = {"owner": owner, "daily_data_gb": float(daily_data_gb)}
    run = runs.new_run(blueprint="demo/aws-data-pipeline-standard",
                       request="demo governed AWS data pipeline", cloud="aws")
    blueprint = blueprints.get_blueprint("aws-data-pipeline-standard")
    generated = terraform_generator.generate(blueprint, inputs, run["terraform_dir"])
    record = {
        "ok": True,
        "demo": True,
        "resolution": {
            "intent": "DEMO_FIXTURE",
            "cloud": "aws",
            "blueprint": blueprint,
            "recommendation": "Demo fixture only; production create is requirements-first.",
        },
        "run": run,
        "inputs": inputs,
        "terraform_generated": True,
        "generated": generated,
    }
    tf_dir = record["run"]["terraform_dir"]
    plan = synthetic_plan(tf_dir, record["inputs"])
    plan_path = os.path.join(record["run"]["root"], "demo-plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
        f.write("\n")
    report_dir = reporter.generate_from_plan_json(tf_dir, plan_path, template="aws-data-pipeline-standard")
    record["demo_plan_json"] = plan_path
    record["report_dir"] = report_dir
    with open(os.path.join(record["run"]["root"], "workflow.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
    return record


def main():
    ap = argparse.ArgumentParser(description="Generate no-cloud demo run artifacts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("governed-data-pipeline")
    g.add_argument("--owner", default="data-platform")
    g.add_argument("--daily-data-gb", type=float, default=50)
    g.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "governed-data-pipeline":
        result = governed_data_pipeline(args.owner, args.daily_data_gb)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("[demo] generated no-cloud governed data pipeline")
            print(f"  run       : {result['run']['run_id']}")
            print(f"  terraform : {result['run']['terraform_dir']}")
            print(f"  report    : {result['report_dir']}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
