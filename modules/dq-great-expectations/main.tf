# Module: dq-great-expectations
# Data-quality enforcement: a Glue Python-shell job that runs Great Expectations suites against
# the target buckets and writes Data Docs / validation results to a dedicated results bucket.
# `fail_on_error` is surfaced to the job so a failing suite can halt the pipeline.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "target_buckets" {
  type        = list(string)
  description = "Bucket names the quality job reads to validate."
}

variable "fail_on_error" {
  type    = bool
  default = true
}

variable "script_s3_bucket" {
  type        = string
  description = "Bucket holding the Great Expectations runner script."
}

variable "script_s3_key" {
  type    = string
  default = "scripts/great_expectations_runner.py"
}

resource "aws_s3_bucket" "results" {
  bucket = "${var.name_prefix}-dq-results"
  tags   = var.tags
}

resource "aws_s3_bucket_public_access_block" "results" {
  bucket                  = aws_s3_bucket.results.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
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

resource "aws_iam_role" "dq" {
  name               = "${var.name_prefix}-dq-exec"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "dq" {
  statement {
    sid       = "ReadTargets"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = concat([for b in var.target_buckets : "arn:aws:s3:::${b}"], [for b in var.target_buckets : "arn:aws:s3:::${b}/*"])
  }
  statement {
    sid       = "WriteResults"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.results.arn, "${aws_s3_bucket.results.arn}/*"]
  }
}

resource "aws_iam_role_policy" "dq" {
  name   = "${var.name_prefix}-dq"
  role   = aws_iam_role.dq.id
  policy = data.aws_iam_policy_document.dq.json
}

resource "aws_glue_job" "dq" {
  name         = "${var.name_prefix}-data-quality"
  role_arn     = aws_iam_role.dq.arn
  glue_version = "4.0"
  tags         = var.tags

  command {
    name            = "pythonshell"
    python_version  = "3.9"
    script_location = "s3://${var.script_s3_bucket}/${var.script_s3_key}"
  }

  default_arguments = {
    "--fail_on_error" = tostring(var.fail_on_error)
    "--results_bucket" = aws_s3_bucket.results.bucket
  }
}

output "dq_job_name" {
  value = aws_glue_job.dq.name
}

output "dq_results_bucket" {
  value = aws_s3_bucket.results.bucket
}
