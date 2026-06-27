variable "role_name" {
  description = "Name of the IAM role to create"
  type        = string
}

variable "service_principal" {
  description = "The AWS Service Principal that will assume this role (e.g. glue.amazonaws.com)"
  type        = string
}

variable "s3_read_buckets" {
  description = "List of S3 Bucket ARNs this role is permitted to read from"
  type        = list(string)
  default     = []
}

variable "s3_write_buckets" {
  description = "List of S3 Bucket ARNs this role is permitted to write to"
  type        = list(string)
  default     = []
}

variable "additional_policies" {
  description = "List of additional policy ARNs to attach to the role"
  type        = list(string)
  default     = []
}
