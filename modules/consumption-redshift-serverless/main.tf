# Module: consumption-redshift-serverless
# Warehouse-class consumption for high-concurrency BI. Athena caps at ~20 concurrent
# queries per workgroup by default and engines like Trino degrade under concurrent BI
# load — once hundreds of analysts/dashboards arrive, a warehouse (RA3/Serverless)
# is the published pattern. Base capacity defaults to the minimum (8 RPU).

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "base_capacity_rpu" {
  type    = number
  default = 8
}

variable "publicly_accessible" {
  type    = bool
  default = false
}

resource "aws_redshiftserverless_namespace" "this" {
  namespace_name = "${var.name_prefix}-analytics"
  tags           = var.tags
}

resource "aws_redshiftserverless_workgroup" "this" {
  workgroup_name      = "${var.name_prefix}-bi"
  namespace_name      = aws_redshiftserverless_namespace.this.namespace_name
  base_capacity       = var.base_capacity_rpu
  publicly_accessible = var.publicly_accessible
  tags                = var.tags
}

output "namespace_name" {
  value = aws_redshiftserverless_namespace.this.namespace_name
}

output "workgroup_name" {
  value = aws_redshiftserverless_workgroup.this.workgroup_name
}
