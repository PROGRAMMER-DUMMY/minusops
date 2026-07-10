# Module: query-athena
# Serving layer for analysts / BI: an Athena workgroup with a dedicated, KMS-encrypted results
# bucket and an enforced per-query scan cutoff (cost guardrail). Use when requirements include
# ad-hoc SQL, Tableau/PowerBI, or interactive analyst access.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "results_kms_key_arn" {
  type        = string
  default     = ""
  description = "Optional CMK ARN for results encryption; falls back to SSE-S3 when empty."
}

variable "bytes_scanned_cutoff" {
  type        = number
  default     = 10737418240
  description = "Per-query data scan limit in bytes (default 10 GiB)."
}

variable "run_id" {
  type        = string
  default     = ""
  description = "MinusOps run id, folded into the results bucket name so two runs sharing the same name_prefix don't collide with each other (or with an unrelated bucket in the global S3 namespace)."
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "results" {
  # account_id guards against colliding with an unrelated bucket in the global S3 namespace;
  # the run_id hash guards against two of our own runs colliding when they share the same
  # name_prefix. Same fix as storage-medallion-s3 (2026-07-04 audit finding), applied here
  # after an exhaustive read found this module had the identical unsuffixed pattern.
  bucket = "${var.name_prefix}-athena-results-${data.aws_caller_identity.current.account_id}-${substr(md5(var.run_id), 0, 8)}"
  tags   = var.tags
}

resource "aws_s3_bucket_public_access_block" "results" {
  bucket                  = aws_s3_bucket.results.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Query results are re-derivable — expire them instead of paying for them forever.
resource "aws_s3_bucket_lifecycle_configuration" "results" {
  bucket = aws_s3_bucket.results.id
  rule {
    id     = "expire_old_results"
    status = "Enabled"
    filter {}
    expiration {
      days = 30
    }
  }
}

resource "aws_athena_workgroup" "this" {
  name = "${var.name_prefix}-analysts"
  tags = var.tags

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.bytes_scanned_cutoff

    result_configuration {
      output_location = "s3://${aws_s3_bucket.results.bucket}/results/"

      encryption_configuration {
        encryption_option = var.results_kms_key_arn == "" ? "SSE_S3" : "SSE_KMS"
        kms_key_arn       = var.results_kms_key_arn == "" ? null : var.results_kms_key_arn
      }
    }
  }
}

output "workgroup_name" {
  value = aws_athena_workgroup.this.name
}

output "results_bucket" {
  value = aws_s3_bucket.results.bucket
}
