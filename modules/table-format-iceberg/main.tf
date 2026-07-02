# Module: table-format-iceberg
# Apache Iceberg table on the curated zone via the Glue catalog. At ~100 TB+ directory
# listings and folder-level metadata stop scaling — file/snapshot tracking, ACID commits,
# and partition evolution are why Iceberg exists (created at Netflix for exactly this).
# One table is provisioned as the pattern; add more by reusing this module block.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "table_bucket" {
  type        = string
  description = "Bucket that stores the Iceberg table data + metadata (usually the gold zone)."
}

variable "table_name" {
  type    = string
  default = "curated_events"
}

variable "columns" {
  type        = map(string)
  default     = { id = "string", event_time = "timestamp", payload = "string" }
  description = "column name => Iceberg type for the starter table (REVIEW: set your schema)."
}

resource "aws_glue_catalog_database" "iceberg" {
  name = replace("${var.name_prefix}_iceberg", "-", "_")
  tags = var.tags
}

resource "aws_glue_catalog_table" "this" {
  name          = var.table_name
  database_name = aws_glue_catalog_database.iceberg.name
  table_type    = "EXTERNAL_TABLE"

  open_table_format_input {
    iceberg_input {
      metadata_operation = "CREATE"
      version            = "2"
    }
  }

  storage_descriptor {
    location = "s3://${var.table_bucket}/iceberg/${var.table_name}"

    dynamic "columns" {
      for_each = var.columns
      content {
        name = columns.key
        type = columns.value
      }
    }
  }
}

output "database_name" {
  value = aws_glue_catalog_database.iceberg.name
}

output "table_name" {
  value = aws_glue_catalog_table.this.name
}

output "table_location" {
  value = "s3://${var.table_bucket}/iceberg/${var.table_name}"
}
