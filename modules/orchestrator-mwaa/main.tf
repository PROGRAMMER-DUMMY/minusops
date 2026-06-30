# Module: orchestrator-mwaa
# Managed Airflow (Amazon MWAA) for companies that orchestrate with Airflow DAGs instead of
# Step Functions. Creates the environment, its execution role, and scoped DAG-bucket access.
# Networking (subnets, security groups) is supplied by the caller — MWAA runs inside your VPC.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "dag_s3_bucket_arn" {
  type        = string
  description = "ARN of the S3 bucket holding DAGs / requirements / plugins."
}

variable "subnet_ids" {
  type        = list(string)
  description = "Two private subnet IDs in your VPC for the MWAA environment."
}

variable "security_group_ids" {
  type = list(string)
}

variable "airflow_version" {
  type    = string
  default = "2.8.1"
}

variable "environment_class" {
  type    = string
  default = "mw1.small"
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["airflow.amazonaws.com", "airflow-env.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "mwaa" {
  name               = "${var.name_prefix}-mwaa-exec"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "mwaa" {
  statement {
    sid       = "DagBucket"
    actions   = ["s3:GetObject", "s3:GetBucket*", "s3:List*"]
    resources = [var.dag_s3_bucket_arn, "${var.dag_s3_bucket_arn}/*"]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:CreateLogGroup", "logs:PutLogEvents", "logs:GetLogEvents", "logs:GetLogRecord", "logs:GetLogGroupFields", "logs:GetQueryResults", "logs:DescribeLogGroups"]
    resources = ["arn:aws:logs:*:*:log-group:airflow-${var.name_prefix}-*"]
  }
}

resource "aws_iam_role_policy" "mwaa" {
  name   = "${var.name_prefix}-mwaa"
  role   = aws_iam_role.mwaa.id
  policy = data.aws_iam_policy_document.mwaa.json
}

resource "aws_mwaa_environment" "this" {
  name               = "${var.name_prefix}-airflow"
  airflow_version    = var.airflow_version
  environment_class  = var.environment_class
  execution_role_arn = aws_iam_role.mwaa.arn
  source_bucket_arn  = var.dag_s3_bucket_arn
  dag_s3_path        = "dags"

  network_configuration {
    security_group_ids = var.security_group_ids
    subnet_ids         = var.subnet_ids
  }

  logging_configuration {
    dag_processing_logs {
      enabled   = true
      log_level = "INFO"
    }
    task_logs {
      enabled   = true
      log_level = "INFO"
    }
  }

  tags = var.tags
}

output "airflow_environment" {
  value = aws_mwaa_environment.this.name
}

output "execution_role_arn" {
  value = aws_iam_role.mwaa.arn
}
