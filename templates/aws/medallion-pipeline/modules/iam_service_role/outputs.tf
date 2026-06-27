output "role_arn" {
  description = "ARN of the dynamically generated IAM Service Role"
  value       = aws_iam_role.role.arn
}

output "role_name" {
  description = "Name of the dynamically generated IAM Service Role"
  value       = aws_iam_role.role.name
}
