# Module: ingest-firehose
# Near-real-time ingestion into the bronze zone via Kinesis Data Firehose. Buffering is
# deliberately large (micro-batching): at high velocity, per-event delivery both costs
# more and manufactures the small-files problem — 64 MB / 300 s buffers land objects
# near the ~128 MB scan-friendly target.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "destination_bucket_arn" {
  type        = string
  description = "ARN of the landing (bronze) bucket."
}

variable "buffering_size_mb" {
  type    = number
  default = 64
}

variable "buffering_interval_seconds" {
  type    = number
  default = 300
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "firehose" {
  name               = "${var.name_prefix}-firehose"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "firehose" {
  statement {
    sid = "Deliver"
    actions = [
      "s3:AbortMultipartUpload", "s3:GetBucketLocation", "s3:GetObject",
      "s3:ListBucket", "s3:ListBucketMultipartUploads", "s3:PutObject",
    ]
    resources = [var.destination_bucket_arn, "${var.destination_bucket_arn}/*"]
  }
}

resource "aws_iam_role_policy" "firehose" {
  name   = "${var.name_prefix}-firehose"
  role   = aws_iam_role.firehose.id
  policy = data.aws_iam_policy_document.firehose.json
}

resource "aws_kinesis_firehose_delivery_stream" "this" {
  name        = "${var.name_prefix}-ingest"
  destination = "extended_s3"
  tags        = var.tags

  extended_s3_configuration {
    role_arn   = aws_iam_role.firehose.arn
    bucket_arn = var.destination_bucket_arn
    prefix     = "streaming/ingest_date=!{timestamp:yyyy-MM-dd}/"
    # Failed records are kept, never dropped silently.
    error_output_prefix = "streaming-errors/!{firehose:error-output-type}/"

    buffering_size     = var.buffering_size_mb
    buffering_interval = var.buffering_interval_seconds
    compression_format = "GZIP"
  }
}

output "delivery_stream_name" {
  value = aws_kinesis_firehose_delivery_stream.this.name
}

output "delivery_stream_arn" {
  value = aws_kinesis_firehose_delivery_stream.this.arn
}
