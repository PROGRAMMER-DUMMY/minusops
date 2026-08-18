# Iceberg table maintenance (MINUS-152).
#
# Iceberg accumulates two kinds of debt that nothing cleans up on its own:
#   - small files, from every streaming or micro-batch write. Query cost is driven by files
#     scanned as much as bytes, so a Gold table written every five minutes gets slower and
#     more expensive every day even with constant data.
#   - old snapshots, which keep every superseded data file alive in S3. Time travel is the
#     feature; the storage bill is the invoice for it.
#
# OPTIMIZE and VACUUM are the fixes, and both are plain Athena DDL. A Lambda runs them on a
# schedule because Athena has no scheduler and Glue would mean a Spark cluster to issue two
# SQL statements.
#
# Off by default. Compaction rewrites data files, and a maintenance job nobody asked for that
# rewrites Gold at 2am is not a helpful default.

variable "iceberg_maintenance_tables" {
  type        = list(string)
  default     = []
  description = "Fully-qualified Iceberg tables to maintain, e.g. \"gold_db.orders\". Empty creates nothing. Named explicitly rather than discovered: a catalog scan would sweep in Hive tables, where OPTIMIZE is a syntax error, and non-Iceberg failures would mask the real ones."
}

variable "iceberg_maintenance_schedule" {
  type        = string
  default     = "cron(0 3 * * ? *)"
  description = "When to run. Daily at 03:00 UTC by default -- compaction competes with queries for the same Athena capacity, so it belongs outside the read window."
}

variable "iceberg_snapshot_retention_days" {
  type        = number
  default     = 7
  description = "How far back time travel stays possible. VACUUM expires snapshots older than this and deletes the data files only they referenced. Below 1 day risks expiring a snapshot a long-running query is still reading."

  validation {
    condition     = var.iceberg_snapshot_retention_days >= 1
    error_message = "Keep at least 1 day: expiring a snapshot mid-query fails the query and the files are already gone."
  }
}

variable "iceberg_lake_kms_key_arn" {
  type        = string
  default     = ""
  description = "CMK on the Gold bucket. Compaction rewrites data files, so without kms:GenerateDataKey the job reads fine and fails on write."
}

variable "iceberg_alert_topic_arn" {
  type        = string
  default     = ""
  description = "SNS topic for maintenance failures. Empty means a failed run is only visible in CloudWatch, which is where unnoticed degradation lives."
}

locals {
  iceberg_maintenance_enabled = length(var.iceberg_maintenance_tables) > 0
}

data "aws_iam_policy_document" "iceberg_maintenance_assume" {
  count = local.iceberg_maintenance_enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "iceberg_maintenance" {
  count              = local.iceberg_maintenance_enabled ? 1 : 0
  name               = "${var.name_prefix}-iceberg-maintenance"
  assume_role_policy = data.aws_iam_policy_document.iceberg_maintenance_assume[0].json
  tags               = var.tags
}

data "aws_iam_policy_document" "iceberg_maintenance" {
  count = local.iceberg_maintenance_enabled ? 1 : 0

  statement {
    sid     = "RunMaintenanceQueries"
    actions = ["athena:StartQueryExecution", "athena:GetQueryExecution"]
    # Scoped to this module's workgroup: a wildcard here would let the maintenance role run
    # arbitrary SQL in every workgroup in the account, including ones with no scan limit.
    resources = [aws_athena_workgroup.this.arn]
  }
  statement {
    sid = "CatalogAndData"
    actions = ["glue:GetTable", "glue:GetTables", "glue:GetDatabase", "glue:GetDatabases",
    "glue:UpdateTable"]
    resources = ["*"]
  }
  # Compaction REWRITES data files and VACUUM DELETES them. This is the one maintenance job
  # that legitimately needs delete on the lake, which is exactly why it is opt-in and its
  # table list is explicit.
  statement {
    sid = "RewriteAndExpire"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket",
    "s3:GetBucketLocation"]
    resources = [
      "arn:aws:s3:::${var.gold_bucket}",
      "arn:aws:s3:::${var.gold_bucket}/*",
      aws_s3_bucket.results.arn,
      "${aws_s3_bucket.results.arn}/*",
    ]
  }
  dynamic "statement" {
    for_each = var.iceberg_lake_kms_key_arn == "" ? [] : [var.iceberg_lake_kms_key_arn]
    content {
      sid       = "LakeKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
      resources = [statement.value]
    }
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:*:*:*"]
  }
}

resource "aws_iam_role_policy" "iceberg_maintenance" {
  count  = local.iceberg_maintenance_enabled ? 1 : 0
  name   = "${var.name_prefix}-iceberg-maintenance"
  role   = aws_iam_role.iceberg_maintenance[0].id
  policy = data.aws_iam_policy_document.iceberg_maintenance[0].json
}

# Inline source rather than an S3 artifact: the handler is 40 lines of boto3 with no
# dependencies, and shipping it as a zip in a bucket would add a build step and a second
# thing to keep in sync with this file.
data "archive_file" "iceberg_maintenance" {
  count       = local.iceberg_maintenance_enabled ? 1 : 0
  type        = "zip"
  output_path = "${path.module}/.build/iceberg_maintenance.zip"

  source {
    filename = "handler.py"
    content  = <<-PY
      """OPTIMIZE + VACUUM every configured Iceberg table, one statement at a time."""
      import os
      import time

      import boto3

      ATHENA = boto3.client("athena")
      TABLES = [t for t in os.environ["TABLES"].split(",") if t]
      WORKGROUP = os.environ["WORKGROUP"]
      RETENTION_DAYS = int(os.environ["RETENTION_DAYS"])


      def _run(sql):
          """Start one statement and wait. Serial on purpose: OPTIMIZE and VACUUM on the same
          table conflict, and Athena reports the loser as a generic failure that is hard to
          tell from a real problem."""
          started = ATHENA.start_query_execution(
              QueryString=sql, WorkGroup=WORKGROUP)
          qid = started["QueryExecutionId"]
          while True:
              state = ATHENA.get_query_execution(
                  QueryExecutionId=qid)["QueryExecution"]["Status"]
              if state["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
                  return qid, state["State"], state.get("StateChangeReason", "")
              time.sleep(5)


      def handler(event, context):
          results = []
          failures = []
          for table in TABLES:
              # VACUUM after OPTIMIZE: compaction creates a new snapshot, and expiring first
              # would leave the pre-compaction files behind until the next run.
              for sql in (
                  f"OPTIMIZE {table} REWRITE DATA USING BIN_PACK",
                  f"ALTER TABLE {table} SET TBLPROPERTIES "
                  f"('vacuum_max_snapshot_age_seconds'='{RETENTION_DAYS * 86400}')",
                  f"VACUUM {table}",
              ):
                  qid, state, reason = _run(sql)
                  results.append({"table": table, "sql": sql.split()[0],
                                  "query_id": qid, "state": state})
                  if state != "SUCCEEDED":
                      # Record and continue: one unmaintained table must not stop the rest,
                      # and a silent partial run is worse than a loud one.
                      failures.append(f"{table}: {sql.split()[0]} {state} -- {reason}")
                      break
          if failures:
              raise RuntimeError("; ".join(failures))
          return {"maintained": len(TABLES), "statements": results}
    PY
  }
}

resource "aws_lambda_function" "iceberg_maintenance" {
  count            = local.iceberg_maintenance_enabled ? 1 : 0
  function_name    = "${var.name_prefix}-iceberg-maintenance"
  role             = aws_iam_role.iceberg_maintenance[0].arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.iceberg_maintenance[0].output_path
  source_code_hash = data.archive_file.iceberg_maintenance[0].output_base64sha256
  # Compaction of a large table is minutes, not seconds, and the handler waits for each
  # statement. 15 minutes is the Lambda ceiling; a table that needs longer wants Glue.
  timeout = 900
  tags    = var.tags

  environment {
    variables = {
      TABLES         = join(",", var.iceberg_maintenance_tables)
      WORKGROUP      = aws_athena_workgroup.this.name
      RETENTION_DAYS = tostring(var.iceberg_snapshot_retention_days)
    }
  }
}

resource "aws_cloudwatch_event_rule" "iceberg_maintenance" {
  count               = local.iceberg_maintenance_enabled ? 1 : 0
  name                = "${var.name_prefix}-iceberg-maintenance"
  schedule_expression = var.iceberg_maintenance_schedule
  tags                = var.tags
}

resource "aws_cloudwatch_event_target" "iceberg_maintenance" {
  count     = local.iceberg_maintenance_enabled ? 1 : 0
  rule      = aws_cloudwatch_event_rule.iceberg_maintenance[0].name
  target_id = "lambda"
  arn       = aws_lambda_function.iceberg_maintenance[0].arn
}

resource "aws_lambda_permission" "iceberg_maintenance" {
  count         = local.iceberg_maintenance_enabled ? 1 : 0
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.iceberg_maintenance[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.iceberg_maintenance[0].arn
}

# A maintenance job that fails silently is worse than none: the tables keep degrading while
# the schedule reports green.
resource "aws_cloudwatch_metric_alarm" "iceberg_maintenance_failed" {
  count               = local.iceberg_maintenance_enabled && var.iceberg_alert_topic_arn != "" ? 1 : 0
  alarm_name          = "${var.name_prefix}-iceberg-maintenance-failed"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = [var.iceberg_alert_topic_arn]
  dimensions = {
    FunctionName = aws_lambda_function.iceberg_maintenance[0].function_name
  }
  tags = var.tags
}

output "iceberg_maintenance_function" {
  value = local.iceberg_maintenance_enabled ? aws_lambda_function.iceberg_maintenance[0].function_name : null
}
