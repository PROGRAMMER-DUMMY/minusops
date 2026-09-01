# Module: orchestrator-mwaa
# Managed Airflow (Amazon MWAA) for companies that orchestrate with Airflow DAGs instead of
# Step Functions. Creates the environment, its execution role, and scoped DAG-bucket access.
# Networking (subnets, security groups) is supplied by the caller — MWAA runs inside your VPC.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "create_dag_bucket" {
  type        = bool
  default     = true
  description = "Create a dedicated, versioned DAG bucket instead of taking one. MWAA REQUIRES versioning on the source bucket -- it resolves DAG updates by object version -- so a bucket supplied from elsewhere without it fails at environment creation with a message that does not say so."
}

variable "dag_noncurrent_version_retention_days" {
  type        = number
  default     = 30
  description = "How long a superseded DAG version is kept before expiry. Versioning is mandatory for MWAA, so without this every edit accumulates a noncurrent version forever. 30 days leaves a rollback window well past the point anyone would notice a bad DAG, and expires the rest."

  validation {
    condition     = var.dag_noncurrent_version_retention_days >= 1
    error_message = "Retention must be at least 1 day; 0 would expire the version that a rollback needs."
  }
}

variable "kms_key_arn" {
  type        = string
  default     = ""
  description = "CMK for the environment (DAG bucket, CloudWatch logs, and the internal SQS queue). The key policy must allow logs.<region>.amazonaws.com, sqs.amazonaws.com and s3.amazonaws.com -- MWAA encrypts through those services, and a key that only grants the execution role fails at creation. Empty uses AWS-managed keys."
}

variable "webserver_access_mode" {
  type        = string
  default     = "PRIVATE_ONLY"
  description = "PRIVATE_ONLY or PUBLIC_ONLY. Private by default: the Airflow UI can trigger every DAG in the environment, so exposing it to the internet makes the orchestrator's blast radius the internet's."

  validation {
    condition     = contains(["PRIVATE_ONLY", "PUBLIC_ONLY"], var.webserver_access_mode)
    error_message = "webserver_access_mode must be PRIVATE_ONLY or PUBLIC_ONLY."
  }
}

variable "log_level" {
  type    = string
  default = "INFO"
}

variable "dag_s3_bucket_arn" {
  type        = string
  default     = ""
  description = "ARN of an EXISTING versioned DAG bucket. Leave empty when create_dag_bucket is true; supplying both is refused rather than silently preferring one."

  validation {
    condition     = !(var.create_dag_bucket && var.dag_s3_bucket_arn != "")
    error_message = "Set create_dag_bucket OR dag_s3_bucket_arn, not both -- two sources of truth for where DAGs live is how an environment ends up reading an empty bucket."
  }

  validation {
    condition     = var.create_dag_bucket || var.dag_s3_bucket_arn != ""
    error_message = "MWAA needs a DAG bucket: set create_dag_bucket = true or pass dag_s3_bucket_arn."
  }
}

variable "subnet_ids" {
  type        = list(string)
  description = "Two private subnet IDs in your VPC for the MWAA environment."
}

variable "security_group_ids" {
  type = list(string)
}

variable "airflow_version" {
  type    = string
  default = "2.8.1"
}

variable "environment_class" {
  type    = string
  default = "mw1.small"
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["airflow.amazonaws.com", "airflow-env.amazonaws.com"]
    }
  }
}

data "aws_caller_identity" "current" {}

# Dedicated DAG storage (MINUS-150). Versioning is not optional here: MWAA identifies DAG
# updates by S3 object version, and an unversioned source bucket fails environment creation.
resource "aws_s3_bucket" "dags" {
  count  = var.create_dag_bucket ? 1 : 0
  bucket = "${var.name_prefix}-airflow-dags-${data.aws_caller_identity.current.account_id}"
  tags   = var.tags
}

resource "aws_s3_bucket_versioning" "dags" {
  count  = var.create_dag_bucket ? 1 : 0
  bucket = aws_s3_bucket.dags[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "dags" {
  count                   = var.create_dag_bucket ? 1 : 0
  bucket                  = aws_s3_bucket.dags[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versioning above is mandatory, not a preference -- MWAA identifies DAG updates by object
# version -- and that is exactly what makes a lifecycle rule non-optional here. Every DAG
# edit leaves a noncurrent version behind, and without an expiry those accumulate for the
# life of the environment: a small file, edited often, forever. Current versions are never
# touched, because MWAA reads them.
resource "aws_s3_bucket_lifecycle_configuration" "dags" {
  count  = var.create_dag_bucket ? 1 : 0
  bucket = aws_s3_bucket.dags[0].id

  rule {
    id     = "expire-noncurrent-dag-versions"
    status = "Enabled"
    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.dag_noncurrent_version_retention_days
    }

    # A multipart upload that failed partway still bills for its parts until aborted.
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dags" {
  count  = var.create_dag_bucket ? 1 : 0
  bucket = aws_s3_bucket.dags[0].id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = var.kms_key_arn == "" ? null : var.kms_key_arn
      sse_algorithm     = var.kms_key_arn == "" ? "AES256" : "aws:kms"
    }
    bucket_key_enabled = true
  }
}

locals {
  # The created bucket wins when both are available, so a caller that flips create_dag_bucket
  # on does not silently keep pointing at the old one.
  source_bucket_arn = var.create_dag_bucket ? aws_s3_bucket.dags[0].arn : var.dag_s3_bucket_arn
}

resource "aws_iam_role" "mwaa" {
  name               = "${var.name_prefix}-mwaa-exec"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "mwaa" {
  statement {
    sid       = "DagBucket"
    actions   = ["s3:GetObject", "s3:GetBucket*", "s3:List*"]
    resources = [var.dag_s3_bucket_arn, "${var.dag_s3_bucket_arn}/*"]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:CreateLogGroup", "logs:PutLogEvents", "logs:GetLogEvents", "logs:GetLogRecord", "logs:GetLogGroupFields", "logs:GetQueryResults", "logs:DescribeLogGroups"]
    resources = ["arn:aws:logs:*:*:log-group:airflow-${var.name_prefix}-*"]
  }
}

resource "aws_iam_role_policy" "mwaa" {
  name   = "${var.name_prefix}-mwaa"
  role   = aws_iam_role.mwaa.id
  policy = data.aws_iam_policy_document.mwaa.json
}

resource "aws_mwaa_environment" "this" {
  name               = "${var.name_prefix}-airflow"
  airflow_version    = var.airflow_version
  environment_class  = var.environment_class
  execution_role_arn = aws_iam_role.mwaa.arn
  source_bucket_arn  = local.source_bucket_arn
  kms_key            = var.kms_key_arn == "" ? null : var.kms_key_arn
  # The UI can trigger every DAG in the environment; public by default would make the
  # orchestrator's blast radius the internet's.
  webserver_access_mode = var.webserver_access_mode
  dag_s3_path           = "dags"

  network_configuration {
    security_group_ids = var.security_group_ids
    subnet_ids         = var.subnet_ids
  }

  # All five streams, not two. Scheduler and worker logs are where a DAG that never runs
  # explains itself, and a webserver log is the only record of who triggered what by hand.
  logging_configuration {
    dag_processing_logs {
      enabled   = true
      log_level = var.log_level
    }
    scheduler_logs {
      enabled   = true
      log_level = var.log_level
    }
    task_logs {
      enabled   = true
      log_level = var.log_level
    }
    webserver_logs {
      enabled   = true
      log_level = var.log_level
    }
    worker_logs {
      enabled   = true
      log_level = var.log_level
    }
  }

  tags = var.tags
}

output "dag_bucket" {
  value       = var.create_dag_bucket ? aws_s3_bucket.dags[0].bucket : null
  description = "Upload DAGs to s3://<this>/dags/. Null when an existing bucket was supplied."
}

output "airflow_environment" {
  value = aws_mwaa_environment.this.name
}

output "execution_role_arn" {
  value = aws_iam_role.mwaa.arn
}
