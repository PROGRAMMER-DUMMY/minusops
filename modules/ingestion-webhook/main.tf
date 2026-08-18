# Module: ingestion-webhook
# An HTTPS endpoint that accepts real-time event pushes (Stripe, GitHub, a partner's system)
# and buffers them in SQS. Use when the upstream pushes to you and cannot be polled.
#
# API Gateway writes to SQS directly via an AWS service integration -- no Lambda in the path.
# A Lambda here would be a function whose entire body is `sqs.send_message`, plus a runtime to
# patch, a cold start on every burst, and a per-invocation bill.
#
# HMAC signature verification is deliberately NOT done here. Every provider signs differently
# (Stripe's timestamped v1 scheme, GitHub's X-Hub-Signature-256, plain HMAC-SHA256), and a
# generic verifier would be wrong for all of them. The shared secret is provisioned and the
# consumer verifies before trusting the payload -- see the output note.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "route_key" {
  type        = string
  default     = "POST /events"
  description = "API route. POST-only: a webhook receiver that answers GET is a scanner magnet."
}

variable "message_retention_seconds" {
  type        = number
  default     = 345600
  description = "How long an unconsumed event survives (default 4 days). The window a consumer outage can last without data loss."
}

variable "visibility_timeout_seconds" {
  type        = number
  default     = 300
  description = "Must exceed the consumer's processing time or the same event is delivered twice while the first attempt is still running."
}

variable "throttling_burst_limit" {
  type        = number
  default     = 100
  description = "A public endpoint with no throttle is a billing denial-of-service waiting to happen."
}

variable "throttling_rate_limit" {
  type    = number
  default = 50
}

# Failed messages go here instead of being retried forever. Without a DLQ a single poison
# payload blocks the queue head until it ages out.
resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name_prefix}-webhook-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum: a poison message is evidence
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}

resource "aws_sqs_queue" "events" {
  name                       = "${var.name_prefix}-webhook"
  message_retention_seconds  = var.message_retention_seconds
  visibility_timeout_seconds = var.visibility_timeout_seconds
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 5
  })
  tags = var.tags
}

# The shared secret the sender signs with. Created empty: Terraform generates the container,
# a human puts the value in. A secret with a Terraform-authored value is a secret in state.
resource "aws_secretsmanager_secret" "hmac" {
  name        = "${var.name_prefix}-webhook-hmac"
  description = "Shared secret for verifying inbound webhook signatures. Set the value out of band; do not put it in Terraform."
  tags        = var.tags
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["apigateway.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apigw" {
  name               = "${var.name_prefix}-webhook-apigw"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "apigw" {
  statement {
    sid       = "SendToQueue"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.events.arn]
  }
}

resource "aws_iam_role_policy" "apigw" {
  name   = "${var.name_prefix}-webhook-apigw"
  role   = aws_iam_role.apigw.id
  policy = data.aws_iam_policy_document.apigw.json
}

resource "aws_apigatewayv2_api" "this" {
  name          = "${var.name_prefix}-webhook"
  protocol_type = "HTTP"
  tags          = var.tags
}

resource "aws_apigatewayv2_integration" "sqs" {
  api_id              = aws_apigatewayv2_api.this.id
  integration_type    = "AWS_PROXY"
  integration_subtype = "SQS-SendMessage"
  credentials_arn     = aws_iam_role.apigw.arn
  request_parameters = {
    QueueUrl = aws_sqs_queue.events.url
    # The whole body is enqueued verbatim. The consumer needs the exact bytes the sender
    # signed -- re-serializing the JSON here would break every signature check downstream.
    MessageBody = "$request.body"
  }
}

resource "aws_apigatewayv2_route" "this" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = var.route_key
  target    = "integrations/${aws_apigatewayv2_integration.sqs.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true
  tags        = var.tags

  default_route_settings {
    throttling_burst_limit = var.throttling_burst_limit
    throttling_rate_limit  = var.throttling_rate_limit
  }
}

output "webhook_url" {
  value       = "${aws_apigatewayv2_api.this.api_endpoint}${trimprefix(var.route_key, "POST ")}"
  description = "Give this to the sender. The consumer MUST verify the signature against the hmac secret before trusting a payload -- this endpoint is unauthenticated by design, because the sender's signature is the authentication."
}

output "queue_url" {
  value = aws_sqs_queue.events.url
}

output "queue_arn" {
  value = aws_sqs_queue.events.arn
}

output "hmac_secret_arn" {
  value = aws_secretsmanager_secret.hmac.arn
}
