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

# base_capacity is a FLOOR. Without a ceiling the workgroup scales RPUs as far as the query
# load asks, and Redshift Serverless bills per RPU-hour -- the one unbounded spend knob in a
# repo whose cost doctrine is otherwise strict everywhere. 128 RPU is roughly 16x the 8 RPU
# floor: enough headroom for a quarter-close spike, small enough that a runaway query is a
# line item rather than an incident.
variable "max_capacity" {
  type        = number
  default     = 128
  description = "Ceiling in RPUs. Must be at least base_capacity_rpu."

  validation {
    condition     = var.max_capacity >= var.base_capacity_rpu
    error_message = "max_capacity must be >= base_capacity_rpu -- a ceiling below the floor produces a plan that applies and a workgroup that will not start."
  }
}

variable "usage_limit_rpu_hours" {
  type        = number
  default     = 0
  description = "Monthly RPU-hour budget. 0 provisions no usage limit -- the ceiling above still bounds burst rate, but nothing bounds total spend over the month."

  validation {
    condition     = var.usage_limit_rpu_hours >= 0
    error_message = "usage_limit_rpu_hours cannot be negative."
  }
}

variable "usage_limit_breach_action" {
  type        = string
  default     = "log"
  description = "What happens at the budget. `log` pages someone; `deactivate` takes BI offline. The default is the one that wakes a human rather than the one that stops the business mid-quarter-close without warning."

  validation {
    condition     = contains(["log", "emit-metric", "deactivate"], var.usage_limit_breach_action)
    error_message = "usage_limit_breach_action must be one of: log, emit-metric, deactivate."
  }
}

resource "aws_redshiftserverless_namespace" "this" {
  namespace_name = "${var.name_prefix}-analytics"
  tags           = var.tags
}

resource "aws_redshiftserverless_workgroup" "this" {
  workgroup_name      = "${var.name_prefix}-bi"
  namespace_name      = aws_redshiftserverless_namespace.this.namespace_name
  base_capacity       = var.base_capacity_rpu
  max_capacity        = var.max_capacity
  publicly_accessible = var.publicly_accessible
  tags                = var.tags
}

# The ceiling bounds burst RATE; this bounds TOTAL spend over the period. Both are needed:
# a workgroup pinned at 128 RPU for a month costs the same as one that spiked to 512 for an
# afternoon, and only one of those is visible on a capacity graph.
resource "aws_redshiftserverless_usage_limit" "monthly" {
  count = var.usage_limit_rpu_hours > 0 ? 1 : 0

  resource_arn  = aws_redshiftserverless_workgroup.this.arn
  usage_type    = "serverless-compute"
  amount        = var.usage_limit_rpu_hours
  period        = "monthly"
  breach_action = var.usage_limit_breach_action
}

output "namespace_name" {
  value = aws_redshiftserverless_namespace.this.namespace_name
}

output "workgroup_name" {
  value = aws_redshiftserverless_workgroup.this.workgroup_name
}

output "capacity_bounds_rpu" {
  value       = { base = var.base_capacity_rpu, max = var.max_capacity }
  description = "Declared floor and ceiling, so a reviewer can see the spend envelope without opening the console."
}

output "usage_limit_configured" {
  value       = var.usage_limit_rpu_hours > 0
  description = "False means burst rate is bounded but monthly total is not."
}
