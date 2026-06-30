# Module: speed-layer-kinesis
# The streaming "speed layer" of a lambda/kappa architecture: a Kinesis Data Stream (KMS
# encrypted) plus an optional Managed Service for Apache Flink application for real-time
# processing. Pair with a batch layer (compute-glue-etl) for a full lambda architecture.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "shard_count" {
  type    = number
  default = 1
}

variable "retention_hours" {
  type    = number
  default = 24
}

variable "enable_flink" {
  type    = bool
  default = false
}

variable "flink_code_s3_bucket" {
  type    = string
  default = ""
}

variable "flink_code_s3_key" {
  type    = string
  default = ""
}

resource "aws_kinesis_stream" "this" {
  name             = "${var.name_prefix}-events"
  shard_count      = var.shard_count
  retention_period = var.retention_hours
  encryption_type  = "KMS"
  kms_key_id       = "alias/aws/kinesis"
  tags             = var.tags
}

data "aws_iam_policy_document" "flink_assume" {
  count = var.enable_flink ? 1 : 0
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["kinesisanalytics.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "flink" {
  count              = var.enable_flink ? 1 : 0
  name               = "${var.name_prefix}-flink-exec"
  assume_role_policy = data.aws_iam_policy_document.flink_assume[0].json
  tags               = var.tags
}

resource "aws_iam_role_policy" "flink" {
  count = var.enable_flink ? 1 : 0
  name  = "${var.name_prefix}-flink"
  role  = aws_iam_role.flink[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ReadStream"
      Effect   = "Allow"
      Action   = ["kinesis:GetRecords", "kinesis:GetShardIterator", "kinesis:DescribeStream", "kinesis:ListShards"]
      Resource = aws_kinesis_stream.this.arn
    }]
  })
}

resource "aws_kinesisanalyticsv2_application" "this" {
  count                  = var.enable_flink ? 1 : 0
  name                   = "${var.name_prefix}-flink"
  runtime_environment    = "FLINK-1_18"
  service_execution_role = aws_iam_role.flink[0].arn
  tags                   = var.tags

  application_configuration {
    application_code_configuration {
      code_content {
        s3_content_location {
          bucket_arn = "arn:aws:s3:::${var.flink_code_s3_bucket}"
          file_key   = var.flink_code_s3_key
        }
      }
      code_content_type = "ZIPFILE"
    }
  }
}

output "stream_arn" {
  value = aws_kinesis_stream.this.arn
}

output "stream_name" {
  value = aws_kinesis_stream.this.name
}
