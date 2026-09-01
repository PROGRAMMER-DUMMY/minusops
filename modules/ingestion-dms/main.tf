# Module: ingestion-dms
# Change-data-capture from an operational database (RDS, or on-premise Oracle/SAP/MySQL over
# a Site-to-Site VPN or Direct Connect) into the Bronze zone. Use when the requirement is
# "keep the lake in sync with the transactional system" rather than a periodic export.
#
# Credentials are NEVER module inputs. The endpoint reads them from Secrets Manager, so no
# password reaches a .tf file, a plan, or the state file (TerraShark FM-02).

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "subnet_ids" {
  type        = list(string)
  description = "Private subnets the replication instance runs in. Wire from networking-vpc."
}

variable "vpc_security_group_ids" {
  type        = list(string)
  default     = []
  description = "Security groups allowing egress to the source database."
}

variable "source_engine_name" {
  type        = string
  default     = "postgres"
  description = "Source engine: postgres, mysql, oracle, sqlserver, sybase, db2."
}

variable "source_secret_arn" {
  type        = string
  description = "Secrets Manager secret holding the source connection details. DMS resolves it at run time; the value never enters Terraform state."
}

variable "target_bucket" {
  type        = string
  description = "Bronze bucket name CDC output lands in."
}

variable "target_bucket_kms_key_arn" {
  type        = string
  default     = ""
  description = "CMK encrypting the target bucket. Required when the lake enforces SSE-KMS, which it does by default."
}

variable "table_mappings_json" {
  type        = string
  default     = ""
  description = "DMS table-mapping rules. Empty selects every table in every schema -- correct for a first sync, wrong once the source has tables nobody agreed to replicate."
}

variable "replication_instance_class" {
  type        = string
  default     = "dms.t3.medium"
  description = "Sizing is workload-specific; the default is the smallest class that handles ongoing CDC for a modest OLTP source."
}

variable "migration_type" {
  type        = string
  default     = "full-load-and-cdc"
  description = "full-load | cdc | full-load-and-cdc."
}

locals {
  # Replicate everything unless the operator narrowed it. Stated as a local rather than a
  # variable default so the "you are replicating every table" decision is visible in the plan.
  effective_table_mappings = var.table_mappings_json != "" ? var.table_mappings_json : jsonencode({
    rules = [{
      "rule-type"      = "selection"
      "rule-id"        = "1"
      "rule-name"      = "select-all"
      "object-locator" = { "schema-name" = "%", "table-name" = "%" }
      "rule-action"    = "include"
    }]
  })
}

data "aws_iam_policy_document" "dms_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["dms.amazonaws.com"]
    }
  }
}

# DMS assumes this to write to S3 and to read the source secret.
resource "aws_iam_role" "dms" {
  name               = "${var.name_prefix}-dms-access"
  assume_role_policy = data.aws_iam_policy_document.dms_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "dms" {
  statement {
    sid       = "WriteBronze"
    actions   = ["s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:GetObject"]
    resources = ["arn:aws:s3:::${var.target_bucket}", "arn:aws:s3:::${var.target_bucket}/*"]
  }
  statement {
    sid       = "ReadSourceSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.source_secret_arn]
  }
  dynamic "statement" {
    for_each = var.target_bucket_kms_key_arn == "" ? [] : [var.target_bucket_kms_key_arn]
    content {
      sid       = "LakeKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "dms" {
  name   = "${var.name_prefix}-dms-access"
  role   = aws_iam_role.dms.id
  policy = data.aws_iam_policy_document.dms.json
}

resource "aws_dms_replication_subnet_group" "this" {
  replication_subnet_group_id          = "${var.name_prefix}-dms"
  replication_subnet_group_description = "CDC replication subnets for ${var.name_prefix}."
  subnet_ids                           = var.subnet_ids
  tags                                 = var.tags
}

resource "aws_dms_replication_instance" "this" {
  replication_instance_id    = "${var.name_prefix}-dms"
  replication_instance_class = var.replication_instance_class
  # Private by design: a CDC instance reaching a production database has no reason to hold a
  # public address, and DMS cannot be moved out of the public subnet after creation.
  publicly_accessible         = false
  replication_subnet_group_id = aws_dms_replication_subnet_group.this.id
  vpc_security_group_ids      = var.vpc_security_group_ids
  tags                        = var.tags
}

resource "aws_dms_endpoint" "source" {
  endpoint_id   = "${var.name_prefix}-source"
  endpoint_type = "source"
  engine_name   = var.source_engine_name
  # Credentials by reference. `username`/`password` arguments exist and are exactly what puts
  # a plaintext password into the state file.
  secrets_manager_arn             = var.source_secret_arn
  secrets_manager_access_role_arn = aws_iam_role.dms.arn
  tags                            = var.tags
}

# aws_dms_s3_endpoint, not aws_dms_endpoint + an s3_settings block: the provider removed
# that block in v6 and split S3 targets into their own resource with top-level arguments.
resource "aws_dms_s3_endpoint" "target" {
  endpoint_id             = "${var.name_prefix}-bronze"
  endpoint_type           = "target"
  bucket_name             = var.target_bucket
  bucket_folder           = "cdc"
  service_access_role_arn = aws_iam_role.dms.arn
  # Parquet, not the CSV default: Bronze is read by Spark and Athena, and CSV costs a full
  # scan on every query plus loses the type information the source already had.
  data_format                       = "parquet"
  compression_type                  = "GZIP"
  encryption_mode                   = var.target_bucket_kms_key_arn == "" ? "SSE_S3" : "SSE_KMS"
  server_side_encryption_kms_key_id = var.target_bucket_kms_key_arn == "" ? null : var.target_bucket_kms_key_arn
  tags                              = var.tags
  depends_on                        = [aws_iam_role_policy.dms]
}

resource "aws_dms_replication_task" "this" {
  replication_task_id      = "${var.name_prefix}-cdc"
  migration_type           = var.migration_type
  replication_instance_arn = aws_dms_replication_instance.this.replication_instance_arn
  source_endpoint_arn      = aws_dms_endpoint.source.endpoint_arn
  target_endpoint_arn      = aws_dms_s3_endpoint.target.endpoint_arn
  table_mappings           = local.effective_table_mappings
  tags                     = var.tags
}

output "replication_task_arn" {
  value = aws_dms_replication_task.this.replication_task_arn
}

output "dms_role_arn" {
  value = aws_iam_role.dms.arn
}
