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

variable "gold_bucket" {
  type        = string
  default     = ""
  description = "Curated (Gold) bucket the catalog database points at. Empty leaves location_uri unset, which is valid -- table-level locations still work."
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

# MINUS-110. An Athena workgroup with no catalog database has nothing to query: the
# 2026-08-17 run provisioned the workgroup and stopped there. This creates the database the
# Gold zone's tables live in.
#
# Deliberately NO table definitions. A table needs a real column schema, and inventing one
# produces tables that do not match the data and fail on first query -- worse than no table.
# Tables come from whatever actually knows the schema: dbt models (src/dbt/), a CTAS, or a
# Glue crawler. Glue database names allow only lowercase alphanumerics and underscores, so
# the hyphenated name_prefix is translated rather than passed through.
resource "aws_glue_catalog_database" "gold" {
  name         = "${replace(lower(var.name_prefix), "-", "_")}_gold"
  description  = "Curated (Gold) tables for ${var.name_prefix}, queried through the ${aws_athena_workgroup.this.name} workgroup."
  location_uri = var.gold_bucket == "" ? null : "s3://${var.gold_bucket}/"
}

output "catalog_database" {
  value = aws_glue_catalog_database.gold.name
}

output "workgroup_name" {
  value = aws_athena_workgroup.this.name
}

output "results_bucket" {
  value = aws_s3_bucket.results.bucket
}
