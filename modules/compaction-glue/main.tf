# Module: compaction-glue
# Scheduled small-file compaction for the lake zones. At >= TB/day scale the small-files
# problem dominates: AWS measured 100k small files scanning 62-88% slower and eventually
# throwing S3 rate-limit errors; the target is ~128 MB objects. This job periodically
# rewrites each target prefix into right-sized Parquet.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "script_s3_bucket" {
  type        = string
  description = "Bucket to hold the compaction script."
}

variable "target_buckets" {
  type        = list(string)
  description = "Lake zone buckets whose objects the job may rewrite."
}

variable "schedule" {
  type        = string
  default     = "cron(0 3 * * ? *)"
  description = "Glue trigger schedule (default: daily 03:00 UTC, after the nightly loads)."
}

variable "worker_type" {
  type    = string
  default = "G.1X"
}

variable "number_of_workers" {
  type    = number
  default = 2
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

resource "aws_iam_role" "compact" {
  name               = "${var.name_prefix}-compact-exec"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "compact" {
  statement {
    sid     = "RewriteTargets"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = concat(
      [for b in var.target_buckets : "arn:aws:s3:::${b}"],
      [for b in var.target_buckets : "arn:aws:s3:::${b}/*"],
    )
  }
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

resource "aws_iam_role_policy" "compact" {
  name   = "${var.name_prefix}-compact"
  role   = aws_iam_role.compact.id
  policy = data.aws_iam_policy_document.compact.json
}

resource "aws_s3_object" "script" {
  bucket = var.script_s3_bucket
  key    = "scripts/compact.py"
  source = "${path.module}/scripts/compact.py"
  etag   = filemd5("${path.module}/scripts/compact.py")
  tags   = var.tags
}

resource "aws_glue_job" "compact" {
  name              = "${var.name_prefix}-compaction"
  role_arn          = aws_iam_role.compact.arn
  glue_version      = "4.0"
  worker_type       = var.worker_type
  number_of_workers = var.number_of_workers
  tags              = var.tags

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${var.script_s3_bucket}/${aws_s3_object.script.key}"
  }

  default_arguments = {
    "--job-bookmark-option" = "job-bookmark-disable" # compaction rewrites in place
    "--target_buckets"      = join(",", var.target_buckets)
  }
}

resource "aws_glue_trigger" "schedule" {
  name     = "${var.name_prefix}-compaction-schedule"
  type     = "SCHEDULED"
  schedule = var.schedule
  tags     = var.tags

  actions {
    job_name = aws_glue_job.compact.name
  }
}

output "compaction_job_name" {
  value = aws_glue_job.compact.name
}
