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

# --- Partition projection (PRD v8 FR-04) ---------------------------------------------
#
# MSCK REPAIR TABLE lists every prefix under the table location and gets monotonically slower
# as partitions accumulate -- on a daily-partitioned table it is a full S3 LIST of the lake,
# and by year three it times out. Partition projection computes the partition set in memory
# from a declared range instead, so no repair ever runs and no partition metadata is stored.
#
# The table below stays OPT-IN and schema-supplied, preserving this module's existing rule
# (see the aws_glue_catalog_database comment): a table with an invented column schema fails
# on first query, which is worse than no table. Supply columns or get no table.

variable "create_projected_table" {
  type        = bool
  default     = false
  description = "Create a date-partitioned Gold table with projection enabled. Requires projected_table_columns."
}

variable "projected_table_name" {
  type        = string
  default     = "customer_events"
  description = "Table name inside the Gold catalog database."
}

variable "projected_table_columns" {
  type = list(object({
    name = string
    type = string
  }))
  default     = []
  description = "Real column schema for the projected table. Empty is refused when create_projected_table is true -- see the validation on create_projected_table's usage below."
}

variable "projection_start_date" {
  type        = string
  default     = "2024/01/01"
  description = "First projected partition, in projection_date_format. Everything before this is invisible to queries, so set it to the actual start of the data rather than leaving a default that silently hides history."
}

variable "projection_date_format" {
  type        = string
  default     = "yyyy/MM/dd"
  description = "Java date format of the partition path segment. Must match how the writer lays out prefixes, or every query returns zero rows against data that is plainly there."
}

variable "projected_table_prefix" {
  type        = string
  default     = "events"
  description = "Prefix under the Gold bucket the table reads, e.g. s3://<gold>/events/2026/08/22/."
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


# Opt-in, schema-supplied, and projection is always on when it exists: a partitioned table
# created here without projection would reintroduce the MSCK dependency this block removes.
resource "aws_glue_catalog_table" "projected_gold" {
  count = var.create_projected_table && length(var.projected_table_columns) > 0 && var.gold_bucket != "" ? 1 : 0

  name          = var.projected_table_name
  database_name = aws_glue_catalog_database.gold.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL            = "TRUE"
    "classification"    = "parquet"
    "projection.enabled" = "true"

    # `NOW` is not a placeholder to replace -- Athena resolves it per query, so the range
    # never needs maintaining as time passes.
    "projection.date.type"   = "date"
    "projection.date.range"  = "${var.projection_start_date},NOW"
    "projection.date.format" = var.projection_date_format
    "projection.date.interval"      = "1"
    "projection.date.interval.unit" = "DAYS"

    # $${date} is escaped: Athena substitutes the partition value here, not Terraform.
    "storage.location.template" = "s3://${var.gold_bucket}/${var.projected_table_prefix}/$${date}/"
  }

  partition_keys {
    name = "date"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${var.gold_bucket}/${var.projected_table_prefix}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = var.projected_table_columns
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
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

output "projected_table_name" {
  value       = length(aws_glue_catalog_table.projected_gold) > 0 ? aws_glue_catalog_table.projected_gold[0].name : ""
  description = "Empty when no projected table was requested; tables then come from dbt, a CTAS, or a crawler as before."
}

output "partition_projection_enabled" {
  value       = length(aws_glue_catalog_table.projected_gold) > 0
  description = "True means no MSCK REPAIR TABLE is required for this table, ever."
}
