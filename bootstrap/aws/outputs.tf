output "finops_readonly_role_arn" {
  description = "Assume this for the dashboard / FinOps agent (read-only)"
  value       = aws_iam_role.finops_readonly.arn
}

output "deploy_role_arn" {
  description = "Pass to plan_gate --role-arn (MFA-required to assume)"
  value       = aws_iam_role.deploy.arn
}

output "pipeline_boundary_arn" {
  description = "Permissions boundary every pipeline service role must attach"
  value       = aws_iam_policy.pipeline_boundary.arn
}
