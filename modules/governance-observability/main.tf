# Module: governance-observability
# Cross-cutting guardrails: a monthly AWS Budget with notifications and a CloudWatch metric
# alarm hook. Composed into most stacks so cost and health are governed from day one.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "monthly_budget_usd" {
  type    = number
  default = 100
}

variable "alarm_sns_topic_arn" {
  type        = string
  default     = ""
  description = "Optional external SNS topic. If empty, this module creates one for alerts."
}

variable "notification_emails" {
  type    = list(string)
  default = []
}

# Failure/spend notification target (WA Analytics Lens BP 6.3 — notify stakeholders on
# job failures / threshold breaches). Created here so the stack has an alerting channel by
# default; an external topic can be supplied via alarm_sns_topic_arn to override.
# --- SIEM audit trail (MINUS-131) -------------------------------------------------------
# Opt-in: a CloudTrail with S3 data events logs EVERY object read and write in the lake, which
# on a busy pipeline is both the point (SecOps can answer "who read the PII") and a real,
# volume-proportional bill. Enabling it silently for every run would be a cost surprise, so it
# is off by default and the operator states it.

variable "enable_siem_trail" {
  type        = bool
  default     = false
  description = "Provision a CloudTrail capturing S3 object-level data events for the lake buckets. Off by default: data-event volume is billed per event."
}

variable "siem_data_bucket_arns" {
  type        = list(string)
  default     = []
  description = "Bucket ARNs whose object-level access is audited. Empty with enable_siem_trail = true audits nothing, which is why the trail is scoped by count on both."
}

variable "siem_kms_key_arn" {
  type        = string
  default     = ""
  description = "CMK encrypting the audit log bucket. Empty falls back to SSE-S3; the trail itself is still immutable via Object Lock."
}

variable "siem_retention_days" {
  type        = number
  default     = 365
  description = "Days audit logs are retained before expiry. Object Lock retention is set to the same window."
}

locals {
  siem_enabled = var.enable_siem_trail && length(var.siem_data_bucket_arns) > 0
}

data "aws_caller_identity" "siem" {}

resource "aws_s3_bucket" "audit" {
  count = local.siem_enabled ? 1 : 0
  # Same account-id + prefix namespacing the lake buckets use.
  bucket = "${var.name_prefix}-audit-${data.aws_caller_identity.siem.account_id}"
  # Never force_destroy, in any environment: an audit trail an operator can delete by
  # re-running destroy is not an audit trail.
  force_destroy       = false
  object_lock_enabled = true
  tags                = var.tags
}

resource "aws_s3_bucket_public_access_block" "audit" {
  count                   = local.siem_enabled ? 1 : 0
  bucket                  = aws_s3_bucket.audit[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Object Lock requires versioning; it is also what makes GOVERNANCE mode meaningful.
resource "aws_s3_bucket_versioning" "audit" {
  count  = local.siem_enabled ? 1 : 0
  bucket = aws_s3_bucket.audit[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

# GOVERNANCE, not COMPLIANCE: COMPLIANCE mode cannot be shortened or removed by anyone
# including the root account for the full retention window, which has stranded more teams
# than it has caught. GOVERNANCE blocks ordinary deletion but leaves a documented, audited
# break-glass path.
resource "aws_s3_bucket_object_lock_configuration" "audit" {
  count      = local.siem_enabled ? 1 : 0
  bucket     = aws_s3_bucket.audit[0].id
  depends_on = [aws_s3_bucket_versioning.audit]
  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = var.siem_retention_days
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audit" {
  count  = local.siem_enabled ? 1 : 0
  bucket = aws_s3_bucket.audit[0].id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = var.siem_kms_key_arn == "" ? null : var.siem_kms_key_arn
      sse_algorithm     = var.siem_kms_key_arn == "" ? "AES256" : "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "audit" {
  count  = local.siem_enabled ? 1 : 0
  bucket = aws_s3_bucket.audit[0].id
  rule {
    id     = "expire_audit_logs"
    status = "Enabled"
    filter {}
    expiration {
      days = var.siem_retention_days
    }
  }
}

data "aws_iam_policy_document" "audit_bucket" {
  count = local.siem_enabled ? 1 : 0
  statement {
    sid     = "AWSCloudTrailAclCheck"
    actions = ["s3:GetBucketAcl"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    resources = [aws_s3_bucket.audit[0].arn]
  }
  statement {
    sid     = "AWSCloudTrailWrite"
    actions = ["s3:PutObject"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    resources = ["${aws_s3_bucket.audit[0].arn}/AWSLogs/${data.aws_caller_identity.siem.account_id}/*"]
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
  }
}

resource "aws_s3_bucket_policy" "audit" {
  count  = local.siem_enabled ? 1 : 0
  bucket = aws_s3_bucket.audit[0].id
  policy = data.aws_iam_policy_document.audit_bucket[0].json
}

resource "aws_cloudtrail" "siem" {
  count                         = local.siem_enabled ? 1 : 0
  name                          = "${var.name_prefix}-siem"
  s3_bucket_name                = aws_s3_bucket.audit[0].id
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true
  kms_key_id                    = var.siem_kms_key_arn == "" ? null : var.siem_kms_key_arn
  tags                          = var.tags
  depends_on                    = [aws_s3_bucket_policy.audit]

  # Data events are the point of this trail. Management events alone tell you a bucket was
  # created; they do not tell you who read the objects in it.
  advanced_event_selector {
    name = "S3 object-level access on the lake buckets"
    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }
    field_selector {
      field  = "resources.type"
      equals = ["AWS::S3::Object"]
    }
    field_selector {
      field       = "resources.ARN"
      starts_with = [for arn in var.siem_data_bucket_arns : "${arn}/"]
    }
  }
}

output "siem_audit_bucket" {
  value = local.siem_enabled ? aws_s3_bucket.audit[0].id : ""
}

output "siem_trail_arn" {
  value = local.siem_enabled ? aws_cloudtrail.siem[0].arn : ""
}

resource "aws_sns_topic" "alerts" {
  count = var.alarm_sns_topic_arn == "" ? 1 : 0
  name  = "${var.name_prefix}-alerts"
  tags  = var.tags
}

resource "aws_sns_topic_subscription" "email" {
  for_each  = var.alarm_sns_topic_arn == "" ? toset(var.notification_emails) : toset([])
  topic_arn = aws_sns_topic.alerts[0].arn
  protocol  = "email"
  endpoint  = each.value
}

locals {
  effective_topic_arn = var.alarm_sns_topic_arn != "" ? var.alarm_sns_topic_arn : aws_sns_topic.alerts[0].arn
}

# --- 3-tier alert routing (MINUS-117) ---------------------------------------------------
# One inbox for everything is why nobody reads the inbox. Three topics with three severities
# and three audiences: a pipeline crash pages someone, a bad batch of rows does not.
#
#   Tier 1 (P0/P1) pipeline failure  -> on-call. Slack webhook / PagerDuty integration URL.
#   Tier 2 (P2)    data-quality fail -> the data team's channel. Rows are quarantined, the
#                                        pipeline keeps running, nobody is woken up.
#   Tier 3 (P3)    budget threshold  -> the budget owner, by email.
#
# `alerts` above stays the Tier 1 topic so every existing caller (compute-glue-etl's failure
# rule, the spend alarm) keeps working unchanged; the two new topics are additive.

variable "data_quality_emails" {
  type        = list(string)
  default     = []
  description = "Tier 2 recipients: data-quality failures. Separate from the on-call list on purpose -- a failed expectation is not a page."
}

variable "budget_owner_emails" {
  type        = list(string)
  default     = []
  description = "Tier 3 recipients: budget thresholds. Falls back to notification_emails when empty."
}

variable "oncall_webhook_url" {
  type        = string
  default     = ""
  description = "Tier 1 HTTPS endpoint -- a Slack incoming webhook or a PagerDuty integration URL. SNS delivers to it directly; no Lambda shim. Empty means email-only paging."
  sensitive   = true
}

variable "data_quality_webhook_url" {
  type        = string
  default     = ""
  description = "Tier 2 HTTPS endpoint, e.g. a #data-quality Slack webhook."
  sensitive   = true
}

resource "aws_sns_topic" "data_quality" {
  name = "${var.name_prefix}-data-quality"
  tags = merge(var.tags, { alert_tier = "2" })
}

resource "aws_sns_topic" "budget" {
  name = "${var.name_prefix}-budget"
  tags = merge(var.tags, { alert_tier = "3" })
}

# https subscriptions to a chat webhook: SNS posts the raw message, which Slack and PagerDuty
# both accept. Confirmation is automatic for https endpoints that return 200.
resource "aws_sns_topic_subscription" "oncall_webhook" {
  count     = var.oncall_webhook_url == "" ? 0 : 1
  topic_arn = local.effective_topic_arn
  protocol  = "https"
  endpoint  = var.oncall_webhook_url
}

resource "aws_sns_topic_subscription" "data_quality_webhook" {
  count     = var.data_quality_webhook_url == "" ? 0 : 1
  topic_arn = aws_sns_topic.data_quality.arn
  protocol  = "https"
  endpoint  = var.data_quality_webhook_url
}

resource "aws_sns_topic_subscription" "data_quality_email" {
  for_each  = toset(var.data_quality_emails)
  topic_arn = aws_sns_topic.data_quality.arn
  protocol  = "email"
  endpoint  = each.value
}

resource "aws_sns_topic_subscription" "budget_email" {
  for_each  = toset(length(var.budget_owner_emails) > 0 ? var.budget_owner_emails : var.notification_emails)
  topic_arn = aws_sns_topic.budget.arn
  protocol  = "email"
  endpoint  = each.value
}

output "data_quality_topic_arn" {
  value = aws_sns_topic.data_quality.arn
}

output "budget_topic_arn" {
  value = aws_sns_topic.budget.arn
}

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator = "GREATER_THAN"
    threshold           = 80
    threshold_type      = "PERCENTAGE"
    notification_type   = "ACTUAL"
    # Tier 3: the budget owner, not the on-call topic. A spend threshold is not an incident.
    subscriber_email_addresses = length(var.budget_owner_emails) > 0 ? var.budget_owner_emails : var.notification_emails
    subscriber_sns_topic_arns  = [aws_sns_topic.budget.arn]
  }
}

resource "aws_cloudwatch_metric_alarm" "spend" {
  alarm_name          = "${var.name_prefix}-estimated-charges"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "EstimatedCharges"
  namespace           = "AWS/Billing"
  period              = 21600
  statistic           = "Maximum"
  threshold           = var.monthly_budget_usd
  alarm_description   = "Estimated charges exceeded the monthly budget for ${var.name_prefix}."
  alarm_actions       = [aws_sns_topic.budget.arn]
  dimensions          = { Currency = "USD" }
  tags                = var.tags
}

output "budget_name" {
  value = aws_budgets_budget.monthly.name
}

output "alerts_topic_arn" {
  value = local.effective_topic_arn
}
