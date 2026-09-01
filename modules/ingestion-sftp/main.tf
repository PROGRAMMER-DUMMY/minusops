# Module: ingestion-sftp
# A managed SFTP endpoint (AWS Transfer Family) that drops partner files straight into the
# Bronze zone. Use when an external party outside your network sends files on a schedule and
# cannot call an API or assume a role -- the case VPC peering and PrivateLink do not cover.
#
# Auth is SSH public keys only. No passwords: Transfer Family's password auth requires a
# custom identity provider, which is a Lambda holding partner credentials -- more attack
# surface than the problem justifies.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "target_bucket" {
  type        = string
  description = "Bronze bucket partner uploads land in."
}

variable "target_bucket_kms_key_arn" {
  type        = string
  default     = ""
  description = "CMK encrypting the target bucket. Without kms:GenerateDataKey the upload fails with 403 on an SSE-KMS bucket."
}

variable "users" {
  type        = map(string)
  default     = {}
  description = "username => SSH public key (the full 'ssh-rsa AAAA...' line). Public keys only -- a public key in state is not a secret. Each user is confined to their own prefix."
}

variable "security_policy_name" {
  type        = string
  default     = "TransferSecurityPolicy-2024-01"
  description = "Transfer Family cipher policy. Pinned rather than left at AWS's default so a policy change is a reviewed diff, not a silent downgrade."
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["transfer.amazonaws.com"]
    }
  }
}

resource "aws_transfer_server" "this" {
  identity_provider_type = "SERVICE_MANAGED"
  protocols              = ["SFTP"]
  domain                 = "S3"
  endpoint_type          = "PUBLIC"
  security_policy_name   = var.security_policy_name
  tags                   = merge(var.tags, { Name = "${var.name_prefix}-sftp" })
}

resource "aws_iam_role" "user" {
  for_each           = var.users
  name               = "${var.name_prefix}-sftp-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

# One role per user, scoped to that user's prefix: partner A must not list, read, or overwrite
# partner B's drop. A single shared role is the usual shortcut and the usual incident.
data "aws_iam_policy_document" "user" {
  for_each = var.users
  statement {
    sid       = "ListOwnPrefix"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.target_bucket}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["sftp/${each.key}/*"]
    }
  }
  statement {
    sid       = "WriteOwnPrefix"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:GetObjectVersion"]
    resources = ["arn:aws:s3:::${var.target_bucket}/sftp/${each.key}/*"]
  }
  dynamic "statement" {
    for_each = var.target_bucket_kms_key_arn == "" ? [] : [var.target_bucket_kms_key_arn]
    content {
      sid       = "LakeKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "user" {
  for_each = var.users
  name     = "${var.name_prefix}-sftp-${each.key}"
  role     = aws_iam_role.user[each.key].id
  policy   = data.aws_iam_policy_document.user[each.key].json
}

resource "aws_transfer_user" "this" {
  for_each  = var.users
  server_id = aws_transfer_server.this.id
  user_name = each.key
  role      = aws_iam_role.user[each.key].arn

  # Chroot: the user sees their prefix as /, so a path traversal in a partner's client
  # cannot walk into the rest of the bucket.
  home_directory_type = "LOGICAL"
  home_directory_mappings {
    entry  = "/"
    target = "/${var.target_bucket}/sftp/${each.key}"
  }
  tags = var.tags
}

resource "aws_transfer_ssh_key" "this" {
  for_each  = var.users
  server_id = aws_transfer_server.this.id
  user_name = aws_transfer_user.this[each.key].user_name
  body      = each.value
}

output "sftp_endpoint" {
  value = aws_transfer_server.this.endpoint
}

output "sftp_server_id" {
  value = aws_transfer_server.this.id
}
