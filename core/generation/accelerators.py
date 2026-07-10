"""
Explicit architecture accelerators.

These are not automatic recommendations. They are reviewable starting points an operator can
choose after requirements gathering, then edit before synthesis. The deploy gate still decides
whether the resulting Terraform can proceed.
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
import architecture_decision as archdec  # noqa: E402
import requirements as reqgate  # noqa: E402
import runs  # noqa: E402


LAKEHOUSE_MODULES = [
    "storage-medallion-s3",
    "compute-glue-etl",
    "orchestrator-stepfunctions",
    "dq-great-expectations",
    "schema-registry-glue",
    "query-athena",
    "governance-observability",
]

LAKEHOUSE_SOURCES = [
    "https://docs.aws.amazon.com/wellarchitected/latest/analytics-lens/welcome.html",
    "https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket",
    "https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/glue_job",
    "https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/sfn_state_machine",
    "https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/athena_workgroup",
    "https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/budgets_budget",
]


def lakehouse_requirements(owner="data-platform", daily_data_gb=100, latency="batch within 4 hours"):
    record = reqgate.template()
    record.update({
        "goal": "Build a governed AWS data lakehouse with medallion storage, batch ETL, data quality, schema governance, analyst SQL access, and cost/observability guardrails.",
        "system_class": "data lakehouse",
        "stakeholders": f"{owner}; data producers; analytics engineers; BI analysts; platform security",
        "functional": [
            "Ingest raw datasets into a bronze S3 zone and publish curated silver/gold datasets.",
            "Run governed Spark ETL jobs with orchestrated dependencies and failure visibility.",
            "Validate data quality before publishing curated datasets.",
            "Enforce schema compatibility for contract-governed datasets.",
            "Expose curated data to analysts through controlled Athena workgroups.",
            "Track cost and operational health from day one.",
        ],
        "non_functional": {
            "latency": latency,
            "scale": f"Initial sizing target {daily_data_gb} GB/day; revisit worker sizing after measured file count and compression ratio.",
            "availability": "Managed regional services; no cross-region DR until RTO/RPO is approved.",
            "retention": "Default 90-day hot retention with archive transition; tune by data classification.",
            "security": "SSE-KMS, S3 public access blocks, scoped service roles, plan-gated production changes.",
            "budget": "Budget guardrail required; published estimates require AWS BCM Pricing Calculator evidence.",
        },
        "constraints": "AWS-first accelerator; networking, private endpoints, source ingestion adapters, and account-specific IAM boundaries remain REVIEW items.",
        "gathered_by": "minusops-accelerator",
        "gathered_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    # Data-pipeline FR/NFR profile (six-layer reference model + Well-Architected pillars).
    # `sources` is genuinely client-specific, so it is explicitly deferred (not silently blank).
    record["data_pipeline"] = {
        "sources": "deferred: source systems / ingestion adapters are client-specific REVIEW inputs",
        "storage_zones": "S3 medallion — bronze (raw) -> silver (cleaned) -> gold (curated), SSE-KMS",
        "transforms": "Glue Spark ETL — bronze->silver validate/clean, silver->gold normalize/enrich",
        "catalog": "Glue Schema Registry for schema governance; Glue Data Catalog for discovery (REVIEW crawler)",
        "consumption": "Athena workgroup for analyst SQL over curated (gold) datasets",
        "data_quality": "Great Expectations-style validation before publishing curated datasets (WA BP 1.1)",
        "freshness_sla": latency,
        "data_volume": f"~{daily_data_gb} GB/day initial; revisit partitioning and file sizes after measurement",
        "governance": "SSE-KMS, S3 public access blocks, scoped IAM roles; lineage/PII classification REVIEW",
        "orchestration": "Step Functions orchestrates Glue jobs with retries + failure notification (REVIEW SNS target)",
    }
    return record


def lakehouse_decision(requirements_file="requirements.json", streaming=False, daily_data_gb=0):
    import architecture_model
    tier = architecture_model.volume_tier(daily_data_gb)
    modules = list(LAKEHOUSE_MODULES)
    if streaming:
        modules.insert(2, "speed-layer-kinesis")
    # Scale-tier additions (researched thresholds in docs/project_plan.md): at TB/day the
    # small-files problem dominates -> compaction; at PB folder-level metadata stops
    # scaling -> open table format.
    if tier in ("tb", "pb"):
        modules.append("compaction-glue")
    if tier == "pb":
        modules.append("table-format-iceberg")
    record = archdec.template(requirements_file=requirements_file)
    record.update({
        "selected_architecture": "AWS governed lakehouse accelerator",
        "decision_summary": (
            "Use S3 medallion storage, Glue ETL, Step Functions orchestration, Glue Schema "
            "Registry, Great Expectations-style data quality, Athena serving, and budget/"
            "CloudWatch guardrails. This is a reviewable accelerator, not an auto-selected "
            "recommendation; operators must refine REVIEW inputs before planning."
        ),
        "selected_modules": modules,
        "alternatives": [
            {
                "name": "Managed Airflow (MWAA) orchestration",
                "decision": "rejected unless Airflow DAG ownership is a requirement",
                "reason": "Step Functions has lower operational surface for the default batch lakehouse; choose MWAA when teams already operate Airflow or need DAG portability.",
            },
            {
                "name": "Redshift-centered warehouse",
                "decision": "rejected for lake-first requirements",
                "reason": "Athena over S3 keeps storage open and low-ops for the initial lakehouse; Redshift can be added when workload concurrency and performance justify it.",
            },
            {
                "name": "Streaming speed layer",
                "decision": "selected" if streaming else "deferred",
                "reason": "Enable Kinesis/Flink only when latency requirements are real-time; batch paths should avoid streaming cost and operational complexity.",
            },
            {
                "name": "Databricks on AWS (lakehouse platform)",
                "decision": ("ask the client — strong candidate at this scale" if tier in ("tb", "pb")
                             else "rejected at this scale"),
                "reason": ("Delta/Photon, notebooks, and unified batch+streaming pay off for heavy "
                           "Spark/ML at TB+ scale; data stays in the client's S3 and Marketplace "
                           "billing lands in Cost Explorer. Note: DBU compute is NOT priceable via "
                           "AWS BCM — the cost report would mark platform compute not-estimated."
                           if tier in ("tb", "pb") else
                           "Native serverless (Glue/Athena) economics win below ~1 TB/day, and the "
                           "cost gate can price the whole stack via AWS BCM. Revisit at TB/day."),
            },
            {
                "name": "Snowflake (warehouse platform)",
                "decision": ("ask the client if BI concurrency / data sharing dominates" if tier in ("tb", "pb")
                             else "rejected at this scale"),
                "reason": ("Elastic warehouse concurrency without cluster ops suits SQL-centric, "
                           "many-analyst workloads; credits are NOT priceable via AWS BCM, so the "
                           "cost report would rely on post-deploy actuals."
                           if tier in ("tb", "pb") else
                           "Lake-first serverless keeps storage open and fully BCM-priceable at this "
                           "volume; a warehouse adds platform cost without a concurrency need."),
            },
        ],
        "assumptions": [
            "The client accepts AWS as the initial production cloud.",
            "Source systems and exact ingestion adapters are still client-specific REVIEW inputs.",
            "The operator will replace placeholder module inputs before plan approval.",
        ],
        "risks": [
            "Default module inputs may be under-sized or over-sized until measured workload data is available.",
            "Network topology, private endpoints, and cross-account access are intentionally not guessed.",
            "BCM cost evidence must be generated before enterprise cost totals are published.",
        ],
        "sources": list(LAKEHOUSE_SOURCES),
        "decided_by": "minusops-accelerator",
        "decided_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    return record


def write_lakehouse(run, owner="data-platform", daily_data_gb=100, streaming=False, force=False):
    root = Path(run["root"])
    req_path = root / reqgate.FILENAME
    decision_path = root / archdec.FILENAME
    if not force and (req_path.exists() or decision_path.exists()):
        raise FileExistsError("requirements.json or architecture_decision.json already exists; pass --force to overwrite")
    requirements = lakehouse_requirements(owner=owner, daily_data_gb=daily_data_gb)
    decision = lakehouse_decision(requirements_file=str(req_path), streaming=streaming,
                                  daily_data_gb=daily_data_gb)
    reqgate.write(str(root), requirements, gathered_by=owner)
    archdec.write(str(root), decision, decided_by=owner)
    return {
        "run": run,
        "requirements_file": str(req_path),
        "decision_file": str(decision_path),
        "requirements": requirements,
        "decision": decision,
        "next": (
            f"python core/generation/synthesizer.py \"AWS governed lakehouse\" --run {run['run_id']} "
            f"--requirements-file {req_path} --decision-file {decision_path}"
        ),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reviewable architecture accelerators")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lake = sub.add_parser("aws-lakehouse", help="write reviewable AWS lakehouse requirements + decision")
    lake.add_argument("--run", default="latest")
    lake.add_argument("--owner", default="data-platform")
    lake.add_argument("--daily-data-gb", type=float, default=100)
    lake.add_argument("--streaming", action="store_true")
    lake.add_argument("--force", action="store_true")
    lake.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "aws-lakehouse":
        run = runs.get_run(args.run)
        if not run:
            print(f"[accelerator] REFUSED - run not found: {args.run}")
            return 2
        try:
            result = write_lakehouse(run, owner=args.owner, daily_data_gb=args.daily_data_gb,
                                     streaming=args.streaming, force=args.force)
        except FileExistsError as exc:
            print(f"[accelerator] REFUSED - {exc}")
            return 2
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("[accelerator] aws-lakehouse artifacts written")
            print(f"requirements: {result['requirements_file']}")
            print(f"decision    : {result['decision_file']}")
            print(f"next        : {result['next']}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
