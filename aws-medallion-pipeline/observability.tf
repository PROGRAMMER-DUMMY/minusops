# ==========================================
# 1. Alerting Infrastructure (SNS Topic)
# ==========================================
resource "aws_sns_topic" "pipeline_alerts" {
  name = "pipeline-monitoring-alerts-${var.environment}"
}

resource "aws_sns_topic_subscription" "email_subscription" {
  topic_arn = aws_sns_topic.pipeline_alerts.arn
  protocol  = "email"
  endpoint  = "devops-alerts@example.com" # Replace with actual team distribution list
}


# ==========================================
# 2. EventBridge Dead-Letter Queue (DLQ)
# ==========================================
resource "aws_sqs_queue" "eventbridge_dlq" {
  name                      = "eventbridge-pipeline-dlq-${var.environment}"
  message_retention_seconds = 1209600 # Retain failed event triggers for 14 days
}

resource "aws_sqs_queue_policy" "dlq_policy" {
  queue_url = aws_sqs_queue.eventbridge_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.eventbridge_dlq.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = "arn:aws:events:${var.aws_region}:*:rule/trigger-step-functions-on-bronze-upload-${var.environment}"
          }
        }
      }
    ]
  })
}


# ==========================================
# 3. CloudWatch Log Group for Step Functions
# ==========================================
resource "aws_cloudwatch_log_group" "sfn_log_group" {
  name              = "/aws/vendedlogs/states/MedallionDataPipeline-${var.environment}"
  retention_in_days = 14
}


# ==========================================
# 4. Observability Metric Alarms
# ==========================================

# Alarm 1: Step Functions Execution Failures
resource "aws_cloudwatch_metric_alarm" "pipeline_failure_alarm" {
  alarm_name          = "step-functions-pipeline-failure-${var.environment}"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "ExecutionsFailed"
  namespace           = "AWS/States"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  alarm_description   = "Triggered when the Medallion Data Pipeline Step Functions execution fails."
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.pipeline_orchestrator.arn
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
}

# Alarm 2: AWS Glue Job Failures (Bronze-to-Silver Job)
resource "aws_cloudwatch_metric_alarm" "glue_bronze_to_silver_failure" {
  alarm_name          = "glue-job-bronze-to-silver-failure-${var.environment}"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "GlueJobFailedRuns"
  namespace           = "AWS/Glue"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  alarm_description   = "Triggered when the Bronze-to-Silver Glue Job fails."
  treat_missing_data  = "notBreaching"

  dimensions = {
    JobName = aws_glue_job.bronze_to_silver.name
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
}

# Alarm 3: AWS Glue Job Failures (Silver-to-Gold Job)
resource "aws_cloudwatch_metric_alarm" "glue_silver_to_gold_failure" {
  alarm_name          = "glue-job-silver-to-gold-failure-${var.environment}"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "GlueJobFailedRuns"
  namespace           = "AWS/Glue"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  alarm_description   = "Triggered when the Silver-to-Gold Glue Job fails."
  treat_missing_data  = "notBreaching"

  dimensions = {
    JobName = aws_glue_job.silver_to_gold.name
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
}
