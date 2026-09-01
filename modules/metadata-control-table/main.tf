# Module: metadata-control-table
# FALLBACK-only pipeline control table (Amazon DynamoDB) backing dynamic Airflow/Step Function
# parameters (schedule, cluster size, worker count, timeout, status) queried at DAG-parse /
# pre-execution time instead of hardcoded in Python.
#
# PRIMARY path (PRD 6.8.4/6.8.5) is reading an EXISTING enterprise control table -- under its
# own name, with its own column names -- via scripts/fetch_pipeline_config.py's caller-supplied
# column mapping. Mature metadata-driven platforms already have this table; MinusOps does not
# assume a fixed schema like `tbl_pipeline_control_config` and does not overwrite one that
# exists. This module is the FALLBACK: a table to provision only for a greenfield project that
# has none yet. Nothing in the generation catalog auto-wires this module into a composed stack
# -- selecting it is an explicit choice, matching "opt-in, not the default".
#
# Even the fallback table's key attribute names are inputs (partition_key_name/sort_key_name),
# not hardcoded, so a greenfield table can still be created under a company's own naming
# convention from day one.
#
# Identity boundary: rows in this table are PIPELINE CONFIG (schedule, cluster size, timeouts),
# never employee credentials. If a consumer later adds an identity/access-mapping column to this
# or any other table, its value must be an IAM role ARN or an Identity Center group id -- never
# an access key. This module takes no credential as a Terraform variable.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "run_id" {
  type        = string
  default     = ""
  description = "MinusOps run id, folded into the default table name so two runs sharing the same name_prefix don't collide. Ignored when table_name is set explicitly."
}

variable "table_name" {
  type        = string
  default     = ""
  description = "Explicit table name, e.g. to match an enterprise's own naming convention. Empty derives '<name_prefix>-pipeline-control-<run hash>'."
}

variable "partition_key_name" {
  type        = string
  default     = "feed_id"
  description = "Primary key attribute name. Rename to match an existing convention (e.g. 'FeedID', 'pipeline_key') -- this module never assumes a fixed schema."
}

variable "partition_key_type" {
  type        = string
  default     = "S"
  description = "DynamoDB attribute type for the partition key: S, N, or B."
}

variable "sort_key_name" {
  type        = string
  default     = ""
  description = "Optional sort key attribute name, e.g. 'environment' to key one feed's config per env. Empty omits a sort key."
}

variable "sort_key_type" {
  type        = string
  default     = "S"
  description = "DynamoDB attribute type for the sort key: S, N, or B. Ignored when sort_key_name is empty."
}

variable "billing_mode" {
  type        = string
  default     = "PAY_PER_REQUEST"
  description = "PAY_PER_REQUEST (on-demand, zero idle cost -- default) or PROVISIONED."
  validation {
    condition     = contains(["PAY_PER_REQUEST", "PROVISIONED"], var.billing_mode)
    error_message = "billing_mode must be PAY_PER_REQUEST or PROVISIONED."
  }
}

variable "read_capacity" {
  type        = number
  default     = 5
  description = "Provisioned read capacity units. Only takes effect when billing_mode = PROVISIONED."
}

variable "write_capacity" {
  type        = number
  default     = 5
  description = "Provisioned write capacity units. Only takes effect when billing_mode = PROVISIONED."
}

variable "kms_key_arn" {
  type        = string
  default     = ""
  description = "Optional CMK ARN for encryption at rest. Empty still encrypts (DynamoDB's AWS-owned default key) -- this only opts into a customer-managed key."
}

variable "point_in_time_recovery" {
  type        = bool
  default     = true
  description = "Continuous backups. A control table is small and cheap to protect; disable only for a throwaway dev/test table."
}

# ponytail: single table, no GSIs. A control table is looked up by its own key (the feed/pipeline
# id); add a global secondary index only once a real query pattern needs a different access path.
resource "aws_dynamodb_table" "control" {
  name         = var.table_name != "" ? var.table_name : "${var.name_prefix}-pipeline-control-${substr(md5(var.run_id), 0, 8)}"
  billing_mode = var.billing_mode
  hash_key     = var.partition_key_name
  range_key    = var.sort_key_name != "" ? var.sort_key_name : null

  read_capacity  = var.billing_mode == "PROVISIONED" ? var.read_capacity : null
  write_capacity = var.billing_mode == "PROVISIONED" ? var.write_capacity : null

  attribute {
    name = var.partition_key_name
    type = var.partition_key_type
  }

  dynamic "attribute" {
    for_each = var.sort_key_name != "" ? [1] : []
    content {
      name = var.sort_key_name
      type = var.sort_key_type
    }
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn != "" ? var.kms_key_arn : null
  }

  point_in_time_recovery {
    enabled = var.point_in_time_recovery
  }

  tags = var.tags
}

output "table_name" {
  value = aws_dynamodb_table.control.name
}

output "table_arn" {
  value = aws_dynamodb_table.control.arn
}
