# ==========================================
# 1. AWS Glue Service Role
# ==========================================
resource "aws_iam_role" "glue_role" {
  name = "GlueDataPipelineServiceRole-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "glue.amazonaws.com"
        }
      }
    ]
  })
}

# Attach standard AWSGlueServiceRole policy for general catalog, database and logging permissions
resource "aws_iam_role_policy_attachment" "glue_service_attach" {
  role       = aws_iam_role.glue_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

# Custom policy for specific S3 buckets access (Bronze, Silver, Gold, Glue Assets)
resource "aws_iam_policy" "glue_s3_policy" {
  name        = "GlueS3AccessPolicy-${var.environment}"
  description = "Allows Glue to read/write specific pipeline S3 buckets"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.bronze.arn,
          "${aws_s3_bucket.bronze.arn}/*",
          aws_s3_bucket.silver.arn,
          "${aws_s3_bucket.silver.arn}/*",
          aws_s3_bucket.gold.arn,
          "${aws_s3_bucket.gold.arn}/*",
          aws_s3_bucket.glue_assets.arn,
          "${aws_s3_bucket.glue_assets.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "glue_s3_attach" {
  role       = aws_iam_role.glue_role.name
  policy_arn = aws_iam_policy.glue_s3_policy.arn
}


# ==========================================
# 2. AWS Step Functions IAM Role
# ==========================================
resource "aws_iam_role" "sfn_role" {
  name = "StepFunctionsDataPipelineRole-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_policy" "sfn_policy" {
  name        = "StepFunctionsGlueControlPolicy-${var.environment}"
  description = "Allows Step Functions to run Glue jobs, trigger crawlers, and send CloudWatch logs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "glue:StartJobRun",
          "glue:GetJobRun",
          "glue:GetJobRuns",
          "glue:BatchStopJobRun"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "glue:StartCrawler",
          "glue:GetCrawler"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutLogEvents",
          "logs:PutDestination",
          "logs:DescribeDestinations",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "sfn_policy_attach" {
  role       = aws_iam_role.sfn_role.name
  policy_arn = aws_iam_policy.sfn_policy.arn
}


# ==========================================
# 3. AWS EventBridge Target IAM Role
# ==========================================
resource "aws_iam_role" "eventbridge_role" {
  name = "EventBridgeTriggerStepFunctionsRole-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_policy" "eventbridge_policy" {
  name = "EventBridgeTriggerStepFunctionsPolicy-${var.environment}"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "states:StartExecution"
        ]
        Resource = [
          "arn:aws:states:*:*:stateMachine:MedallionDataPipeline-${var.environment}"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "eb_policy_attach" {
  role       = aws_iam_role.eventbridge_role.name
  policy_arn = aws_iam_policy.eventbridge_policy.arn
}
