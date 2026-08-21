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

variable "force_destroy" {
  type        = bool
  default     = false
  description = "Allow `terraform destroy` to delete a non-empty bucket. Defaults to false so production data is never silently destroyable; the synthesizer sets it from `var.environment == \"dev\"` for ephemeral runs, where the alternative is a BucketNotEmpty failure that strands the whole stack."
}

# --- Disaster recovery (MINUS-132) -------------------------------------------------------
# CRR takes an EXISTING destination bucket rather than creating one. A destination in another
# region needs a second provider configuration, which only the root module can supply; taking
# the ARN keeps this module single-provider and also supports the common enterprise shape
# where the DR bucket lives in a separate account owned by a different team.

variable "replication_destination_bucket_arns" {
  type        = map(string)
  default     = {}
  description = "zone => existing destination bucket ARN in the DR region, e.g. { gold = \"arn:aws:s3:::acme-gold-dr\" }. One destination PER ZONE, not one shared bucket: S3 replication preserves the object key exactly and cannot add a prefix, so three zones replicating into one bucket would overwrite each other. Empty disables replication; listing only the zones that matter is the normal case (Gold is usually the only tier worth DR cost)."
}

variable "replication_destination_kms_key_arn" {
  type        = string
  default     = ""
  description = "CMK in the destination region used to re-encrypt replicas. Required when the destination enforces SSE-KMS."
}

variable "multi_region_kms" {
  type        = bool
  default     = false
  description = "Create the lake CMK as a multi-region key so a replica key can be created in the DR region. NOTE: flipping this on an existing key REPLACES it -- objects encrypted under the old key are not readable with the new one. Decide before the first apply."
}

data "aws_caller_identity" "current" {}

# No explicit `policy` here, deliberately (MINUS-112). Omitting it keeps AWS's default key
# policy, which delegates to the account root -- and that root delegation is precisely what
# lets the IAM role policies in compute-glue-etl / query-athena grant KMS access at all.
# Replacing it with a service-principal-only policy is the classic way to lock yourself out
# of a CMK, and it would not have fixed the 403 the 2026-08-17 run hit: that was a missing
# kms:GenerateDataKey on the Glue role, granted via IAM in compute-glue-etl. Add explicit
# service-principal statements (with kms:ViaService conditions) only if a service must use
# the key without an assumable role -- and keep the root statement when you do.
resource "aws_kms_key" "lake" {
  description             = "${var.name_prefix} data lake CMK"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  multi_region            = var.multi_region_kms
  tags                    = var.tags
}

# A deleted CMK sits in PendingDeletion for 7-30 days, but its alias is freed immediately --
# so a same-name recreate collides on the alias, not the key. The run_id hash (the same suffix
# the buckets below use) keeps each run's alias distinct.
resource "aws_kms_alias" "lake" {
  name          = "alias/${var.name_prefix}-${substr(md5(var.run_id), 0, 8)}-lake"
  target_key_id = aws_kms_key.lake.key_id
}

resource "aws_s3_bucket" "zone" {
  for_each = toset(var.zones)
  # account_id guards against colliding with an unrelated bucket in the global S3 namespace
  # (the incident this fixes); the run_id hash guards against two of our own runs colliding
  # with each other when they share the same name_prefix. Each solves a different failure mode.
  bucket        = "${var.name_prefix}-${each.value}-${data.aws_caller_identity.current.account_id}-${substr(md5(var.run_id), 0, 8)}"
  force_destroy = var.force_destroy
  tags          = merge(var.tags, { zone = each.value })
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

locals {
  # Only zones that both exist and have a destination declared.
  replicated_zones    = { for z, b in aws_s3_bucket.zone : z => b if contains(keys(var.replication_destination_bucket_arns), z) }
  replication_enabled = length(var.replication_destination_bucket_arns) > 0
}

data "aws_iam_policy_document" "replication_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "replication" {
  count              = local.replication_enabled ? 1 : 0
  name               = "${var.name_prefix}-s3-replication"
  assume_role_policy = data.aws_iam_policy_document.replication_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "replication" {
  count = local.replication_enabled ? 1 : 0
  statement {
    sid       = "ReadSource"
    actions   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
    resources = [for b in aws_s3_bucket.zone : b.arn]
  }
  statement {
    sid       = "ReadSourceObjects"
    actions   = ["s3:GetObjectVersionForReplication", "s3:GetObjectVersionAcl", "s3:GetObjectVersionTagging"]
    resources = [for b in aws_s3_bucket.zone : "${b.arn}/*"]
  }
  statement {
    sid       = "WriteReplicas"
    actions   = ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"]
    resources = [for arn in values(var.replication_destination_bucket_arns) : "${arn}/*"]
  }
  # Replication decrypts with the source key and re-encrypts with the destination key, so
  # both grants are required; SSE-KMS replication silently stops without them.
  statement {
    sid       = "DecryptSource"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.lake.arn]
  }
  dynamic "statement" {
    for_each = var.replication_destination_kms_key_arn == "" ? [] : [var.replication_destination_kms_key_arn]
    content {
      sid       = "EncryptReplicas"
      actions   = ["kms:Encrypt", "kms:GenerateDataKey"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "replication" {
  count  = local.replication_enabled ? 1 : 0
  name   = "${var.name_prefix}-s3-replication"
  role   = aws_iam_role.replication[0].id
  policy = data.aws_iam_policy_document.replication[0].json
}

resource "aws_s3_bucket_replication_configuration" "zone" {
  for_each = local.replicated_zones
  bucket   = each.value.id
  role     = aws_iam_role.replication[0].arn
  # Versioning must exist before a replication config is accepted.
  depends_on = [aws_s3_bucket_versioning.zone]

  rule {
    id     = "dr-${each.key}"
    status = "Enabled"
    filter {}
    delete_marker_replication {
      status = "Enabled"
    }
    destination {
      bucket        = var.replication_destination_bucket_arns[each.key]
      storage_class = "STANDARD_IA"
      dynamic "encryption_configuration" {
        for_each = var.replication_destination_kms_key_arn == "" ? [] : [var.replication_destination_kms_key_arn]
        content {
          replica_kms_key_id = encryption_configuration.value
        }
      }
    }
    source_selection_criteria {
      sse_kms_encrypted_objects {
        # The lake is SSE-KMS everywhere; without this, encrypted objects are skipped.
        status = "Enabled"
      }
    }
  }
}

output "replication_role_arn" {
  value = local.replication_enabled ? aws_iam_role.replication[0].arn : ""
}

variable "access_log_bucket" {
  type        = string
  default     = ""
  description = "Existing bucket receiving S3 server access logs. Empty disables logging. Takes an existing bucket rather than creating one: the target must not be a medallion zone (each delivery is an object write, which would generate another log record), and delivery is billed per record."
}

# Answers "who read the Gold data", which nothing else here covers -- CloudTrail data events
# are billed per request and are off by default in governance-observability. Opt-in for the
# same reason as replication: the destination must already exist.
resource "aws_s3_bucket_logging" "zone" {
  for_each = var.access_log_bucket == "" ? {} : aws_s3_bucket.zone

  bucket        = each.value.id
  target_bucket = var.access_log_bucket
  target_prefix = "s3-access/${var.name_prefix}/${each.key}/"
}

output "bucket_names" {
  value = { for z, b in aws_s3_bucket.zone : z => b.bucket }
}

output "kms_key_arn" {
  value = aws_kms_key.lake.arn
}
