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

# --- Per-consumer-group access (pillar 13) ----------------------------------------------
#
# The scalar inputs above describe ONE consumer. Real organisations grant Gold to several
# groups at once -- analysts, data science, modelling -- each reading a different prefix and
# each carrying its own cost centre. One role shared between them is the wildcard this module
# exists to refuse, wearing a narrower name.
#
# This is additive: `consumers = {}` leaves the module byte-identical to the scalar form. The
# two are mutually exclusive rather than merged, because a half-migrated caller would grant
# the scalar policy AND every per-group policy, which is strictly more access than either.

variable "consumers" {
  type = map(object({
    gold_prefixes               = optional(list(string), ["*"])
    athena_workgroup_arn        = optional(string, "")
    trusted_external_principals = optional(list(string), [])
    external_id                 = optional(string, "")
    cost_center                 = optional(string, "")
  }))
  default     = {}
  description = "Consumer group name => its scope. Empty uses the scalar inputs above as a single unnamed consumer."
}

resource "terraform_data" "one_consumer_model_only" {
  lifecycle {
    precondition {
      condition     = length(var.consumers) == 0 || length(var.trusted_external_principals) == 0
      error_message = "consumers and trusted_external_principals are both set. Pick one model: the scalar inputs describe a single consumer, the map describes several. Setting both grants the scalar policy in addition to every per-group policy."
    }
  }
}

resource "terraform_data" "group_external_id_required" {
  for_each = var.consumers

  lifecycle {
    precondition {
      condition     = length(each.value.trusted_external_principals) == 0 || each.value.external_id != ""
      error_message = "consumer group has trusted_external_principals but no external_id. A cross-account role without an sts:ExternalId condition can be assumed by anyone who learns its ARN."
    }
  }
}

data "aws_iam_policy_document" "group_read" {
  for_each = var.consumers

  statement {
    sid       = "ListTheGoldBucketOnly"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [var.gold_bucket_arn]
  }

  statement {
    sid       = "ReadGoldObjects"
    actions   = ["s3:GetObject"]
    resources = [for p in each.value.gold_prefixes : "${var.gold_bucket_arn}/${p}"]
  }

  dynamic "statement" {
    for_each = var.kms_key_arn != "" ? [1] : []
    content {
      sid       = "DecryptGoldObjects"
      actions   = ["kms:Decrypt", "kms:DescribeKey"]
      resources = [var.kms_key_arn]
    }
  }

  dynamic "statement" {
    for_each = each.value.athena_workgroup_arn != "" ? [1] : []
    content {
      sid = "QueryThroughTheNamedWorkgroup"
      actions = [
        "athena:StartQueryExecution",
        "athena:GetQueryExecution",
        "athena:GetQueryResults",
        "athena:StopQueryExecution",
      ]
      resources = [each.value.athena_workgroup_arn]
    }
  }
}

resource "aws_iam_policy" "group_read" {
  for_each = var.consumers

  name   = "${var.name_prefix}-gold-${each.key}-read"
  policy = data.aws_iam_policy_document.group_read[each.key].json
  # The cost centre is stamped on the policy so Cost Explorer can attribute per group. An
  # empty value is omitted rather than written: a blank tag reads as allocated and carries
  # no owner.
  tags = merge(var.tags, each.value.cost_center == "" ? {} : { cost_center = each.value.cost_center })
}

data "aws_iam_policy_document" "group_assume" {
  for_each = { for k, v in var.consumers : k => v if length(v.trusted_external_principals) > 0 }

  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = each.value.trusted_external_principals
    }

    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [each.value.external_id]
    }
  }
}

resource "aws_iam_role" "group" {
  for_each = { for k, v in var.consumers : k => v if length(v.trusted_external_principals) > 0 }

  name               = "${var.name_prefix}-gold-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.group_assume[each.key].json
  tags               = merge(var.tags, each.value.cost_center == "" ? {} : { cost_center = each.value.cost_center })
}

resource "aws_iam_role_policy_attachment" "group" {
  for_each = aws_iam_role.group

  role       = each.value.name
  policy_arn = aws_iam_policy.group_read[each.key].arn
}

output "consumer_policy_arns" {
  value       = { for k, p in aws_iam_policy.group_read : k => p.arn }
  description = "Consumer group name => its least-privilege read policy ARN."
}

output "consumer_role_arns" {
  value       = { for k, r in aws_iam_role.group : k => r.arn }
  description = "Consumer group name => its role ARN. A group with no external principals gets a policy and no role; attach it to a principal you manage."
}

output "consumer_cost_centers" {
  value       = { for k, v in var.consumers : k => v.cost_center if v.cost_center != "" }
  description = "Consumer group name => cost centre tag value, for per-group budget filters."
}
