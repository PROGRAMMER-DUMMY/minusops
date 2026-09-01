# Module: cube-semantic-layer
# Headless universal semantic layer: one governed metric definition served over SQL, REST and
# GraphQL, for consumers that cannot run dbt in-process -- BI tools, embedded analytics, and
# LLM agents that need a metrics API rather than table access.
#
# CHOOSE THIS OVER dbt-semantic-layer WHEN the consumers are heterogeneous and latency-
# sensitive. dbt's semantic layer compiles SQL against the warehouse on every request; Cube
# adds a pre-aggregation cache, which is the entire reason to run a separate service. A Cube
# deployment with no pre-aggregations is a proxy that re-scans the lake on every dashboard
# refresh and costs more than the thing it fronts.
#
# WHAT THIS PROVISIONS: the container image location, the Redis-backed cache, and the
# configuration surface. It does NOT provision the EKS/ECS service itself -- the domain team
# owns their runtime, and a module that created a cluster here would be one MinusOps could
# not safely destroy. Outputs feed a task definition or Helm values in the domain repo.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "cube_image" {
  type        = string
  default     = "cubejs/cube:latest"
  description = "Container image. `latest` is fine for a scaffold and wrong for production -- pin a digest before this reaches prod, or a silent upstream change becomes a silent metric change."
}

variable "pre_aggregation_refresh_minutes" {
  type        = number
  default     = 60
  description = "How stale a cached rollup may be. This is a data-freshness commitment, not a performance knob: dashboards will report numbers up to this old."

  validation {
    condition     = var.pre_aggregation_refresh_minutes >= 1
    error_message = "Refresh interval must be at least 1 minute -- anything lower re-scans continuously and defeats the cache."
  }
}

variable "cache_node_type" {
  type        = string
  default     = "cache.t4g.micro"
  description = "ElastiCache node backing the pre-aggregation store."
}

variable "subnet_ids" {
  type        = list(string)
  description = "Private subnets for the cache. Cube's cache holds pre-aggregated business metrics -- it is not public-facing under any configuration."
}

variable "security_group_ids" {
  type    = list(string)
  default = []
}

resource "aws_elasticache_subnet_group" "cube" {
  name       = "${var.name_prefix}-cube-cache"
  subnet_ids = var.subnet_ids
  tags       = var.tags
}

resource "aws_elasticache_replication_group" "cube" {
  replication_group_id = "${var.name_prefix}-cube"
  description          = "Cube pre-aggregation cache"
  node_type            = var.cache_node_type
  num_cache_clusters   = 1
  engine               = "redis"
  port                 = 6379

  subnet_group_name  = aws_elasticache_subnet_group.cube.name
  security_group_ids = var.security_group_ids

  # The cache holds business metrics. Both flags, or the pre-aggregations move and rest in
  # the clear inside the VPC.
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  tags = var.tags
}

output "cache_endpoint" {
  value = aws_elasticache_replication_group.cube.primary_endpoint_address
}

output "cube_image" {
  value = var.cube_image
}

output "schema_path" {
  value       = "cube/schema"
  description = "Scaffold shipped with this module; edit it in the domain repo after `minusctl export`."
}
