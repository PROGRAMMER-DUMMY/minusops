# =============================================================
# Customer-managed KMS key for the Medallion data layers.
# Replaces SSE-S3 (AES256) with SSE-KMS using a CMK with annual rotation,
# satisfying the "encrypt at rest with a managed key" requirement.
# =============================================================

data "aws_caller_identity" "current" {}

resource "aws_kms_key" "pipeline" {
  description             = "CMK for Medallion S3 data layers (${var.environment})"
  enable_key_rotation     = true
  rotation_period_in_days = 365
  deletion_window_in_days = 14

  # Root retains administration; the Glue service role is granted usage via IAM (iam.tf).
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      }
    ]
  })
}

resource "aws_kms_alias" "pipeline" {
  name          = "alias/medallion-pipeline-${var.environment}"
  target_key_id = aws_kms_key.pipeline.key_id
}
