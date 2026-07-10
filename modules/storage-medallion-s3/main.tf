# Module: storage-medallion-s3
# Tiered S3 data lake (bronze/silver/gold by default) with a customer-managed KMS key,
# versioning, public-access blocks, and a lifecycle archive. Composable building block —
# the architect selects it when requirements call for a data lake / medallion storage.

variable "name_prefix" {
  type        = string
  description = "Prefix for resource names, e.g. data-platform-dev."
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "zones" {
  type        = list(string)
  default     = ["bronze", "silver", "gold"]
  description = "Storage tiers to create as separate buckets."
}

variable "retention_days" {
  type        = number
  default     = 90
  description = "Days before objects transition to Glacier (cost optimization)."
}

variable "run_id" {
  type        = string
  default     = ""
  description = "MinusOps run id, folded into bucket names so two runs sharing the same name_prefix don't collide with each other (or with an unrelated bucket in the global S3 namespace)."
}

data "aws_caller_identity" "current" {}

resource "aws_kms_key" "lake" {
  description             = "${var.name_prefix} data lake CMK"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  tags                    = var.tags
}

resource "aws_kms_alias" "lake" {
  name          = "alias/${var.name_prefix}-lake"
  target_key_id = aws_kms_key.lake.key_id
}

resource "aws_s3_bucket" "zone" {
  for_each = toset(var.zones)
  # account_id guards against colliding with an unrelated bucket in the global S3 namespace
  # (the incident this fixes); the run_id hash guards against two of our own runs colliding
  # with each other when they share the same name_prefix. Each solves a different failure mode.
  bucket = "${var.name_prefix}-${each.value}-${data.aws_caller_identity.current.account_id}-${substr(md5(var.run_id), 0, 8)}"
  tags   = merge(var.tags, { zone = each.value })
}

resource "aws_s3_bucket_public_access_block" "zone" {
  for_each                = aws_s3_bucket.zone
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "zone" {
  for_each = aws_s3_bucket.zone
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "zone" {
  for_each = aws_s3_bucket.zone
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.lake.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "zone" {
  for_each = aws_s3_bucket.zone
  bucket   = each.value.id
  rule {
    id     = "archive"
    status = "Enabled"
    filter {}
    transition {
      days          = var.retention_days
      storage_class = "GLACIER"
    }
  }
}

output "bucket_names" {
  value = { for z, b in aws_s3_bucket.zone : z => b.bucket }
}

output "kms_key_arn" {
  value = aws_kms_key.lake.arn
}
