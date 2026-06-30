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
    script_location = "s3://${var.script_s3_bucket}/${each.value}"
  }
}

output "glue_job_names" {
  value = { for k, j in aws_glue_job.this : k => j.name }
}

output "glue_role_arn" {
  value = aws_iam_role.glue.arn
}
