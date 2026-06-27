# Glue Catalog Database
resource "aws_glue_catalog_database" "medallion_db" {
  name        = "medallion_database_${var.environment}"
  description = "Glue Catalog database for Medallion architecture data"
}

# Upload ETL Scripts to Glue Assets S3 Bucket
resource "aws_s3_object" "bronze_to_silver_script" {
  bucket = aws_s3_bucket.glue_assets.id
  key    = "scripts/bronze_to_silver.py"
  source = "${path.module}/etl_scripts/bronze_to_silver.py"
  etag   = filemd5("${path.module}/etl_scripts/bronze_to_silver.py")
}

resource "aws_s3_object" "silver_to_gold_script" {
  bucket = aws_s3_bucket.glue_assets.id
  key    = "scripts/silver_to_gold.py"
  source = "${path.module}/etl_scripts/silver_to_gold.py"
  etag   = filemd5("${path.module}/etl_scripts/silver_to_gold.py")
}

# Glue Job: Bronze to Silver
resource "aws_glue_job" "bronze_to_silver" {
  name     = "bronze_to_silver_job_${var.environment}"
  role_arn = aws_iam_role.glue_role.arn

  glue_version      = var.glue_spark_version
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = 10 # 10 minutes timeout limit for safety

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_bucket.glue_assets.bucket}/${aws_s3_object.bronze_to_silver_script.key}"
    python_version  = "3"
  }

  default_arguments = {
    "--job-language"                     = "python"
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-metrics"                   = "true"
    "--BRONZE_BUCKET"                    = aws_s3_bucket.bronze.bucket
    "--SILVER_BUCKET"                    = aws_s3_bucket.silver.bucket
  }
}

# Glue Job: Silver to Gold
resource "aws_glue_job" "silver_to_gold" {
  name     = "silver_to_gold_job_${var.environment}"
  role_arn = aws_iam_role.glue_role.arn

  glue_version      = var.glue_spark_version
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = 10

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_bucket.glue_assets.bucket}/${aws_s3_object.silver_to_gold_script.key}"
    python_version  = "3"
  }

  default_arguments = {
    "--job-language"                     = "python"
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-metrics"                   = "true"
    "--SILVER_BUCKET"                    = aws_s3_bucket.silver.bucket
    "--GOLD_BUCKET"                      = aws_s3_bucket.gold.bucket
  }
}

# Glue Crawler for Gold S3 data (updates the Metadata Catalog for Athena queries)
resource "aws_glue_crawler" "gold_crawler" {
  database_name = aws_glue_catalog_database.medallion_db.name
  name          = "gold_crawler_${var.environment}"
  role          = aws_iam_role.glue_role.arn

  s3_target {
    path = "s3://${aws_s3_bucket.gold.bucket}/business-aggregations/"
  }

  schema_change_policy {
    delete_behavior = "LOG"
    update_behavior = "UPDATE_IN_DATABASE"
  }
}
