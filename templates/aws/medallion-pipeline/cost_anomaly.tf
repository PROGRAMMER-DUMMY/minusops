# =============================================================
# AWS Cost Anomaly Detection Service (Native ML-Based Cost Guard)
# =============================================================

# 1. Cost Monitor: Monitors all AWS services for cost anomalies
resource "aws_ce_anomaly_monitor" "pipeline_cost_monitor" {
  name              = "PipelineAnomalyCostMonitor-${var.environment}"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE" # Tracks anomalies per individual service (Glue, S3, Redshift)
}

# 2. Anomaly Subscription: Sends alerts to SNS/Email when cost spikes occur
resource "aws_ce_anomaly_subscription" "pipeline_cost_subscription" {
  name      = "PipelineCostAnomalySubscription-${var.environment}"
  frequency = "IMMEDIATE"

  monitor_arn_list = [
    aws_ce_anomaly_monitor.pipeline_cost_monitor.arn
  ]

  # Trigger when a single anomaly's absolute cost impact is >= $10 USD.
  # (Replaces the removed `threshold` argument.)
  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = ["10"]
    }
  }

  subscriber {
    address = "devops-alerts@example.com"
    type    = "EMAIL"
  }

  # Also route to the pipeline SNS topic to trigger Slack/PagerDuty webhooks
  subscriber {
    address = aws_sns_topic.pipeline_alerts.arn
    type    = "SNS"
  }
}
