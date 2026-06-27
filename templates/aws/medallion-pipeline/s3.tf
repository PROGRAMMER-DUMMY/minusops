# S3 Bucket for Bronze Layer (Raw Data Ingestion)
resource "aws_s3_bucket" "bronze" {
  bucket        = "bronze-${var.bucket_suffix}"
  force_destroy = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "bronze_sse" {
  bucket = aws_s3_bucket.bronze.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.pipeline.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "bronze_pab" {
  bucket = aws_s3_bucket.bronze.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "bronze_lifecycle" {
  bucket = aws_s3_bucket.bronze.id

  rule {
    id     = "archive-old-raw-data"
    status = "Enabled"

    filter {} # applies to all objects in the bucket

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    expiration {
      days = 180 # Automatically clean up raw data after 6 months to cut storage costs
    }
  }
}


# S3 Bucket for Silver Layer (Cleaned & Structured Data in Parquet)
resource "aws_s3_bucket" "silver" {
  bucket        = "silver-${var.bucket_suffix}"
  force_destroy = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "silver_sse" {
  bucket = aws_s3_bucket.silver.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.pipeline.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "silver_pab" {
  bucket = aws_s3_bucket.silver.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "silver_lifecycle" {
  bucket = aws_s3_bucket.silver.id

  rule {
    id     = "archive-silver-parquet"
    status = "Enabled"

    filter {} # applies to all objects in the bucket

    transition {
      days          = 60
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 120
      storage_class = "GLACIER"
    }
  }
}


# S3 Bucket for Gold Layer (Aggregated Business-Level Data)
resource "aws_s3_bucket" "gold" {
  bucket        = "gold-${var.bucket_suffix}"
  force_destroy = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "gold_sse" {
  bucket = aws_s3_bucket.gold.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.pipeline.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "gold_pab" {
  bucket = aws_s3_bucket.gold.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}


# S3 Bucket for Glue Scripts & Temporary Space
resource "aws_s3_bucket" "glue_assets" {
  bucket        = "glue-assets-${var.bucket_suffix}"
  force_destroy = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "glue_assets_sse" {
  bucket = aws_s3_bucket.glue_assets.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.pipeline.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "glue_assets_pab" {
  bucket = aws_s3_bucket.glue_assets.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
