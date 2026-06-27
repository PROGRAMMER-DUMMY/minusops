# Enable S3 to send events to Amazon EventBridge
resource "aws_s3_bucket_notification" "bronze_notifications" {
  bucket      = aws_s3_bucket.bronze.id
  eventbridge = true
}

# EventBridge Rule: Trigger Step Functions on S3 ObjectCreated under raw-data/
resource "aws_cloudwatch_event_rule" "s3_upload_trigger" {
  name        = "trigger-step-functions-on-bronze-upload-${var.environment}"
  description = "Triggers Step Functions state machine when a raw data file lands in the Bronze bucket"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = {
        name = [aws_s3_bucket.bronze.id]
      }
      object = {
        key = [{
          prefix = "raw-data/"
        }]
      }
    }
  })
}

# EventBridge Target linking the Event Rule to Step Functions with a Dead-Letter Queue (DLQ)
resource "aws_cloudwatch_event_target" "step_functions_target" {
  rule      = aws_cloudwatch_event_rule.s3_upload_trigger.name
  target_id = "TriggerMedallionStatePipeline"
  arn       = aws_sfn_state_machine.pipeline_orchestrator.arn
  role_arn  = aws_iam_role.eventbridge_role.arn

  dead_letter_config {
    arn = aws_sqs_queue.eventbridge_dlq.arn
  }
}
