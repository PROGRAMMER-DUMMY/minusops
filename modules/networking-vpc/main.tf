# Module: networking-vpc
# Customer-managed VPC: public + private subnets across az_count AZs, NAT-gated egress, and
# the VPC's own default security group (self-referencing ingress + open egress) so
# orchestrator-mwaa and future VPC-attached modules (e.g. a Databricks workspace) share one
# security group definition instead of each hand-rolling their own -- same pattern AWS's own
# Databricks-on-AWS Terraform example uses (module.vpc.default_security_group_id).
# Endpoints are minimal by design (docs/project_plan.md Phase E addendum): S3 gateway is free
# and included unconditionally; STS/Kinesis interface endpoints are opt-in behind variables,
# off by default -- turn on only when an actual consumer needs private access to that service.
# Adapted from runs/manual-mwaa-network-scratch/main.tf (live-tested 2026-07-06: real apply +
# destroy completed cleanly, 15 resources, independently verified via
# aws ec2 describe-vpcs/describe-nat-gateways -- not written from a blank page).

variable "name_prefix" {
  type        = string
  description = "Prefix for resource names, e.g. data-platform-dev."
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "vpc_cidr" {
  type        = string
  default     = "10.0.0.0/16"
  description = "CIDR block for the VPC."
}

variable "az_count" {
  type        = number
  default     = 2
  description = "Number of availability zones to spread public/private subnets across. orchestrator-mwaa requires at least 2."
}

variable "single_nat_gateway" {
  type        = bool
  default     = true
  description = "true (default): one shared NAT gateway for all private subnets -- cheapest, no AZ-failure isolation for egress. false: one NAT gateway per AZ (own route table per private subnet) -- higher availability, proportionally higher cost."
}

variable "enable_sts_endpoint" {
  type        = bool
  default     = false
  description = "Add an STS interface VPC endpoint. Off by default (minimal-endpoint posture, docs/project_plan.md Phase E addendum) -- enable when a consuming module actually needs private STS access."
}

variable "enable_kinesis_endpoint" {
  type        = bool
  default     = false
  description = "Add a Kinesis Streams interface VPC endpoint. Off by default, same posture as enable_sts_endpoint."
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_region" "current" {}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(var.tags, { Name = "${var.name_prefix}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.name_prefix}-igw" })
}

resource "aws_subnet" "public" {
  count                   = var.az_count
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 100 + count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = merge(var.tags, { Name = "${var.name_prefix}-public-${count.index}" })
}

resource "aws_subnet" "private" {
  count             = var.az_count
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 1)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = merge(var.tags, { Name = "${var.name_prefix}-private-${count.index}" })
}

resource "aws_eip" "nat" {
  count  = var.single_nat_gateway ? 1 : var.az_count
  domain = "vpc"
  tags   = merge(var.tags, { Name = "${var.name_prefix}-nat-eip-${count.index}" })
}

resource "aws_nat_gateway" "this" {
  count         = var.single_nat_gateway ? 1 : var.az_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = merge(var.tags, { Name = "${var.name_prefix}-nat-${count.index}" })
  depends_on    = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = merge(var.tags, { Name = "${var.name_prefix}-public-rt" })
}

resource "aws_route_table_association" "public" {
  count          = var.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# One shared table when single_nat_gateway, one per AZ otherwise -- see aws_route_table_association.private
# below for how each private subnet picks the right one.
resource "aws_route_table" "private" {
  count  = var.single_nat_gateway ? 1 : var.az_count
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[count.index].id
  }
  tags = merge(var.tags, { Name = "${var.name_prefix}-private-rt-${count.index}" })
}

resource "aws_route_table_association" "private" {
  count          = var.az_count
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = var.single_nat_gateway ? aws_route_table.private[0].id : aws_route_table.private[count.index].id
}

# Manages the VPC's auto-created default security group (not a new named one) so every
# VPC-attached module -- orchestrator-mwaa today, a Databricks workspace in Phase 2 -- shares
# a single self-referencing security group instead of each defining its own.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.name_prefix}-default-sg" })

  ingress {
    description = "Self-referencing: allow all traffic from resources in this security group (required by MWAA; matches Databricks cluster-node self-reference requirement)."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# S3 gateway endpoint: free, near-universal need, included unconditionally -- not part of the
# minimal-endpoint decision's "opt-in" set (that applies to interface endpoints, which cost
# per-hour). Attached to every route table so both public and private subnets get it.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat([aws_route_table.public.id], aws_route_table.private[*].id)
  tags              = merge(var.tags, { Name = "${var.name_prefix}-s3-endpoint" })
}

# sts/kinesis-streams are AWS's long-stable standard service-name suffixes (com.amazonaws.<region>.<service>),
# not the kind of volatile, vendor-specific endpoint name (e.g. a Databricks SCC-relay/PrivateLink
# endpoint) the module-update-time-fetch policy exists to guard against -- safe to author directly.
resource "aws_vpc_endpoint" "sts" {
  count               = var.enable_sts_endpoint ? 1 : 0
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.sts"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_default_security_group.this.id]
  private_dns_enabled = true
  tags                = merge(var.tags, { Name = "${var.name_prefix}-sts-endpoint" })
}

resource "aws_vpc_endpoint" "kinesis" {
  count               = var.enable_kinesis_endpoint ? 1 : 0
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.kinesis-streams"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_default_security_group.this.id]
  private_dns_enabled = true
  tags                = merge(var.tags, { Name = "${var.name_prefix}-kinesis-endpoint" })
}

output "vpc_id" {
  value = aws_vpc.this.id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "default_security_group_id" {
  value = aws_default_security_group.this.id
}

output "nat_gateway_ids" {
  value = aws_nat_gateway.this[*].id
}
