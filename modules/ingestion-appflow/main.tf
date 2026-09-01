# Module: ingestion-appflow
# Scheduled pulls from a SaaS system (Salesforce, Stripe, Zendesk, Google Analytics) into the
# Bronze zone via Amazon AppFlow. Use instead of writing an API poller: AppFlow owns the
# pagination, retry, and rate-limit handling that a hand-rolled puller gets wrong first.
#
# The connector PROFILE is an input, not a resource here. A profile holds the OAuth grant or
# API key for the SaaS tenant; creating one in Terraform means the credential passes through
# a plan and lands in state (TerraShark FM-02). Create it once with `aws appflow
# create-connector-profile` or the console, and pass its name.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "connector_profile_name" {
  type        = string
  description = "Existing AppFlow connector profile for the SaaS tenant. Created out of band so the credential never enters Terraform state."
}

variable "connector_type" {
  type        = string
  default     = "Salesforce"
  description = "AppFlow connector: Salesforce, Zendesk, Slack, Marketo, Datadog, ServiceNow, Singular, Trendmicro, Veeva, Amplitude, Dynatrace, Infornexus, Googleanalytics."
}

variable "source_object" {
  type        = string
  description = "Object to pull, e.g. \"Account\" or \"Opportunity\" for Salesforce."
}

variable "target_bucket" {
  type        = string
  description = "Bronze bucket the flow writes to."
}

variable "target_prefix" {
  type        = string
  default     = ""
  description = "Key prefix inside the bucket. Defaults to saas/<source_object>."
}

variable "schedule_expression" {
  type        = string
  default     = "rate(1 day)"
  description = "Pull cadence. Unlike the orchestrator's schedule this DOES default: a SaaS ingestion flow with no trigger never runs, and there is no event to trigger it from."
}

variable "mapped_fields" {
  type        = list(string)
  default     = []
  description = "Fields to copy. Empty maps the whole object, which is the honest default for a Bronze landing zone -- filtering belongs in Silver, not at the ingestion boundary where a dropped field is unrecoverable."
}

locals {
  prefix = var.target_prefix != "" ? var.target_prefix : "saas/${lower(var.source_object)}"
}

resource "aws_appflow_flow" "this" {
  name = "${var.name_prefix}-${lower(var.source_object)}"
  tags = var.tags

  source_flow_config {
    connector_type         = var.connector_type
    connector_profile_name = var.connector_profile_name
    source_connector_properties {
      dynamic "salesforce" {
        for_each = var.connector_type == "Salesforce" ? [1] : []
        content {
          object = var.source_object
        }
      }
      dynamic "service_now" {
        for_each = var.connector_type == "Servicenow" ? [1] : []
        content {
          object = var.source_object
        }
      }
      dynamic "zendesk" {
        for_each = var.connector_type == "Zendesk" ? [1] : []
        content {
          object = var.source_object
        }
      }
    }
  }

  destination_flow_config {
    connector_type = "S3"
    destination_connector_properties {
      s3 {
        bucket_name   = var.target_bucket
        bucket_prefix = local.prefix
        s3_output_format_config {
          file_type = "PARQUET"
        }
      }
    }
  }

  # `connector_operator`'s per-connector fields are ATTRIBUTES, not nested blocks (verified
  # against the installed provider's own schema, not the rendered docs). NO_OP copies the
  # field through untouched, which is what a Bronze landing zone wants: reshaping at the
  # ingestion boundary makes a mistake unrecoverable, because the original was never stored.
  task {
    task_type     = "Map_all"
    source_fields = var.mapped_fields
    connector_operator {
      salesforce  = var.connector_type == "Salesforce" ? "NO_OP" : null
      service_now = var.connector_type == "Servicenow" ? "NO_OP" : null
      zendesk     = var.connector_type == "Zendesk" ? "NO_OP" : null
    }
  }

  trigger_config {
    trigger_type = "Scheduled"
    trigger_properties {
      scheduled {
        schedule_expression = var.schedule_expression
        # Incremental where the connector supports it: a full pull every day of an object
        # that only grows is the same bill compounding.
        data_pull_mode = "Incremental"
      }
    }
  }
}

output "flow_name" {
  value = aws_appflow_flow.this.name
}

output "flow_arn" {
  value = aws_appflow_flow.this.arn
}
