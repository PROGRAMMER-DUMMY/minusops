terraform {
  required_version = ">= 1.10.0" # S3-native state locking (use_lockfile) requires >= 1.10
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Environment = var.environment
      Project     = "AI-Data-Pipeline"
      ManagedBy   = "Terraform"
    }
  }
}
