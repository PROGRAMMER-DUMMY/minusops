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
  description = "Optional SNS topic for budget + alarm notifications."
}

variable "notification_emails" {
  type    = list(string)
  default = []
}

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = length(var.notification_emails) > 0 ? [1] : []
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = 80
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = var.notification_emails
    }
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
  alarm_actions       = var.alarm_sns_topic_arn == "" ? [] : [var.alarm_sns_topic_arn]
  dimensions          = { Currency = "USD" }
  tags                = var.tags
}

output "budget_name" {
  value = aws_budgets_budget.monthly.name
}
