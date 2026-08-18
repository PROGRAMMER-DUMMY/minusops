# Module: warehouse-snowflake-aws
# The AWS half of a Snowflake integration: the cross-account role Snowflake assumes to read
# S3, the bucket grants scoped to it, and the SQS notification path Snowpipe auto-ingest
# needs. Use when Snowflake is the warehouse and the lake stays in S3 -- data is read in
# place through an external stage rather than copied into Snowflake-managed storage.
#
# **This module deliberately provisions no Snowflake objects.** The snowflake provider would
# need account credentials, and the integration is a two-sided handshake that cannot be done
# in one pass anyway: Snowflake generates the IAM user ARN and external id only AFTER its
# STORAGE INTEGRATION exists, and this role's trust policy needs both. Trying to do both
# sides in one apply produces a cycle, not a shortcut. See the output notes for the order.
#
# The external id is the whole security story here (TerraShark SEC-05). Without it, the trust
# policy says "any principal Snowflake controls may assume this role" -- which includes every
# other Snowflake customer's account, since they share Snowflake's AWS account. This is the
# textbook confused-deputy, and the external id is the fix.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "stage_bucket" {
  type        = string
  description = "S3 bucket Snowflake reads through the external stage -- normally the Gold zone."
}

variable "stage_prefixes" {
  type        = list(string)
  default     = ["gold/"]
  description = "Key prefixes Snowflake may read. Scoped rather than whole-bucket: an external stage that can read every prefix can read Bronze, which is where the un-redacted data is."

  validation {
    condition     = length(var.stage_prefixes) > 0
    error_message = "Name at least one prefix. An empty list would mean whole-bucket access, which is the thing this variable exists to prevent."
  }
}

variable "stage_bucket_kms_key_arn" {
  type        = string
  default     = ""
  description = "CMK on the stage bucket. Without kms:Decrypt Snowflake reads 403 on an SSE-KMS bucket, and the error surfaces in Snowflake as an unhelpful stage failure."
}

variable "snowflake_iam_user_arn" {
  type        = string
  default     = ""
  description = "STORAGE_AWS_IAM_USER_ARN from `DESC INTEGRATION` in Snowflake. Empty on the first apply -- that is expected, see the module header for the two-pass order."
}

variable "snowflake_external_id" {
  type        = string
  default     = ""
  description = "STORAGE_AWS_EXTERNAL_ID from `DESC INTEGRATION`. Not a secret (it is an anti-confused-deputy nonce, useless without the trust relationship) which is why it is a plain variable rather than a Secrets Manager reference."
}

variable "enable_snowpipe_queue" {
  type        = bool
  default     = true
  description = "Create the SQS queue and S3 notification for Snowpipe auto-ingest. Off for a stage that is only read on a schedule."
}

locals {
  # Before Snowflake tells us who it is, the role must trust NOBODY. A placeholder principal
  # would be a role that exists with an unconstrained trust policy, which is exactly the
  # window an attacker wants; the account's own root is the tightest no-op principal there is.
  handshake_complete = var.snowflake_iam_user_arn != "" && var.snowflake_external_id != ""
  trusted_principal = local.handshake_complete ? var.snowflake_iam_user_arn : (
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
  )
}

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [local.trusted_principal]
    }

    # SEC-05. Snowflake's AWS account is shared across its customers, so the principal alone
    # authenticates Snowflake-the-company, never your Snowflake account. The external id is
    # what ties this role to YOUR integration.
    dynamic "condition" {
      for_each = local.handshake_complete ? [var.snowflake_external_id] : []
      content {
        test     = "StringEquals"
        variable = "sts:ExternalId"
        values   = [condition.value]
      }
    }
  }
}

resource "aws_iam_role" "snowflake" {
  name               = "${var.name_prefix}-snowflake-storage"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags

  # Surfaces the half-configured state as a plan-time note rather than a runtime surprise in
  # Snowflake.
  description = local.handshake_complete ? "Snowflake external stage access" : "PENDING: rerun with snowflake_iam_user_arn and snowflake_external_id from DESC INTEGRATION"
}

data "aws_iam_policy_document" "stage" {
  statement {
    sid       = "ReadStagedObjects"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = [for p in var.stage_prefixes : "arn:aws:s3:::${var.stage_bucket}/${p}*"]
  }
  # ListBucket is bucket-level, so the prefix restriction has to be a condition rather than a
  # resource path -- without it Snowflake can enumerate every key in the bucket even though it
  # can only read the ones above.
  statement {
    sid       = "ListStagedPrefixes"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = ["arn:aws:s3:::${var.stage_bucket}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = [for p in var.stage_prefixes : "${p}*"]
    }
  }
  dynamic "statement" {
    for_each = var.stage_bucket_kms_key_arn == "" ? [] : [var.stage_bucket_kms_key_arn]
    content {
      sid       = "StageKey"
      actions   = ["kms:Decrypt", "kms:DescribeKey"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "stage" {
  name   = "${var.name_prefix}-snowflake-stage"
  role   = aws_iam_role.snowflake.id
  policy = data.aws_iam_policy_document.stage.json
}

# --- Snowpipe auto-ingest --------------------------------------------------------------

resource "aws_sqs_queue" "snowpipe" {
  count                     = var.enable_snowpipe_queue ? 1 : 0
  name                      = "${var.name_prefix}-snowpipe"
  sqs_managed_sse_enabled   = true
  message_retention_seconds = 345600
  # Snowpipe polls; a short visibility timeout re-delivers a notification Snowflake is still
  # working through and duplicates the load.
  visibility_timeout_seconds = 300
  tags                       = var.tags
}

# S3 must be allowed to publish, and only for THIS bucket -- an unconditioned s3.amazonaws.com
# grant lets any bucket in any account publish into this queue.
data "aws_iam_policy_document" "snowpipe" {
  count = var.enable_snowpipe_queue ? 1 : 0
  statement {
    sid     = "AllowThisBucketToNotify"
    actions = ["sqs:SendMessage"]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    resources = [aws_sqs_queue.snowpipe[0].arn]
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = ["arn:aws:s3:::${var.stage_bucket}"]
    }
  }
  dynamic "statement" {
    for_each = local.handshake_complete ? [var.snowflake_iam_user_arn] : []
    content {
      sid     = "AllowSnowflakeToConsume"
      actions = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
      principals {
        type        = "AWS"
        identifiers = [statement.value]
      }
      resources = [aws_sqs_queue.snowpipe[0].arn]
    }
  }
}

resource "aws_sqs_queue_policy" "snowpipe" {
  count     = var.enable_snowpipe_queue ? 1 : 0
  queue_url = aws_sqs_queue.snowpipe[0].id
  policy    = data.aws_iam_policy_document.snowpipe[0].json
}

output "storage_role_arn" {
  value       = aws_iam_role.snowflake.arn
  description = "STORAGE_AWS_ROLE_ARN for CREATE STORAGE INTEGRATION in Snowflake."
}

output "snowpipe_queue_arn" {
  value       = var.enable_snowpipe_queue ? aws_sqs_queue.snowpipe[0].arn : null
  description = "AWS_SQS_ARN for CREATE PIPE ... AUTO_INGEST = TRUE."
}

output "handshake_complete" {
  value       = local.handshake_complete
  description = "False means this role trusts only the account root and Snowflake cannot assume it yet. Order: apply once, CREATE STORAGE INTEGRATION in Snowflake with storage_role_arn, run DESC INTEGRATION, then re-apply with snowflake_iam_user_arn and snowflake_external_id."
}
