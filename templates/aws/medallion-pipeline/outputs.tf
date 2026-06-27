output "bronze_bucket_name" {
  description = "Name of the S3 bucket for Bronze (Raw) layer"
  value       = aws_s3_bucket.bronze.id
}

output "silver_bucket_name" {
  description = "Name of the S3 bucket for Silver (Cleaned) layer"
  value       = aws_s3_bucket.silver.id
}

output "gold_bucket_name" {
  description = "Name of the S3 bucket for Gold (Aggregated) layer"
  value       = aws_s3_bucket.gold.id
}

output "glue_assets_bucket_name" {
  description = "Name of the S3 bucket storing Glue Scripts/assets"
  value       = aws_s3_bucket.glue_assets.id
}

output "glue_database_name" {
  description = "Glue Catalog Database Name"
  value       = aws_glue_catalog_database.medallion_db.name
}

output "step_function_arn" {
  description = "ARN of the Step Functions Orchestrator State Machine"
  value       = aws_sfn_state_machine.pipeline_orchestrator.arn
}

output "glue_bronze_to_silver_job_name" {
  description = "Name of the Bronze to Silver Glue job"
  value       = aws_glue_job.bronze_to_silver.name
}

output "glue_silver_to_gold_job_name" {
  description = "Name of the Silver to Gold Glue job"
  value       = aws_glue_job.silver_to_gold.name
}
