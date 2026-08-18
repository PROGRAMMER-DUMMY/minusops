# Module: compute-databricks-delta
# Unity Catalog external locations over the Medallion lake, plus optional Delta Sharing.
# Extends `databricks-workspace` -- it does not replace it: this module consumes the metastore
# and catalog that module creates, and provisions the AWS-to-UC access path on top.
#
# The shape matters. A storage CREDENTIAL wraps an IAM role; an external LOCATION binds that
# credential to one S3 prefix. Governance lives on the location, so one credential can serve
# several locations and a grant on Gold does not leak Bronze. Pointing a single external
# location at the bucket root would collapse that distinction and hand every catalog user the
# raw zone.

# Every module using databricks_* resources -- not just the root composition -- must declare
# its own required_providers source, or Terraform infers the nonexistent hashicorp/databricks
# and the root/child provider addresses disagree. Same reasoning as databricks-workspace;
# configuration (host, auth) still lives only in the composed root.
terraform {
  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = ">= 1.0"
    }
  }
}

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "metastore_id" {
  type        = string
  description = "Unity Catalog metastore from the databricks-workspace module."
}

variable "catalog_name" {
  type        = string
  description = "Catalog the external locations are registered under."
}

variable "bucket_names" {
  type        = map(string)
  description = "Medallion zone name -> bucket name, from storage-medallion-s3."
}

variable "external_zones" {
  type        = list(string)
  default     = ["gold"]
  description = "Zones to expose as external locations. Gold only by default: Silver and Bronze hold pre-redaction data, and exposing them through the catalog makes every workspace user a reader of it."
}

variable "kms_key_arn" {
  type        = string
  default     = ""
  description = "Lake CMK. Without kms:Decrypt the credential's role reads 403 and Unity Catalog reports it as a permissions error on the location, not on the key."
}

variable "databricks_account_id" {
  type        = string
  description = "Databricks account id, for the credential role's trust condition."
}

variable "delta_share_recipients" {
  type        = map(string)
  default     = {}
  description = "recipient_name => sharing identifier for Delta Sharing. Empty creates no share at all -- a share with no recipients is an open-ended grant waiting for one."
}

variable "shared_tables" {
  type        = list(string)
  default     = []
  description = "Fully-qualified tables to place in the share (catalog.schema.table). Explicit, never a whole schema: a schema-level share silently includes every table added to it later."
}

locals {
  zones = { for z in var.external_zones : z => var.bucket_names[z] if contains(keys(var.bucket_names), z) }
}

data "aws_iam_policy_document" "uc_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type = "AWS"
      # Databricks' own Unity Catalog principal. Fixed account id, published by Databricks.
      identifiers = ["arn:aws:iam::414351767826:role/unity-catalog-prod-UCMasterRole-14S5ZJVKOTYTL"]
    }
    # Same confused-deputy problem as the Snowflake module: Databricks' account is shared
    # across customers, so the external id is what ties this role to YOUR account.
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.databricks_account_id]
    }
  }
}

resource "aws_iam_role" "uc" {
  name               = "${var.name_prefix}-uc-external"
  assume_role_policy = data.aws_iam_policy_document.uc_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "uc" {
  statement {
    sid = "ReadWriteExternalZones"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket",
    "s3:GetBucketLocation", "s3:ListBucketMultipartUploads", "s3:AbortMultipartUpload"]
    resources = concat(
      [for b in values(local.zones) : "arn:aws:s3:::${b}"],
      [for b in values(local.zones) : "arn:aws:s3:::${b}/*"],
    )
  }
  dynamic "statement" {
    for_each = var.kms_key_arn == "" ? [] : [var.kms_key_arn]
    content {
      sid       = "LakeKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "uc" {
  name   = "${var.name_prefix}-uc-external"
  role   = aws_iam_role.uc.id
  policy = data.aws_iam_policy_document.uc.json
}

resource "databricks_storage_credential" "this" {
  name         = "${var.name_prefix}-lake"
  metastore_id = var.metastore_id
  aws_iam_role {
    role_arn = aws_iam_role.uc.arn
  }
  comment = "Medallion lake access for ${var.catalog_name}"
}

# One location per zone, not one for the bucket root: governance is granted on the location,
# so a single root location would make a Gold grant a Bronze grant too.
resource "databricks_external_location" "zone" {
  for_each        = local.zones
  name            = "${var.name_prefix}-${each.key}"
  url             = "s3://${each.value}/"
  credential_name = databricks_storage_credential.this.name
  metastore_id    = var.metastore_id
  comment         = "Medallion ${each.key} zone"
}

# --- Delta Sharing ----------------------------------------------------------------------
# Only when recipients are named. A share created "ready for later" is an object someone
# grants access to without revisiting what is in it.

resource "databricks_share" "this" {
  count = length(var.delta_share_recipients) > 0 && length(var.shared_tables) > 0 ? 1 : 0
  name  = "${var.name_prefix}-share"

  dynamic "object" {
    for_each = var.shared_tables
    content {
      name             = object.value
      data_object_type = "TABLE"
      # Full history, not just the latest snapshot: a recipient reading a Delta table without
      # history cannot do incremental reads and re-scans everything on every refresh.
      history_data_sharing_status = "ENABLED"
    }
  }
}

resource "databricks_recipient" "this" {
  for_each                           = var.delta_share_recipients
  name                               = each.key
  authentication_type                = "TOKEN"
  data_recipient_global_metastore_id = each.value
  comment                            = "Delta Sharing recipient ${each.key}"
}

resource "databricks_grants" "share" {
  count = length(var.delta_share_recipients) > 0 && length(var.shared_tables) > 0 ? 1 : 0
  share = databricks_share.this[0].name

  dynamic "grant" {
    for_each = var.delta_share_recipients
    content {
      principal = grant.key
      # SELECT only. A share is a one-way publication; anything writable is a pipeline, not
      # a share.
      privileges = ["SELECT"]
    }
  }
}

output "storage_credential_name" {
  value = databricks_storage_credential.this.name
}

output "external_location_urls" {
  value = { for k, v in databricks_external_location.zone : k => v.url }
}

output "uc_role_arn" {
  value = aws_iam_role.uc.arn
}
