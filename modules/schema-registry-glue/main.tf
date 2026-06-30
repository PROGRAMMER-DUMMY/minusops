# Module: schema-registry-glue
# Schema enforcement / data contracts via the AWS Glue Schema Registry. Producers and consumers
# validate against registered schemas with a configurable compatibility mode, so breaking schema
# changes are rejected at the boundary instead of corrupting downstream data.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "compatibility" {
  type        = string
  default     = "BACKWARD"
  description = "Schema evolution rule: NONE|DISABLED|BACKWARD|BACKWARD_ALL|FORWARD|FORWARD_ALL|FULL|FULL_ALL."
}

variable "schemas" {
  type        = map(string)
  default     = {}
  description = "schema_name => Avro schema definition JSON."
}

resource "aws_glue_registry" "this" {
  registry_name = "${var.name_prefix}-registry"
  description   = "Schema enforcement for ${var.name_prefix} data contracts."
  tags          = var.tags
}

resource "aws_glue_schema" "this" {
  for_each          = var.schemas
  schema_name       = each.key
  registry_arn      = aws_glue_registry.this.arn
  data_format       = "AVRO"
  compatibility     = var.compatibility
  schema_definition = each.value
  tags              = var.tags
}

output "registry_arn" {
  value = aws_glue_registry.this.arn
}

output "schema_arns" {
  value = { for k, s in aws_glue_schema.this : k => s.arn }
}
