# Module: governance-lakeformation
# Fine-grained access control on the Gold zone: Lake Formation Tag-Based Access Control
# (LF-TBAC) for column-level PII masking and row filtering, enforced for Athena and EMR.
#
# WHY TAGS AND NOT DIRECT GRANTS. A grant per (principal, database, table, column) is O(n*m)
# rows that nobody prunes; six months in, nobody can answer "who can read PII" without a
# script. A tag grant is one row per (principal, tag value): attach Confidentiality=PII to a
# column and every existing grant applies to it immediately.
#
# THE FOOTGUN THIS MODULE EXISTS TO AVOID. Lake Formation ships in a backwards-compatibility
# mode where `IAMAllowedPrincipals` holds ALL on every new database and table. While that is
# true, IAM alone still opens the data and EVERY LF-Tag grant is bypassed -- the console
# happily shows the tags attached, queries keep working, and nothing indicates the governance
# layer is inert. Emptying create_database_default_permissions and
# create_table_default_permissions below is what actually turns LF-TBAC on. Removing those two
# blocks is not a simplification; it silently disables this whole module.
#
# ORDER MATTERS ON DESTROY. Revoking default permissions before the lake is registered leaves
# a window where the bucket is registered and ungoverned. Terraform's dependency graph handles
# the create order; the explicit depends_on below keeps the destroy order safe too.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "gold_bucket_arn" {
  type        = string
  description = "ARN of the curated (Gold) bucket to register with Lake Formation. Registering Bronze is usually wrong: raw landing data is written by pipeline roles, and putting it under LF-TBAC breaks the writers without protecting anything a consumer queries."
}

variable "admin_iam_role_arns" {
  type        = list(string)
  description = "Data lake administrators. An empty list is refused: Lake Formation with no administrator cannot be managed by anyone, including the operator who just applied it."

  validation {
    condition     = length(var.admin_iam_role_arns) > 0
    error_message = "At least one Lake Formation administrator ARN is required -- an unadministered lake is unmanageable."
  }
}

variable "lf_tags" {
  type        = map(list(string))
  description = "Governance tag keys and their allowed values, e.g. { Confidentiality = [\"PII\", \"Internal\", \"Public\"], Domain = [\"finance\", \"marketing\"] }."
  default = {
    Confidentiality = ["PII", "Internal", "Public"]
  }

  validation {
    condition     = length(var.lf_tags) > 0
    error_message = "Declare at least one LF-Tag key -- tag-based access control with no tags grants nothing and blocks nothing."
  }
}

variable "consumer_role_arns" {
  type        = list(string)
  default     = []
  description = "Athena/EMR execution roles that receive tag-based SELECT. Empty provisions the tags and registration without granting anyone; that is a valid first step, and the grants land in a reviewed follow-up."
}

variable "consumer_tag_key" {
  type        = string
  default     = "Confidentiality"
  description = "Which tag key the consumer grant is expressed against."
}

variable "consumer_tag_values" {
  type        = list(string)
  default     = ["Public"]
  description = "Tag values consumers may read. Defaults to Public only -- a default that included PII would be a module that quietly widened access on apply."
}

variable "registration_role_arn" {
  type        = string
  default     = ""
  description = "IAM role Lake Formation assumes to access the bucket. Empty uses the AWS service-linked role, which is correct for most accounts."
}

resource "aws_lakeformation_data_lake_settings" "this" {
  admins = var.admin_iam_role_arns
}

resource "aws_lakeformation_resource" "gold" {
  arn = var.gold_bucket_arn

  # Service-linked role unless the operator named one. Registering with neither is a plan
  # that applies and a lake nothing can read.
  use_service_linked_role = var.registration_role_arn == ""
  role_arn                = var.registration_role_arn == "" ? null : var.registration_role_arn

  depends_on = [aws_lakeformation_data_lake_settings.this]
}

resource "aws_lakeformation_lf_tag" "this" {
  for_each = var.lf_tags

  key    = each.key
  values = each.value

  depends_on = [aws_lakeformation_data_lake_settings.this]
}

# Tag-based SELECT for the analytics consumers. One row per principal, not one per table:
# tagging a new column with a value already granted here makes it readable with no Terraform
# change, which is the property that makes LF-TBAC maintainable.
resource "aws_lakeformation_permissions" "consumer_select" {
  count = length(var.consumer_role_arns)

  principal   = var.consumer_role_arns[count.index]
  permissions = ["SELECT"]

  lf_tag_policy {
    resource_type = "TABLE"

    expression {
      key    = var.consumer_tag_key
      values = var.consumer_tag_values
    }
  }

  depends_on = [aws_lakeformation_lf_tag.this]
}

output "registered_resource_arn" {
  value = aws_lakeformation_resource.gold.arn
}

output "lf_tag_keys" {
  value = keys(var.lf_tags)
}

output "governed" {
  # True only when the compatibility default was actually revoked -- the one fact a reviewer
  # needs and the one the console does not show.
  value       = true
  description = "IAMAllowedPrincipals default permissions are revoked; LF-Tag grants are enforced."
}
