# Module: compute-emr-serverless
# EMR Serverless Spark application for sustained / long-running transforms. Glue's
# per-minute premium wins for short, infrequent jobs; once jobs run for hours daily
# (>= TB/day tier), EMR Serverless prices better and removes cluster management.
# Auto-stop keeps an idle application free.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "release_label" {
  type    = string
  default = "emr-7.5.0"
}

variable "max_vcpu" {
  type    = string
  default = "16 vCPU"
}

variable "max_memory" {
  type    = string
  default = "64 GB"
}

variable "target_buckets" {
  type        = list(string)
  default     = []
  description = "Lake buckets the job runtime role may read/write."
}

resource "aws_emrserverless_application" "spark" {
  name          = "${var.name_prefix}-spark"
  release_label = var.release_label
  type          = "spark"
  tags          = var.tags

  maximum_capacity {
    cpu    = var.max_vcpu
    memory = var.max_memory
  }

  auto_stop_configuration {
    enabled              = true
    idle_timeout_minutes = 15
  }
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["emr-serverless.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "runtime" {
  name               = "${var.name_prefix}-emrs-runtime"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "runtime" {
  statement {
    sid     = "Lake"
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = length(var.target_buckets) > 0 ? concat(
      [for b in var.target_buckets : "arn:aws:s3:::${b}"],
      [for b in var.target_buckets : "arn:aws:s3:::${b}/*"],
    ) : ["arn:aws:s3:::${var.name_prefix}-placeholder"] # REVIEW: wire target_buckets
  }
}

resource "aws_iam_role_policy" "runtime" {
  name   = "${var.name_prefix}-emrs-runtime"
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.runtime.json
}

output "application_id" {
  value = aws_emrserverless_application.spark.id
}

output "runtime_role_arn" {
  value = aws_iam_role.runtime.arn
}
