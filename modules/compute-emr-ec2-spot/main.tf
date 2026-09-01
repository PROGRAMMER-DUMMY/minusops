# Module: compute-emr-ec2-spot
# EMR on EC2 with Graviton instance fleets and Spot task capacity, for sustained multi-TB/day
# Spark. Use ONLY above roughly 5 TB/day: below that, EMR Serverless costs less all-in once
# cluster idle time and the operational burden are counted, and Glue costs less again.
#
# The three-fleet split is the whole point of using fleets rather than groups:
#   master  -> ON DEMAND, always. A lost master kills the cluster, and the saving on one node
#              is rounding error against re-running a day of batch.
#   core    -> ON DEMAND. Core nodes carry HDFS blocks, so reclaiming one loses shuffle data
#              and triggers a recompute: Spot here trades a large risk for a small saving.
#   task    -> SPOT, diversified across several instance types. Task nodes hold no persistent
#              data, so an interruption costs one re-executed task. The saving comes from here.
#
# Diversification is not decoration: a single instance type in one AZ is a single Spot pool,
# and when that pool is reclaimed the whole task fleet goes at once. Several types spread the
# reclaim risk across pools, which is what capacity-optimized allocation then exploits.

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "subnet_ids" {
  type        = list(string)
  description = "Private subnets for the cluster. More subnets means more Spot pools; a single subnet defeats the diversification below."
}

variable "release_label" {
  type        = string
  default     = "emr-7.5.0"
  description = "Pinned, not floating: an EMR release bump changes the Spark version under a running pipeline (TerraShark FM-04)."
}

variable "target_buckets" {
  type        = list(string)
  default     = []
  description = "Bucket names the cluster reads and writes."
}

variable "kms_key_arn" {
  type        = string
  default     = ""
  description = "Lake CMK. Without the data-key grants the cluster 403s writing to an SSE-KMS bucket."
}

variable "master_instance_types" {
  type        = list(string)
  default     = ["m7g.xlarge", "m6g.xlarge"]
  description = "Graviton. Two types so the on-demand fleet can still be filled when one is short in an AZ."
}

variable "core_instance_types" {
  type        = list(string)
  default     = ["r7g.2xlarge", "r6g.2xlarge"]
  description = "Memory-optimised Graviton: Spark shuffle is memory-bound long before it is CPU-bound."
}

variable "task_instance_types" {
  type        = list(string)
  default     = ["r7g.2xlarge", "r6g.2xlarge", "r7g.4xlarge", "m7g.4xlarge"]
  description = "At least three types, ideally across families and sizes. Each type-and-AZ pair is a separate Spot pool; one type is one pool, and one reclaim event takes the whole fleet."

  validation {
    condition     = length(var.task_instance_types) >= 3
    error_message = "Give at least 3 task instance types: fewer than 3 Spot pools is not diversification, and the fleet is reclaimed as a unit."
  }
}

variable "core_target_capacity" {
  type    = number
  default = 2
}

variable "task_target_spot_capacity" {
  type        = number
  default     = 32
  description = "Spot capacity units for task nodes. Weighted by vCPU below, so this counts vCPUs rather than instances."
}

variable "spot_timeout_minutes" {
  type    = number
  default = 20
}

variable "idle_timeout_seconds" {
  type        = number
  default     = 3600
  description = "Auto-terminate after this much idle time. A forgotten multi-TB cluster is the most expensive mistake this module can make, so the default is one hour rather than never."
}

data "aws_iam_policy_document" "emr_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["elasticmapreduce.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "service" {
  name               = "${var.name_prefix}-emr-service"
  assume_role_policy = data.aws_iam_policy_document.emr_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "service" {
  role       = aws_iam_role.service.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEMRServicePolicy_v2"
}

resource "aws_iam_role" "instance" {
  name               = "${var.name_prefix}-emr-instance"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
  tags               = var.tags
}

# Data access is written here rather than attached from an AWS managed policy: the managed EMR
# instance policy grants S3 far more broadly than one pipeline's own buckets (SEC-02).
data "aws_iam_policy_document" "instance" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:*:*:*"]
  }
  dynamic "statement" {
    for_each = length(var.target_buckets) > 0 ? [1] : []
    content {
      sid     = "DataLake"
      actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
      resources = concat(
        [for b in var.target_buckets : "arn:aws:s3:::${b}"],
        [for b in var.target_buckets : "arn:aws:s3:::${b}/*"],
      )
    }
  }
  dynamic "statement" {
    for_each = var.kms_key_arn == "" ? [] : [var.kms_key_arn]
    content {
      sid       = "LakeKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "instance" {
  name   = "${var.name_prefix}-emr-instance"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.name_prefix}-emr-instance"
  role = aws_iam_role.instance.name
}

resource "aws_emr_cluster" "this" {
  name          = "${var.name_prefix}-emr"
  release_label = var.release_label
  applications  = ["Spark"]
  service_role  = aws_iam_role.service.arn
  tags          = var.tags

  ec2_attributes {
    subnet_ids       = var.subnet_ids
    instance_profile = aws_iam_instance_profile.instance.arn
  }

  master_instance_fleet {
    name                      = "master"
    target_on_demand_capacity = 1
    dynamic "instance_type_configs" {
      for_each = var.master_instance_types
      content {
        instance_type     = instance_type_configs.value
        weighted_capacity = 1
      }
    }
  }

  core_instance_fleet {
    name                      = "core"
    target_on_demand_capacity = var.core_target_capacity
    dynamic "instance_type_configs" {
      for_each = var.core_instance_types
      content {
        instance_type     = instance_type_configs.value
        weighted_capacity = 1
      }
    }
  }

  # The failure this guards against is a cluster left running after its batch finished, which
  # at this scale costs more per forgotten day than the pipeline saves in a month.
  auto_termination_policy {
    idle_timeout = var.idle_timeout_seconds
  }
}

# Task capacity is a separate resource: the cluster resource carries master and core fleets
# only, and the Spot fleet that produces the actual saving attaches here.
resource "aws_emr_instance_fleet" "task" {
  cluster_id           = aws_emr_cluster.this.id
  name                 = "task-spot"
  target_spot_capacity = var.task_target_spot_capacity

  dynamic "instance_type_configs" {
    for_each = var.task_instance_types
    content {
      instance_type = instance_type_configs.value
      # Weighted by vCPU, so target capacity means vCPUs and mixing a 2xlarge with a 4xlarge
      # in one fleet does not silently double the cluster.
      weighted_capacity = tonumber(regex("^[a-z0-9]+\\.(\\d+)xlarge$", instance_type_configs.value)[0]) * 4
      # No bid_price: the default is the on-demand price, which is what you want here. A lower
      # bid buys a marginally better rate in exchange for being outbid and losing the fleet.
    }
  }

  launch_specifications {
    spot_specification {
      # capacity-optimized picks the pools with the deepest spare capacity, which is the
      # strategy that actually reduces interruptions; lowest-price maximises them.
      allocation_strategy      = "capacity-optimized"
      timeout_action           = "SWITCH_TO_ON_DEMAND"
      timeout_duration_minutes = var.spot_timeout_minutes
    }
  }
}

output "cluster_id" {
  value = aws_emr_cluster.this.id
}

output "instance_role_arn" {
  value = aws_iam_role.instance.arn
}
