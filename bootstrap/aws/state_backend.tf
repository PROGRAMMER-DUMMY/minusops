# =============================================================================
# Remote state backend store — an S3 bucket holding every template's state.
# Uses S3-native locking (use_lockfile, Terraform >= 1.10); no DynamoDB needed.
#
# This is bootstrap infrastructure: created once with an admin/MFA session.
# The bootstrap's OWN state stays local (it is the thing that creates the backend).
# =============================================================================

resource "aws_s3_bucket" "tfstate" {
  bucket = "${lower(var.role_name_prefix)}-tfstate-${data.aws_caller_identity.current.account_id}"

  # State is irreplaceable — block accidental bucket deletion.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
