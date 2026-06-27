# 1. Trust Policy (Assume Role Policy)
data "aws_iam_policy_document" "assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    effect  = "Allow"

    principals {
      type        = "Service"
      identifiers = [var.service_principal]
    }
  }
}

# 2. IAM Role
resource "aws_iam_role" "role" {
  name               = var.role_name
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

# 3. Dynamic Least-Privilege S3 Access Policy
data "aws_iam_policy_document" "s3_access" {
  # Conditional Read permissions
  dynamic "statement" {
    for_each = length(var.s3_read_buckets) > 0 ? [1] : []
    content {
      actions   = ["s3:GetObject", "s3:ListBucket"]
      resources = concat(var.s3_read_buckets, [for arn in var.s3_read_buckets : "${arn}/*"])
      effect    = "Allow"
    }
  }

  # Conditional Write permissions
  dynamic "statement" {
    for_each = length(var.s3_write_buckets) > 0 ? [1] : []
    content {
      actions   = ["s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
      resources = concat(var.s3_write_buckets, [for arn in var.s3_write_buckets : "${arn}/*"])
      effect    = "Allow"
    }
  }
}

resource "aws_iam_policy" "s3_policy" {
  count  = (length(var.s3_read_buckets) + length(var.s3_write_buckets)) > 0 ? 1 : 0
  name   = "${var.role_name}-s3-policy"
  policy = data.aws_iam_policy_document.s3_access.json
}

resource "aws_iam_role_policy_attachment" "s3_attach" {
  count      = (length(var.s3_read_buckets) + length(var.s3_write_buckets)) > 0 ? 1 : 0
  role       = aws_iam_role.role.name
  policy_arn = aws_iam_policy.s3_policy[0].arn
}

# 4. Attach additional policies (e.g. AWSGlueServiceRole)
resource "aws_iam_role_policy_attachment" "additional" {
  for_each   = toset(var.additional_policies)
  role       = aws_iam_role.role.name
  policy_arn = each.value
}
