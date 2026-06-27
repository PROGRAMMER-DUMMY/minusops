variable "aws_region" {
  description = "AWS region to deploy the pipeline resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (e.g. dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "bucket_suffix" {
  description = "A unique suffix appended to S3 bucket names to ensure global uniqueness"
  type        = string
  default     = "ai-data-medallion-12345"
}

variable "glue_spark_version" {
  description = "Glue Spark version for ETL jobs"
  type        = string
  default     = "4.0"
}
