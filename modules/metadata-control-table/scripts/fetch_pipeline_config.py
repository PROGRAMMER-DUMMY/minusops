"""
Runtime helper: fetch one pipeline's control-table row at Airflow DAG-parse time (or a Step
Functions pre-execution Lambda) via a caller-supplied column mapping, so an EXISTING enterprise
control table -- any name, any column names -- can drive dynamic DAG parameters. This is the
PRIMARY path: MinusOps never assumes `tbl_pipeline_control_config` or any
fixed column names. `modules/metadata-control-table/main.tf`'s DynamoDB table is only the
FALLBACK for a greenfield project with no existing table -- read it with this same helper by
supplying an identity column_map (normalized key -> the fallback table's own key names).

Runs inside the customer's Airflow/Lambda environment, not this repo's own control plane, so it
deliberately does not import anything from core/ -- it must work standalone once copied next to
a DAG file. No boto3: shells to the `aws` CLI the same way core/providers/aws.py does, since a
missing/incompatible boto3 version at DAG-parse time silently breaks every DAG in the
environment, and the CLI is what this repo already trusts for AWS calls.

Never accepts or stores an employee access key. A pipeline-config row may legitimately hold an
`iam_role_arn` or an Identity Center group id for downstream access wiring -- never a static
AWS access key.

ponytail: get-item (single key lookup) only, no Query/Scan -- add a query() helper when a DAG
needs to enumerate rows rather than look one up by key.

Depends on: nothing (stdlib only)
Shells out to: the `aws` CLI (`aws dynamodb get-item`)
Used by: Airflow DAGs / Step Functions pre-execution steps in a deployed pipeline project
    (copied out of this catalog, not imported from core/); tests/test_metadata_control_table.py
"""
import json
import subprocess

# DynamoDB's typed-attribute value kinds this helper understands. M (map) and L (list) are
# deliberately unsupported -- a control-table row is flat key/value config, not a nested
# document; a caller storing nested structures needs a helper this simple wasn't built for.
_SUPPORTED_TYPES = ("S", "N", "BOOL", "NULL")


def _scalar(attribute_value):
    """Convert one DynamoDB AttributeValue (`{"S": "x"}`, `{"N": "4"}`, ...) to a Python value.
    Returns None for an absent/malformed/unsupported-type value, never raises -- one odd column
    must not crash DAG parsing for every pipeline sharing the table."""
    if not isinstance(attribute_value, dict):
        return None
    for kind in _SUPPORTED_TYPES:
        if kind not in attribute_value:
            continue
        raw = attribute_value[kind]
        if kind == "N":
            try:
                return int(raw) if "." not in str(raw) else float(raw)
            except (TypeError, ValueError):
                return None
        if kind == "BOOL":
            return bool(raw)
        if kind == "NULL":
            return None
        return raw  # "S"
    return None


def parse_control_row(raw_item, column_map):
    """The column-mapping indirection this module exists for: `raw_item` is DynamoDB's own
    `{actual_column: {"S": ...}}` shape (whatever the caller's real table calls its columns);
    `column_map` is `{normalized_key: actual_column_name}` supplied by the caller. Returns
    `{normalized_key: python_value}` using MinusOps' normalized names regardless of what the
    source table calls them. A normalized key whose mapped column is absent from the row
    resolves to None, not a KeyError -- a DAG must be able to branch on a missing/mid-migration
    column instead of crashing at parse time and taking every other DAG down with it."""
    raw_item = raw_item or {}
    return {
        normalized_key: _scalar(raw_item.get(actual_column))
        for normalized_key, actual_column in (column_map or {}).items()
    }


def fetch_control_row(table_name, key, column_map, region=None, aws_bin="aws", timeout=20):
    """Look up one row by primary key and return it normalized through `column_map`.

    `key` is DynamoDB's own typed key shape, e.g. `{"FeedID": {"S": "payer_feed"}}` -- passed
    straight through to `aws dynamodb get-item --key`, so a composite (partition + sort) key
    works exactly as DynamoDB itself expects.

    Returns `(row_dict_or_None, error_string)`. The row is None with no error when the key
    simply has no matching item (a normal "not configured yet" case, not a failure); the row is
    None with a non-empty error when the AWS CLI call itself failed.
    """
    args = [aws_bin, "dynamodb", "get-item", "--table-name", table_name,
            "--key", json.dumps(key), "--output", "json"]
    if region:
        args += ["--region", region]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, "AWS CLI not found. Install it and run `aws configure` / `aws sso login`."
    except subprocess.TimeoutExpired:
        return None, f"AWS CLI timed out after {timeout}s."
    if result.returncode != 0:
        return None, (result.stderr or "Unknown AWS CLI error").strip()
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None, "AWS CLI returned non-JSON output."
    item = payload.get("Item")
    if item is None:
        return None, ""
    return parse_control_row(item, column_map), ""


def _demo():
    """ponytail self-check: exercises the column-mapping indirection against two
    DIFFERENTLY-NAMED source rows -- no network, no AWS CLI required."""
    raw_item = {
        "FeedID": {"S": "payer_feed"},
        "CronSchedule": {"S": "0 8 * * ? *"},
        "EngineType": {"S": "glue_spark"},
        "WorkerCount": {"N": "4"},
        "Status": {"S": "ACTIVE"},
    }
    column_map = {
        "feed_id": "FeedID",
        "schedule_cron": "CronSchedule",
        "cluster_type": "EngineType",
        "dpu_workers": "WorkerCount",
        "status": "Status",
    }
    row = parse_control_row(raw_item, column_map)
    assert row == {
        "feed_id": "payer_feed",
        "schedule_cron": "0 8 * * ? *",
        "cluster_type": "glue_spark",
        "dpu_workers": 4,
        "status": "ACTIVE",
    }

    # A differently-named table (e.g. this catalog's own greenfield fallback schema) resolves
    # through the SAME parser -- proves the indirection, not just one fixture.
    other_item = {
        "pipeline_key": {"S": "claims_aud"},
        "cron": {"S": "0 0 1 * ? *"},
    }
    other_map = {"feed_id": "pipeline_key", "schedule_cron": "cron", "status": "not_a_column"}
    row2 = parse_control_row(other_item, other_map)
    assert row2["feed_id"] == "claims_aud"
    assert row2["status"] is None  # mapped column absent from the row -- None, not a crash

    print("fetch_pipeline_config self-check OK")


if __name__ == "__main__":
    _demo()
