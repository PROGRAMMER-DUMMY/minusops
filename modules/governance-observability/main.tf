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

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.notification_emails
    subscriber_sns_topic_arns  = [local.effective_topic_arn]
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
  alarm_actions       = [local.effective_topic_arn]
  dimensions          = { Currency = "USD" }
  tags                = var.tags
}

output "budget_name" {
  value = aws_budgets_budget.monthly.name
}

output "alerts_topic_arn" {
  value = local.effective_topic_arn
}
