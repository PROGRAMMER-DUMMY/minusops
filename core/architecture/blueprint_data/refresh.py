"""Fetch the published reference architectures in `sources.json` and record what they name.

Run by hand. The output (`blueprints.json`) is evidence, not artwork: for each published
architecture it records which services the page names and which medallion stage vocabulary it
uses. `tests/test_blueprint_corpus.py` holds our own classifier to it -- a service that 30
reference architectures name and `architecture_model._RULES` cannot place is a gap in the
classifier, and the corpus is how that becomes visible instead of being noticed on a run.

No page text and no images are stored. The pages are AWS's, Databricks' and others' to
publish; what is kept is the count of which service names appear where, which is a fact about
the corpus rather than a copy of it.
"""
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(HERE, "sources.json")
TARGET = os.path.join(HERE, "blueprints.json")

_AGENT = "MinusOps-blueprint-refresh/1.0 (+reference architecture corpus)"

# Service vocabulary, keyed by the Terraform resource type prefix it corresponds to, so a
# corpus hit can be compared with what `architecture_model.classify_role` does with the same
# service. The right-hand side is what the prose calls it.
SERVICE_VOCABULARY = {
    "aws_s3_bucket": ("amazon s3", "s3 bucket"),
    "aws_glue_job": ("aws glue", "glue job", "glue etl"),
    "aws_glue_crawler": ("glue crawler",),
    "aws_glue_catalog_database": ("glue data catalog", "data catalog"),
    "aws_glue_registry": ("schema registry",),
    "aws_athena_workgroup": ("amazon athena", "athena"),
    "aws_redshift": ("amazon redshift", "redshift"),
    "aws_emr": ("amazon emr", "emr serverless"),
    "aws_kinesis_stream": ("kinesis data streams", "kinesis stream"),
    "aws_kinesis_firehose_delivery_stream": ("firehose",),
    "aws_msk_cluster": ("amazon msk", "managed streaming for apache kafka"),
    "aws_dms_replication_task": ("aws dms", "database migration service"),
    "aws_transfer_server": ("transfer family", "sftp"),
    "aws_sfn_state_machine": ("step functions",),
    "aws_mwaa_environment": ("managed workflows for apache airflow", "mwaa", "airflow"),
    "aws_lambda_function": ("aws lambda", "lambda function"),
    "aws_lakeformation_permissions": ("lake formation",),
    "aws_kms_key": ("aws kms", "key management service"),
    "aws_iam_role": ("iam role", "identity and access management"),
    "aws_cloudwatch_metric_alarm": ("cloudwatch",),
    "aws_sns_topic": ("amazon sns", "sns topic"),
    "aws_sqs_queue": ("amazon sqs", "sqs queue"),
    "aws_quicksight": ("quicksight",),
    "aws_datazone": ("datazone",),
    "aws_vpc": ("vpc endpoint", "virtual private cloud"),
    "aws_dynamodb_table": ("dynamodb",),
    "aws_appflow_flow": ("appflow",),
    "aws_eventbridge": ("eventbridge",),
}

STAGE_VOCABULARY = ("raw", "bronze", "cleaned", "clean", "silver", "curated", "gold",
                    "landing", "staged", "presentation", "serving")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _text(html):
    body = re.sub(r"(?is)<(script|style|nav|footer)[^>]*>.*?</\1>", " ", html)
    return _WS.sub(" ", _TAG.sub(" ", body)).lower()


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": _AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", "replace")


def extract(text):
    """Which services and which stage words a page names, with counts."""
    services = {}
    for resource_type, phrases in SERVICE_VOCABULARY.items():
        hits = sum(text.count(phrase) for phrase in phrases)
        if hits:
            services[resource_type] = hits
    stages = {word: text.count(word) for word in STAGE_VOCABULARY if text.count(word)}
    return services, stages


def main(argv=None):
    with open(SOURCES, encoding="utf-8") as handle:
        sources = json.load(handle)

    only = set(argv or [])
    records, failed = [], []
    for source in sources:
        if only and source["key"] not in only:
            continue
        try:
            text = _text(_fetch(source["url"]))
        except (urllib.error.URLError, OSError, ValueError) as error:
            failed.append((source["key"], str(error)[:90]))
            continue
        services, stages = extract(text)
        records.append({
            "key": source["key"], "provider": source["provider"],
            "title": source["title"], "url": source["url"],
            "characters": len(text), "services": services, "stages": stages,
        })
        print(f"  {source['key']:44} {len(services):2d} services, {len(stages)} stage words")
        time.sleep(1)

    for key, error in failed:
        print(f"  FAILED {key}: {error}", file=sys.stderr)

    if not records:
        print("nothing fetched; leaving the corpus as it is", file=sys.stderr)
        return 1

    payload = {
        "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_count": len(sources),
        "fetched_count": len(records),
        "failed": [{"key": key, "error": error} for key, error in failed],
        "blueprints": sorted(records, key=lambda r: r["key"]),
    }
    io.open(TARGET, "w", encoding="utf-8", newline="\n").write(
        json.dumps(payload, indent=2) + "\n")
    print(f"wrote {len(records)} of {len(sources)} blueprints to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
