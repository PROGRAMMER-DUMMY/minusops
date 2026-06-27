# =============================================================================
# Governance bootstrap — the framework's own IAM roles.
#
# Run ONCE per account with an admin/MFA session (chicken-and-egg: you cannot use
# the deploy role to create the deploy role). After this, plan_gate assumes the
# deploy role (MFA-gated) and the dashboard/FinOps agent uses the read-only role.
# =============================================================================

data "aws_caller_identity" "current" {}

locals {
  trusted = var.trusted_principal != "" ? var.trusted_principal : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
}

# ---------------------------------------------------------------------------
# Permissions boundary — caps any service role the deploy role creates so it
# can never grant itself IAM/org privileges.
# ---------------------------------------------------------------------------
resource "aws_iam_policy" "pipeline_boundary" {
  name        = "PipelineBoundary"
  description = "Permissions boundary for pipeline service roles — no IAM/org escalation."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "BoundaryAllowPipelineServices"
        Effect   = "Allow"
        Action   = ["s3:*", "glue:*", "states:*", "events:*", "sns:*", "sqs:*", "logs:*", "cloudwatch:*"]
        Resource = "*"
      },
      {
        Sid      = "BoundaryDenyEscalation"
        Effect   = "Deny"
        Action   = ["iam:*", "organizations:*", "account:*", "sts:AssumeRole"]
        Resource = "*"
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# FinOps read-only role — the dashboard and cost agent assume this. Read-only.
# ---------------------------------------------------------------------------
resource "aws_iam_policy" "finops_readonly" {
  name        = "${var.role_name_prefix}FinOpsReadOnly"
  description = "Read-only access for the FinOps dashboard and cost agent."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "FinOpsReadOnly"
      Effect = "Allow"
      Action = [
        "sts:GetCallerIdentity",
        "ce:GetCostAndUsage", "ce:GetAnomalies", "ce:GetAnomalyMonitors", "ce:GetAnomalySubscriptions",
        "cloudtrail:LookupEvents",
        "tag:GetResources",
        "pricing:GetProducts", "pricing:DescribeServices",
        "glue:GetJobRun", "glue:GetJobRuns",
        "s3:ListBucket", "s3:GetBucketLocation"
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role" "finops_readonly" {
  name = "${var.role_name_prefix}FinOpsReadOnly"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = local.trusted }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "finops_readonly" {
  role       = aws_iam_role.finops_readonly.name
  policy_arn = aws_iam_policy.finops_readonly.arn
}

# ---------------------------------------------------------------------------
# Deploy role — what plan_gate assumes to run `terraform apply`.
# MFA-required to assume; can create pipeline roles ONLY with the boundary
# attached; explicit Deny blocks user/key/org escalation.
# ---------------------------------------------------------------------------
resource "aws_iam_policy" "deploy" {
  name        = "${var.role_name_prefix}Deploy"
  description = "Provisioning permissions for the pipeline templates, boundary-constrained."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PipelineServices"
        Effect    = "Allow"
        Action    = ["s3:*", "glue:*", "states:*", "events:*", "sns:*", "sqs:*", "logs:*", "cloudwatch:*", "budgets:*", "ce:*"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } }
      },
      {
        Sid    = "IamButBoundaryRequired"
        Effect = "Allow"
        Action = [
          "iam:CreateRole", "iam:CreatePolicy", "iam:AttachRolePolicy", "iam:PutRolePolicy",
          "iam:PassRole", "iam:TagRole", "iam:GetRole", "iam:GetPolicy", "iam:ListRolePolicies",
          "iam:DeleteRole", "iam:DetachRolePolicy", "iam:DeletePolicy"
        ]
        Resource  = "arn:aws:iam::*:role/*DataPipeline*"
        Condition = { StringEquals = { "iam:PermissionsBoundary" = aws_iam_policy.pipeline_boundary.arn } }
      },
      {
        Sid      = "DenyEscalation"
        Effect   = "Deny"
        Action   = ["iam:*User*", "iam:CreateAccessKey", "iam:*LoginProfile", "iam:AttachUserPolicy", "organizations:*", "account:*"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role" "deploy" {
  name = "${var.role_name_prefix}Deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = local.trusted }
      Action    = "sts:AssumeRole"
      Condition = { Bool = { "aws:MultiFactorAuthPresent" = "true" } }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "deploy" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy.arn
}
