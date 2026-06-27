variable "aws_region" {
  description = "Region the deploy role is permitted to operate in"
  type        = string
  default     = "us-east-1"
}

variable "trusted_principal" {
  description = "ARN allowed to assume the governance roles. Defaults to this account's root."
  type        = string
  default     = ""
}

variable "role_name_prefix" {
  description = "Prefix for the governance role/policy names"
  type        = string
  default     = "Minus"
}
