# Module: compute-glue-etl
# AWS Glue Spark jobs for batch transformation, with a least-privilege execution role.
# `jobs` maps a job name to the S3 key of its PySpark script in `script_s3_bucket`.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "script_s3_bucket" {
  type        = string
  description = "Bucket name holding the Glue job scripts."
}

variable "jobs" {
  type        = map(string)
  default     = {}
  description = "job_name => script_s3_key (e.g. { bronze_to_silver = \"scripts/b2s.py\" })."
}

variable "worker_type" {
  type    = string
  default = "G.1X"
}

variable "number_of_workers" {
  type    = number
  default = 2
}

variable "alarm_sns_topic_arn" {
  type        = string
  default     = ""
  description = "SNS topic to notify on Glue job failure (WA Analytics Lens BP 6.2/6.3). An EventBridge rule routes FAILED/TIMEOUT/STOPPED job runs to it when enable_alarms is true."
}

variable "enable_alarms" {
  type        = bool
  default     = false
  description = "Create the failure EventBridge rule. Separate from alarm_sns_topic_arn because count cannot depend on a value computed at plan time (the topic ARN usually comes from another module)."
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "glue" {
  name               = "${var.name_prefix}-glue-exec"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "glue" {
  statement {
    sid       = "Scripts"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.script_s3_bucket}", "arn:aws:s3:::${var.script_s3_bucket}/*"]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:*:*:/aws-glue/*"]
  }
}

resource "aws_iam_role_policy" "glue" {
  name   = "${var.name_prefix}-glue"
  role   = aws_iam_role.glue.id
  policy = data.aws_iam_policy_document.glue.json
}

# Upload the bundled starter PySpark script to each job's S3 key so the job is runnable
# on apply (the operator replaces the logic; the etag makes a changed script a new plan).
resource "aws_s3_object" "script" {
  for_each = var.jobs
  bucket   = var.script_s3_bucket
  key      = each.value
  source   = "${path.module}/scripts/etl.py"
  etag     = filemd5("${path.module}/scripts/etl.py")
  tags     = var.tags
}

resource "aws_glue_job" "this" {
  for_each          = var.jobs
  name              = "${var.name_prefix}-${each.key}"
  role_arn          = aws_iam_role.glue.arn
  glue_version      = "4.0"
  worker_type       = var.worker_type
  number_of_workers = var.number_of_workers
  tags              = var.tags

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${var.script_s3_bucket}/${aws_s3_object.script[each.key].key}"
  }

  # Incremental processing by default (WA Analytics Lens BP10 / our DATA-01 check):
  # bookmarks stop re-scanning already-processed input on every run.
  default_arguments = {
    "--job-bookmark-option" = "job-bookmark-enable"
  }
}

# Failure monitoring: route Glue job FAILED/TIMEOUT/STOPPED events to the alerts topic
# (BP 6.2 detect job failures, BP 6.3 notify stakeholders). Created only when a topic is wired.
resource "aws_cloudwatch_event_rule" "glue_failed" {
  count       = var.enable_alarms ? 1 : 0
  name        = "${var.name_prefix}-glue-failed"
  description = "Notify on Glue job failure/timeout for ${var.name_prefix}."
  event_pattern = jsonencode({
    source        = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    detail        = { state = ["FAILED", "TIMEOUT", "STOPPED"] }
  })
  tags = var.tags
}

resource "aws_cloudwatch_event_target" "glue_failed_sns" {
  count     = var.enable_alarms ? 1 : 0
  rule      = aws_cloudwatch_event_rule.glue_failed[0].name
  target_id = "sns"
  arn       = var.alarm_sns_topic_arn
}

output "glue_job_names" {
  value = { for k, j in aws_glue_job.this : k => j.name }
}

output "glue_job_arns" {
  value = [for j in aws_glue_job.this : j.arn]
}

output "glue_role_arn" {
  value = aws_iam_role.glue.arn
}
