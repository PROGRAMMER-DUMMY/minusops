# Module: streaming-msk-kafka
# Managed Kafka for the speed layer, when the requirement is genuinely Kafka -- an existing
# Kafka producer fleet, Kafka Connect, or exactly-once semantics across consumer groups.
#
# If the requirement is only "stream data into the lake", `ingest-firehose` or
# `speed-layer-kinesis` costs less and needs no broker sizing. MSK earns its price when the
# Kafka PROTOCOL is the requirement, not when streaming is.
#
# Auth is IAM SASL only. Kafka's alternatives here are SCRAM (a username and password, which
# would have to live in a variable or a secret this module reads) or mTLS (a private CA to
# operate). IAM means the same roles that govern everything else govern topic access, and no
# credential exists to leak (TerraShark FM-02).

variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "subnet_ids" {
  type        = list(string)
  description = "Private subnets, one per AZ. MSK places one broker per subnet and requires at least two."

  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "MSK needs at least 2 subnets in different AZs. A single-AZ Kafka cluster loses the durability that is the reason to run Kafka."
  }
}

variable "security_group_ids" {
  type    = list(string)
  default = []
}

variable "kafka_version" {
  type        = string
  default     = "3.6.0"
  description = "Pinned, not floating: a broker version bump is a rolling restart of the cluster (TerraShark FM-04)."
}

variable "broker_instance_type" {
  type        = string
  default     = "kafka.m7g.large"
  description = "Graviton. Kafka is JVM and IO-bound, runs on ARM unchanged, and costs materially less per broker-hour."
}

variable "broker_ebs_volume_size_gb" {
  type        = number
  default     = 100
  description = "Per broker. Sized for retention x throughput; the default holds roughly a week of a modest topic set."
}

variable "kms_key_arn" {
  type        = string
  default     = ""
  description = "CMK for data at rest on the brokers. Empty uses the AWS-managed MSK key."
}

variable "sink_bucket" {
  type        = string
  default     = ""
  description = "Bronze bucket an S3 sink connector writes to. Empty skips the connector role entirely rather than creating one with nothing to reach."
}

variable "sink_bucket_kms_key_arn" {
  type        = string
  default     = ""
  description = "CMK on the sink bucket. Without kms:GenerateDataKey the connector 403s on an SSE-KMS bucket -- the same failure MINUS-108 fixed for Glue."
}

resource "aws_cloudwatch_log_group" "broker" {
  name              = "/aws/msk/${var.name_prefix}"
  retention_in_days = 30

  # Broker logs carry topic and client detail. The default is an AWS-owned key, which cannot
  # be audited or revoked independently of the account; null falls back to that only when the
  # module is used standalone with no CMK supplied.
  kms_key_id = var.kms_key_arn == "" ? null : var.kms_key_arn
  tags       = var.tags
}

resource "aws_msk_cluster" "this" {
  cluster_name  = "${var.name_prefix}-kafka"
  kafka_version = var.kafka_version
  # One broker per subnet: MSK requires the count to be a multiple of the AZ count, so
  # deriving it removes the most common "invalid parameter" failure at creation.
  number_of_broker_nodes = length(var.subnet_ids)
  tags                   = var.tags

  broker_node_group_info {
    instance_type   = var.broker_instance_type
    client_subnets  = var.subnet_ids
    security_groups = var.security_group_ids

    storage_info {
      ebs_storage_info {
        volume_size = var.broker_ebs_volume_size_gb
      }
    }
  }

  client_authentication {
    sasl {
      iam = true
    }
    # No `unauthenticated` block: its absence is what disables anonymous access. Kafka has no
    # concept of a read-only anonymous client -- an unauthenticated connection can produce and
    # consume on any topic the cluster allows.
  }

  encryption_info {
    encryption_at_rest_kms_key_arn = var.kms_key_arn == "" ? null : var.kms_key_arn
    encryption_in_transit {
      # TLS between clients and brokers AND between brokers. PLAINTEXT is offered by the API
      # and is how a "private subnet, it's fine" cluster ends up shipping topic data in clear
      # across AZs.
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.broker.name
      }
    }
  }
}

# --- S3 sink connector role -----------------------------------------------------------
# Created only when a sink bucket is named. A connector role with no bucket to write is an
# unused principal that still shows up in every IAM review.

data "aws_iam_policy_document" "connect_assume" {
  count = var.sink_bucket == "" ? 0 : 1
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["kafkaconnect.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "connector" {
  count              = var.sink_bucket == "" ? 0 : 1
  name               = "${var.name_prefix}-msk-s3-sink"
  assume_role_policy = data.aws_iam_policy_document.connect_assume[0].json
  tags               = var.tags
}

data "aws_iam_policy_document" "connector" {
  count = var.sink_bucket == "" ? 0 : 1

  # Topic access is scoped to this cluster's ARN, not "*": an IAM-authenticated Kafka client
  # with a wildcard cluster resource can read every topic in the account's other clusters too.
  statement {
    sid       = "ReadThisCluster"
    actions   = ["kafka-cluster:Connect", "kafka-cluster:DescribeCluster"]
    resources = [aws_msk_cluster.this.arn]
  }
  statement {
    sid = "ConsumeTopics"
    actions = ["kafka-cluster:DescribeTopic", "kafka-cluster:ReadData",
    "kafka-cluster:DescribeGroup", "kafka-cluster:AlterGroup"]
    # HCL has no string `+`; interpolation is the concatenation operator here.
    resources = [
      "${replace(aws_msk_cluster.this.arn, ":cluster/", ":topic/")}/*",
      "${replace(aws_msk_cluster.this.arn, ":cluster/", ":group/")}/*",
    ]
  }
  statement {
    sid       = "WriteSink"
    actions   = ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.sink_bucket}", "arn:aws:s3:::${var.sink_bucket}/*"]
  }
  dynamic "statement" {
    for_each = var.sink_bucket_kms_key_arn == "" ? [] : [var.sink_bucket_kms_key_arn]
    content {
      sid       = "LakeKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "connector" {
  count  = var.sink_bucket == "" ? 0 : 1
  name   = "${var.name_prefix}-msk-s3-sink"
  role   = aws_iam_role.connector[0].id
  policy = data.aws_iam_policy_document.connector[0].json
}

output "cluster_arn" {
  value = aws_msk_cluster.this.arn
}

output "bootstrap_brokers_sasl_iam" {
  value       = aws_msk_cluster.this.bootstrap_brokers_sasl_iam
  description = "IAM-SASL bootstrap endpoints. There is no plaintext endpoint by design."
}

output "connector_role_arn" {
  value = var.sink_bucket == "" ? null : aws_iam_role.connector[0].arn
}
