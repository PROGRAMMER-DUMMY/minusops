# Module: databricks-workspace
# Databricks-on-AWS workspace (classic, customer-managed VPC) -- account-level Databricks
# resources (credentials, storage config, network, workspace, metastore + assignment) plus the
# AWS-side cross-account IAM role and root storage bucket they depend on. Consumes vpc_id/
# subnet_ids/security_group_ids from the networking-vpc module (wired by synthesizer.py, same
# pattern as orchestrator-mwaa). Uses the default (unaliased) databricks provider throughout --
# the account-level provider configured in the composed root is sufficient for every resource
# here, including the metastore assignment (via provider_config { workspace_id }), so no second
# aliased provider pointed at the new workspace's own host is needed.
#
# Cross-account IAM: both the trust policy (data.databricks_aws_assume_role_policy) and the
# permissions policy (data.databricks_aws_crossaccount_policy) are Databricks' own canonical
# generators, not hand-rolled JSON -- SEC-05 (core/reporting/optimize_analyzer.py) verifies the
# trust policy actually supplies external_id, as a backstop in case a future edit bypasses this
# data source and hand-rolls the policy again.
#
# DBU cost: not priced by this project's cost gate (docs/project_plan.md Phase E addendum) --
# DBUs aren't in the AWS Price List API. coverage_audit.py will report the databricks_* resource
# types here as unresolved; that's the recorded policy working as intended, not a gap.

# databricks/databricks isn't under the default `hashicorp` registry namespace, so unlike aws
# (whose default namespace already resolves correctly without this), every module using it --
# not just the root composition -- must declare its own required_providers source, or Terraform
# infers this module wants the (nonexistent) hashicorp/databricks and the root/child addresses
# disagree. This declares the source constraint only; actual provider configuration (host,
# auth) still lives solely in the composed root (synthesizer.py's _render_providers), matching
# Terraform's rule that only root modules configure providers.
terraform {
  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = ">= 1.0"
    }
  }
}

variable "name_prefix" {
  type        = string
  description = "Prefix for resource names, e.g. data-platform-dev."
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "databricks_account_id" {
  type        = string
  description = "Databricks account ID (top-right of https://accounts.cloud.databricks.com/)."
}

variable "vpc_id" {
  type        = string
  description = "VPC id the workspace's cluster nodes run in (from networking-vpc)."
}

variable "subnet_ids" {
  type        = list(string)
  description = "Two or more private subnet IDs (from networking-vpc)."
}

variable "security_group_ids" {
  type        = list(string)
  description = "Security group IDs for cluster nodes (from networking-vpc's default_security_group_id)."
}

variable "existing_metastore_id" {
  type        = string
  default     = ""
  description = "Attach to an existing Unity Catalog metastore instead of creating one -- a metastore is region-scoped (one per region) and shareable across workspaces, so a second workspace in the same region must reuse the first one's metastore, not create a second."
}

variable "catalog_name" {
  type        = string
  default     = ""
  description = "Unity Catalog catalog to create inside the workspace's metastore. Empty = skip (no catalog created by this module). Note: creating a catalog here removes the 'default' schema Databricks auto-creates with it (the provider's own documented behavior, for clean destroy) -- schema/table setup inside the catalog is an application-level step, not this module's job."
}

variable "create_sql_warehouse" {
  type        = bool
  default     = false
  description = "Create a Databricks SQL Warehouse for warehouse-mode query execution."
}

variable "sql_warehouse_cluster_size" {
  type        = string
  default     = "2X-Small"
  description = "SQL warehouse cluster size. Smallest by default -- cost-conscious, same posture as networking-vpc's single-NAT default."
}

variable "sql_warehouse_auto_stop_mins" {
  type        = number
  default     = 10
  description = "Minutes idle before the warehouse auto-stops. Provider default is 120; shortened here for the same cost-conscious posture."
}

data "aws_region" "current" {}

resource "aws_s3_bucket" "root_storage_bucket" {
  bucket        = "${var.name_prefix}-dbx-root"
  force_destroy = true
  tags          = var.tags
}

resource "aws_s3_bucket_versioning" "root_storage_bucket" {
  bucket = aws_s3_bucket.root_storage_bucket.id
  versioning_configuration {
    status = "Disabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "root_storage_bucket" {
  bucket = aws_s3_bucket.root_storage_bucket.bucket
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "root_storage_bucket" {
  bucket                  = aws_s3_bucket.root_storage_bucket.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Vetted bucket policy generator -- not hand-rolled, same principle as the trust/permissions
# policies below.
data "databricks_aws_bucket_policy" "this" {
  bucket = aws_s3_bucket.root_storage_bucket.bucket
}

resource "aws_s3_bucket_policy" "root_storage_bucket" {
  bucket     = aws_s3_bucket.root_storage_bucket.id
  policy     = data.databricks_aws_bucket_policy.this.json
  depends_on = [aws_s3_bucket_public_access_block.root_storage_bucket]
}

# Trust policy: who may assume this role. SEC-05 verifies external_id is actually supplied here.
data "databricks_aws_assume_role_policy" "this" {
  external_id = var.databricks_account_id
}

resource "aws_iam_role" "cross_account_role" {
  name               = "${var.name_prefix}-dbx-crossaccount"
  assume_role_policy = data.databricks_aws_assume_role_policy.this.json
  tags               = var.tags
}

# Permissions policy: what the role may do. Also Databricks-canonical, not hand-rolled.
data "databricks_aws_crossaccount_policy" "this" {}

resource "aws_iam_role_policy" "cross_account_role" {
  name   = "${var.name_prefix}-dbx-crossaccount-policy"
  role   = aws_iam_role.cross_account_role.id
  policy = data.databricks_aws_crossaccount_policy.this.json
}

resource "databricks_mws_credentials" "this" {
  # account_id is deprecated on this resource specifically -- Databricks' docs say it should
  # come from the provider instance instead (which the composed root already configures).
  credentials_name = "${var.name_prefix}-dbx-creds"
  role_arn         = aws_iam_role.cross_account_role.arn
}

resource "databricks_mws_storage_configurations" "this" {
  account_id                 = var.databricks_account_id
  storage_configuration_name = "${var.name_prefix}-dbx-storage"
  bucket_name                = aws_s3_bucket.root_storage_bucket.bucket
}

resource "databricks_mws_networks" "this" {
  account_id         = var.databricks_account_id
  network_name       = "${var.name_prefix}-dbx-network"
  vpc_id             = var.vpc_id
  subnet_ids         = var.subnet_ids
  security_group_ids = var.security_group_ids
}

resource "databricks_mws_workspaces" "this" {
  account_id     = var.databricks_account_id
  workspace_name = var.name_prefix
  aws_region     = data.aws_region.current.region

  credentials_id           = databricks_mws_credentials.this.credentials_id
  storage_configuration_id = databricks_mws_storage_configurations.this.storage_configuration_id
  network_id               = databricks_mws_networks.this.network_id

  custom_tags = var.tags
}

# Region-scoped (one metastore per region, shareable across workspaces) -- only created when
# the caller hasn't supplied an existing one to attach to instead.
resource "databricks_metastore" "this" {
  count         = var.existing_metastore_id == "" ? 1 : 0
  name          = "${var.name_prefix}-metastore"
  storage_root  = "s3://${aws_s3_bucket.root_storage_bucket.id}/metastore"
  owner         = "uc admins"
  region        = data.aws_region.current.region
  force_destroy = true
}

# Runs through the same account-level provider as everything above -- no aliased
# workspace-scoped provider needed; workspace_id is just a normal resource reference,
# resolved by Terraform's dependency graph in one apply.
resource "databricks_metastore_assignment" "this" {
  metastore_id = var.existing_metastore_id != "" ? var.existing_metastore_id : databricks_metastore.this[0].id
  workspace_id = databricks_mws_workspaces.this.workspace_id
}

# Optional named catalog (Phase 2b) -- skipped unless catalog_name is supplied. Same
# provider_config { workspace_id } pattern as the metastore assignment above; not yet proven
# against a live account (see HANDOFF.md §6 item 10), same carried-forward caveat as that
# resource.
resource "databricks_catalog" "this" {
  count         = var.catalog_name == "" ? 0 : 1
  name          = var.catalog_name
  force_destroy = true # same posture as databricks_metastore.this -- clean teardown over content protection
  provider_config {
    workspace_id = databricks_mws_workspaces.this.workspace_id
  }
  depends_on = [databricks_metastore_assignment.this]
}

# Optional SQL Warehouse (Phase 2b) -- skipped unless create_sql_warehouse is true. Needed
# only for warehouse-mode query execution; job-based execution doesn't need one.
resource "databricks_sql_endpoint" "this" {
  count            = var.create_sql_warehouse ? 1 : 0
  name             = "${var.name_prefix}-warehouse"
  cluster_size     = var.sql_warehouse_cluster_size
  max_num_clusters = 1
  auto_stop_mins   = var.sql_warehouse_auto_stop_mins
  provider_config {
    workspace_id = databricks_mws_workspaces.this.workspace_id
  }
}

output "workspace_id" {
  value = databricks_mws_workspaces.this.workspace_id
}

output "workspace_url" {
  value = databricks_mws_workspaces.this.workspace_url
}

output "metastore_id" {
  value = var.existing_metastore_id != "" ? var.existing_metastore_id : databricks_metastore.this[0].id
}

output "catalog_name" {
  value = var.catalog_name == "" ? null : databricks_catalog.this[0].name
}

output "sql_warehouse_id" {
  value = var.create_sql_warehouse ? databricks_sql_endpoint.this[0].id : null
}

output "sql_warehouse_jdbc_url" {
  value = var.create_sql_warehouse ? databricks_sql_endpoint.this[0].jdbc_url : null
}
