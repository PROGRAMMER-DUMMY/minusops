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

variable "data_buckets" {
  type        = list(string)
  default     = []
  description = "Medallion bucket NAMES the job reads and writes (bronze/silver/gold). Named for the same shape dq-great-expectations' target_buckets uses, so the synthesizer wires both from values(module.storage_medallion_s3.bucket_names)."
}

variable "kms_key_arn" {
  type        = string
  default     = ""
  description = "CMK encrypting the medallion buckets. Without kms:GenerateDataKey on it the job gets 403 AccessDenied writing to an SSE-KMS bucket, even with the S3 actions allowed."
}

variable "source_bucket" {
  type        = string
  default     = ""
  description = "Bucket the starter job reads from (bronze). Empty means the operator wires --source_path themselves."
}

variable "target_bucket" {
  type        = string
  default     = ""
  description = "Bucket the starter job writes to (silver). Empty means the operator wires --target_path themselves."
}

variable "source_format" {
  type        = string
  default     = "json"
  description = "Spark reader format for source_path. Declared rather than inferred: scripts/etl.py used to pick parquet-vs-json from a trailing slash, which read the raw-JSON Bronze zone as Parquet."
}

variable "target_format" {
  type        = string
  default     = "parquet"
  description = "Spark writer format for target_path. Columnar by default (WA Performance guidance)."
}

variable "execution_class" {
  type        = string
  default     = "STANDARD"
  description = "STANDARD or FLEX. FLEX runs on spare capacity for roughly 35% less, at the cost of an unpredictable start time and possible interruption -- correct for a nightly batch whose SLA is measured in hours, wrong for anything a person is waiting on. Requires Glue 3.0+ and a G-series worker."

  validation {
    condition     = contains(["STANDARD", "FLEX"], var.execution_class)
    error_message = "execution_class must be STANDARD or FLEX."
  }
}

variable "worker_type" {
  type    = string
  default = "G.1X"
}

variable "number_of_workers" {
  type    = number
  default = 2
}

variable "timeout_minutes" {
  type    = number
  default = 120

  # FinOps circuit breaker (PRD s11). Without an explicit timeout AWS Glue defaults to
  # 2880 minutes (48 hours), so a Spark job stuck in a shuffle loop bills DPU-seconds for
  # two days before anyone notices. 120 minutes bounds the worst case to roughly the cost
  # of one bad run rather than one bad weekend.
  #
  # This is a ceiling, not a target: a job legitimately exceeding it is under-provisioned
  # or reading too much per run, and raising the number is the wrong first response.
  validation {
    condition     = var.timeout_minutes > 0 && var.timeout_minutes <= 2880
    error_message = "timeout_minutes must be between 1 and 2880 (AWS Glue's own ceiling)."
  }
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
  # The Scripts statement above only covers reading the job script. Without these the job
  # reads bronze but 403s on the first write to silver/gold -- the exact failure the
  # 2026-08-17 live run hit. Scoped to the named buckets: never Resource = "*" (SEC-02).
  dynamic "statement" {
    for_each = length(var.data_buckets) > 0 ? [1] : []
    content {
      sid     = "DataLake"
      actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
      resources = concat(
        [for b in var.data_buckets : "arn:aws:s3:::${b}"],
        [for b in var.data_buckets : "arn:aws:s3:::${b}/*"],
      )
    }
  }
  # SSE-KMS buckets need the data-key grants too; S3 actions alone still 403. The key's own
  # policy keeps AWS's default root delegation (MINUS-112), so this IAM grant is what
  # actually confers access -- no service-principal block, no lockout risk.
  dynamic "statement" {
    for_each = var.kms_key_arn == "" ? [] : [var.kms_key_arn]
    content {
      sid       = "LakeKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
      resources = [statement.value]
    }
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
  execution_class   = var.execution_class
  worker_type       = var.worker_type
  number_of_workers = var.number_of_workers
  timeout           = var.timeout_minutes
  tags              = var.tags

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${var.script_s3_bucket}/${aws_s3_object.script[each.key].key}"
  }

  # Incremental processing by default (WA Analytics Lens BP10 / our DATA-01 check):
  # bookmarks stop re-scanning already-processed input on every run.
  #
  # --source_path / --target_path are injected here rather than left to the operator:
  # scripts/etl.py raises SystemExit without them, so an unwired job fails on its first
  # run (2026-08-17 live run). Omitted entirely when no bucket is wired, so a standalone
  # use of this module does not get a malformed "s3:///data/".
  default_arguments = merge(
    { "--job-bookmark-option" = "job-bookmark-enable" },
    var.source_bucket == "" ? {} : { "--source_path" = "s3://${var.source_bucket}/data/" },
    var.target_bucket == "" ? {} : { "--target_path" = "s3://${var.target_bucket}/data/" },
    # Read/write formats are declared, never inferred from the path shape.
    {
      "--source_format" = var.source_format
      "--target_format" = var.target_format
    },
  )
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
