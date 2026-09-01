# Module: dbt-semantic-layer
# Code-native semantic layer: dbt Semantic Layer / MetricFlow definitions that live in Git
# next to the models they describe.
#
# WHY A SEMANTIC LAYER AT ALL. Without one, "revenue" is re-implemented in every dashboard,
# notebook and LLM prompt, and the four answers disagree by a filter nobody documented. With
# one, the join keys, the grain and the exclusions are declared once and every consumer --
# including a text-to-SQL agent -- queries the governed metric instead of inventing a join.
#
# WHAT THIS MODULE ACTUALLY PROVISIONS: almost nothing in AWS. The deliverable is the
# scaffold under models/, which the domain team edits and runs through their own dbt
# invocation. The Terraform here is the S3 location the compiled manifest is published to, so
# downstream tooling has one address to read it from. Provisioning a dbt Cloud account or a
# scheduler from here would put credentials in a control plane that holds none.
#
# Pairs with query-athena (dbt-athena adapter) or warehouse-snowflake-aws.

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
  description = "MinusOps run id, folded into the bucket name so two runs sharing a name_prefix do not collide in the global S3 namespace."
}

variable "manifest_retention_days" {
  type        = number
  default     = 90
  description = "How long compiled manifests are kept. They are regenerable, but keeping a quarter of them is what lets you answer 'what did this metric mean in March'."
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "manifests" {
  bucket = "${var.name_prefix}-dbt-manifests-${data.aws_caller_identity.current.account_id}-${substr(md5(var.run_id), 0, 8)}"
  tags   = var.tags
}

resource "aws_s3_bucket_public_access_block" "manifests" {
  bucket                  = aws_s3_bucket.manifests.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "manifests" {
  bucket = aws_s3_bucket.manifests.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "manifests" {
  bucket = aws_s3_bucket.manifests.id

  rule {
    id     = "expire_old_manifests"
    status = "Enabled"

    filter {}

    expiration {
      days = var.manifest_retention_days
    }
  }
}

output "manifest_bucket" {
  value = aws_s3_bucket.manifests.bucket
}

output "semantic_model_path" {
  value       = "models/semantic_models.yml"
  description = "Scaffold shipped with this module; edit it in the domain repo after `minusctl export`."
}
