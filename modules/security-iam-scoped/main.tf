# Module: security-iam-scoped
# Least-privilege read access to the Gold zone for downstream BI and data-science consumers,
# including consumers in another AWS account.
#
# TWO THINGS THIS MODULE REFUSES TO DO, both deliberate:
#
# 1. No `Resource = "*"`. Every statement names the Gold bucket ARN, the CMK ARN, or the
#    workgroup it was given. A least-privilege module with a wildcard in it is a wildcard
#    with a reassuring name.
# 2. No trust policy without an external ID. A cross-account role whose trust policy names
#    only the peer account is the confused-deputy problem: role ARNs are not secrets -- they
#    show up in logs, error messages and support tickets -- and any principal in that account
#    who learns this one can assume it. The sts:ExternalId condition is what makes the trust
#    specific to the arrangement you actually made.
#
# KMS IS NOT OPTIONAL WHEN THE LAKE IS ENCRYPTED. s3:GetObject on a CMK-encrypted object
# returns AccessDenied that names S3, not KMS, which is one of the longer debugging sessions
# in this stack. The decrypt grant is issued alongside the read grant, scoped to the one key.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "gold_bucket_arn" {
  type        = string
  description = "ARN of the curated bucket consumers may read. Gold only: granting Bronze hands out the raw, un-quarantined, un-masked copy of the same data."
}

variable "gold_prefixes" {
  type        = list(string)
  default     = ["*"]
  description = "Key prefixes inside the bucket, e.g. [\"marketing/*\"]. The default is every object in the named bucket -- still bucket-scoped, never account-scoped."
}

variable "kms_key_arn" {
  type        = string
  default     = ""
  description = "CMK protecting the Gold objects. Empty means SSE-S3 and no decrypt statement is emitted; a wrong non-empty value produces AccessDenied that blames S3."
}

variable "athena_workgroup_arn" {
  type        = string
  default     = ""
  description = "Workgroup consumers may run queries in. Empty omits the Athena statements entirely rather than widening them."
}

variable "trusted_external_principals" {
  type        = list(string)
  default     = []
  description = "Account roots or role ARNs allowed to assume the consumer role. Empty creates the policy but no role -- useful for attaching to an in-account principal you manage elsewhere."
}

variable "external_id" {
  type        = string
  default     = ""
  description = "Anti-confused-deputy value the assuming party must present. Required whenever trusted_external_principals is non-empty; see the validation below."

  validation {
    condition     = var.external_id == "" || length(var.external_id) >= 16
    error_message = "external_id must be at least 16 characters -- a guessable one provides no protection at all."
  }
}

locals {
  create_role  = length(var.trusted_external_principals) > 0
  object_arns  = [for p in var.gold_prefixes : "${var.gold_bucket_arn}/${p}"]
  emit_kms     = var.kms_key_arn != ""
  emit_athena  = var.athena_workgroup_arn != ""
}

# Fails the PLAN rather than the audit: a cross-account trust with no external ID is the
# defect this module exists to prevent, so it must not be expressible.
#
# No `count` guard here, and the condition is a real expression rather than a constant. Both
# matter: `terraform validate` rejects `condition = false` outright ("must refer to at least
# one object from elsewhere in the configuration"), and a counted resource only evaluates its
# precondition when it exists -- which is fine, but leaves nothing for the validator to check.
# Always present, condition references both variables, fails only on the bad combination.
resource "terraform_data" "external_id_required" {
  lifecycle {
    precondition {
      condition     = length(var.trusted_external_principals) == 0 || var.external_id != ""
      error_message = "trusted_external_principals is set but external_id is empty. A cross-account role without an sts:ExternalId condition can be assumed by anyone who learns its ARN."
    }
  }
}

data "aws_iam_policy_document" "consumer_read" {
  statement {
    sid       = "ListTheGoldBucketOnly"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [var.gold_bucket_arn]
  }

  statement {
    sid       = "ReadGoldObjects"
    actions   = ["s3:GetObject"]
    resources = local.object_arns
  }

  dynamic "statement" {
    for_each = local.emit_kms ? [1] : []
    content {
      sid       = "DecryptGoldObjects"
      actions   = ["kms:Decrypt", "kms:DescribeKey"]
      resources = [var.kms_key_arn]
    }
  }

  dynamic "statement" {
    for_each = local.emit_athena ? [1] : []
    content {
      sid = "QueryThroughTheNamedWorkgroup"
      actions = [
        "athena:StartQueryExecution",
        "athena:GetQueryExecution",
        "athena:GetQueryResults",
        "athena:StopQueryExecution",
      ]
      resources = [var.athena_workgroup_arn]
    }
  }
}

resource "aws_iam_policy" "consumer_read" {
  name   = "${var.name_prefix}-gold-consumer-read"
  policy = data.aws_iam_policy_document.consumer_read.json
  tags   = var.tags
}

data "aws_iam_policy_document" "assume" {
  count = local.create_role ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = var.trusted_external_principals
    }

    # The whole point of this module's trust policy.
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.external_id]
    }
  }
}

resource "aws_iam_role" "consumer" {
  count = local.create_role ? 1 : 0

  name               = "${var.name_prefix}-gold-consumer"
  assume_role_policy = data.aws_iam_policy_document.assume[0].json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "consumer" {
  count = local.create_role ? 1 : 0

  role       = aws_iam_role.consumer[0].name
  policy_arn = aws_iam_policy.consumer_read.arn
}

output "policy_arn" {
  value = aws_iam_policy.consumer_read.arn
}

output "consumer_role_arn" {
  value       = local.create_role ? aws_iam_role.consumer[0].arn : ""
  description = "Empty when no external principals were declared; attach policy_arn to your own principal instead."
}
