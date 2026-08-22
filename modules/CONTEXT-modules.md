# CONTEXT-modules.md — Reusable Building Block Modules Context

## Overview
`modules/` contains vetted, cloud-native Terraform modules used by [`core/generation/synthesizer.py`](../core/generation/synthesizer.py) and registered in [`core/generation/modules.py`](../core/generation/modules.py) to construct production-ready data pipelines and cloud infrastructure in **MinusOps**.

---

## Module Catalog Summary

| Module Directory | Category | Primary Function | Key Files |
| :--- | :--- | :--- | :--- |
| [`compaction-glue`](./compaction-glue/main.tf) | Storage / Maintenance | Scheduled S3 small-file compaction via Glue PySpark jobs | [`main.tf`](./compaction-glue/main.tf), [`scripts/compact.py`](./compaction-glue/scripts/compact.py) |
| [`compute-databricks-delta`](./compute-databricks-delta/main.tf) | Compute / Storage | Unity Catalog External Locations and Delta Sharing grants over S3 Gold lake | [`main.tf`](./compute-databricks-delta/main.tf) |
| [`compute-emr-serverless`](./compute-emr-serverless/main.tf) | Compute | EMR Serverless Spark application on Graviton ARM64 for sustained Spark workloads | [`main.tf`](./compute-emr-serverless/main.tf) |
| [`compute-emr-ec2-spot`](./compute-emr-ec2-spot/main.tf) | Compute | EMR on EC2 3-fleet Graviton Spot task capacity for the 5+ TB/day tier | [`main.tf`](./compute-emr-ec2-spot/main.tf) |
| [`compute-glue-etl`](./compute-glue-etl/main.tf) | Compute | AWS Glue ETL Spark jobs with job bookmarking & failure alerts | [`main.tf`](./compute-glue-etl/main.tf), [`scripts/etl.py`](./compute-glue-etl/scripts/etl.py) |
| [`consumption-redshift-serverless`](./consumption-redshift-serverless/main.tf) | Analytics | Redshift Serverless workgroup & namespace for high-concurrency BI | [`main.tf`](./consumption-redshift-serverless/main.tf) |
| [`databricks-workspace`](./databricks-workspace/main.tf) | Compute / Analytics | Databricks E2 customer VPC workspace, Unity Catalog metastore & SEC-05 cross-account IAM | [`main.tf`](./databricks-workspace/main.tf), [`PROVENANCE.json`](./databricks-workspace/PROVENANCE.json) |
| [`dq-great-expectations`](./dq-great-expectations/main.tf) | Data Quality | Data quality validation gates using Great Expectations on AWS Glue | [`main.tf`](./dq-great-expectations/main.tf) |
| [`governance-observability`](./governance-observability/main.tf) | Governance | AWS Budgets, CloudWatch Billing Alarms, and SNS notification alerts | [`main.tf`](./governance-observability/main.tf) |
| [`ingest-firehose`](./ingest-firehose/main.tf) | Ingestion | Kinesis Data Firehose micro-batch ingestion stream to S3 Bronze zone | [`main.tf`](./ingest-firehose/main.tf) |
| [`ingestion-appflow`](./ingestion-appflow/main.tf) | Ingestion | SaaS pulls (Salesforce/Zendesk/ServiceNow) into Bronze via Amazon AppFlow | [`main.tf`](./ingestion-appflow/main.tf) |
| [`ingestion-dms`](./ingestion-dms/main.tf) | Ingestion | Database CDC (RDS / on-premise) into Bronze via AWS DMS, credentials by Secrets Manager reference | [`main.tf`](./ingestion-dms/main.tf) |
| [`ingestion-sftp`](./ingestion-sftp/main.tf) | Ingestion | Partner file drops via AWS Transfer Family, one chrooted role per user | [`main.tf`](./ingestion-sftp/main.tf) |
| [`ingestion-webhook`](./ingestion-webhook/main.tf) | Ingestion | HTTPS event receiver: API Gateway to SQS with a DLQ, no Lambda in the path | [`main.tf`](./ingestion-webhook/main.tf) |
| [`metadata-control-table`](./metadata-control-table/main.tf) | Orchestration | Fallback DynamoDB pipeline control table for dynamic DAG parameters; primary path reads an existing enterprise table via a column mapping | [`main.tf`](./metadata-control-table/main.tf), [`scripts/fetch_pipeline_config.py`](./metadata-control-table/scripts/fetch_pipeline_config.py) |
| [`networking-vpc`](./networking-vpc/main.tf) | Networking | Multi-AZ customer VPC with NAT Gateways, default SG & VPC Endpoints | [`main.tf`](./networking-vpc/main.tf), [`PROVENANCE.json`](./networking-vpc/PROVENANCE.json) |
| [`orchestrator-mwaa`](./orchestrator-mwaa/main.tf) | Orchestration | Managed Workflows for Apache Airflow (MWAA) environment in private VPC with KMS & log streams | [`main.tf`](./orchestrator-mwaa/main.tf) |
| [`orchestrator-stepfunctions`](./orchestrator-stepfunctions/main.tf) | Orchestration | AWS Step Functions state machine workflow orchestrator | [`main.tf`](./orchestrator-stepfunctions/main.tf) |
| [`governance-lakeformation`](./governance-lakeformation/main.tf) | Governance | Lake Formation LF-TBAC: row filters and PII column masking on Gold, with the `IAMAllowedPrincipals` compatibility default revoked | [`main.tf`](./governance-lakeformation/main.tf) |
| [`security-iam-scoped`](./security-iam-scoped/main.tf) | Governance | Least-privilege consumer access: scoped S3/KMS/Athena reads, external-ID trust for cross-account | [`main.tf`](./security-iam-scoped/main.tf) |
| [`dbt-semantic-layer`](./dbt-semantic-layer/main.tf) | Serving | Code-native governed metrics (dbt / MetricFlow) plus a versioned manifest bucket | [`main.tf`](./dbt-semantic-layer/main.tf), [`models/`](./dbt-semantic-layer/models/) |
| [`cube-semantic-layer`](./cube-semantic-layer/main.tf) | Serving | Headless semantic layer with a pre-aggregation cache and an encrypted Redis store | [`main.tf`](./cube-semantic-layer/main.tf), [`cube/`](./cube-semantic-layer/cube/) |
| [`query-athena`](./query-athena/main.tf) | Query Engine | Athena workgroup, scan limit cutoff, encrypted results bucket & Iceberg maintenance | [`main.tf`](./query-athena/main.tf), [`iceberg_maintenance.tf`](./query-athena/iceberg_maintenance.tf) |
| [`schema-registry-glue`](./schema-registry-glue/main.tf) | Governance | AWS Glue Schema Registry for Avro data contracts and compatibility rules | [`main.tf`](./schema-registry-glue/main.tf) |
| [`speed-layer-kinesis`](./speed-layer-kinesis/main.tf) | Streaming | Kinesis Data Streams speed layer with optional Apache Flink application | [`main.tf`](./speed-layer-kinesis/main.tf) |
| [`storage-medallion-s3`](./storage-medallion-s3/main.tf) | Storage | Tiered S3 Medallion Lake (Bronze/Silver/Gold) with KMS CMK & Glacier lifecycle | [`main.tf`](./storage-medallion-s3/main.tf) |
| [`streaming-msk-kafka`](./streaming-msk-kafka/main.tf) | Streaming | Managed Apache Kafka cluster (AWS MSK) with IAM SASL auth & multi-AZ distribution | [`main.tf`](./streaming-msk-kafka/main.tf) |
| [`table-format-iceberg`](./table-format-iceberg/main.tf) | Storage Format | Apache Iceberg table format v2 definition in Glue Catalog | [`main.tf`](./table-format-iceberg/main.tf) |
| [`warehouse-snowflake-aws`](./warehouse-snowflake-aws/main.tf) | Warehouse | Snowflake on AWS storage integration, SEC-05 external ID handshake & Snowpipe SQS queues | [`main.tf`](./warehouse-snowflake-aws/main.tf) |

---

## Detailed Module Specifications

### 1. `compaction-glue`
- **Files**:
  - [`modules/compaction-glue/main.tf`](./compaction-glue/main.tf)
  - [`modules/compaction-glue/scripts/compact.py`](./compaction-glue/scripts/compact.py)
- **Architectural Role**: Solves the S3 "small-files problem" in data lake zones (where thousands of small files degrade Athena scan speeds by 62-88% and cause S3 throttling) by running a scheduled Glue PySpark job to coalesce Parquet objects to ~128 MB.
- **Provisioned Resources**:
  - `aws_iam_role.compact`: Glue execution role (`glue.amazonaws.com`).
  - `aws_iam_role_policy.compact`: Least-privilege policy allowing S3 read/write/delete on target buckets, script bucket access, and CloudWatch log creation (`/aws-glue/*`).
  - `aws_s3_object.script`: Uploads `scripts/compact.py` to `var.script_s3_bucket`.
  - `aws_glue_job.compact`: Glue 4.0 PySpark ETL job (`glueetl`) with `--job-bookmark-option = job-bookmark-disable`.
  - `aws_glue_trigger.schedule`: Scheduled trigger running the compaction job.
- **Inputs**:
  - `name_prefix` (string, required): Prefix for resource naming.
  - `tags` (map(string), default `{}`): Resource tags.
  - `script_s3_bucket` (string, required): S3 bucket storing `compact.py`.
  - `target_buckets` (list(string), required): Target S3 data lake buckets to compact.
  - `schedule` (string, default `"cron(0 3 * * ? *)"`): Cron expression for execution schedule.
  - `worker_type` (string, default `"G.1X"`): Glue worker type.
  - `number_of_workers` (number, default `2`): Worker count.
- **Outputs**:
  - `compaction_job_name`: Name of the created Glue compaction job.
- **Scripts**:
  - [`scripts/compact.py`](./compaction-glue/scripts/compact.py): PySpark script that parses `--target_buckets`, calculates target partition count based on `TARGET_OBJECT_MB = 128`, reads Parquet prefixes, and rewrites them into `_compacted/` locations.

---

### 2. `compute-emr-serverless`
- **Files**:
  - [`modules/compute-emr-serverless/main.tf`](./compute-emr-serverless/main.tf)
- **Architectural Role**: Serverless Spark compute engine for sustained, heavy transformation workloads (>= TB/day scale) where EMR Serverless pricing is superior to Glue per-minute premiums. Includes auto-stop idle configuration.
- **Provisioned Resources**:
  - `aws_emrserverless_application.spark`: EMR Serverless application running Spark (`emr-7.5.0` default), capped maximum vCPU/memory, and 15-minute idle auto-stop.
  - `aws_iam_role.runtime`: Execution role assumed by `emr-serverless.amazonaws.com`.
  - `aws_iam_role_policy.runtime`: S3 read/write permissions for target data lake buckets.
- **Inputs**:
  - `name_prefix` (string, required): Naming prefix.
  - `tags` (map(string), default `{}`): Resource tags.
  - `release_label` (string, default `"emr-7.5.0"`): EMR release version.
  - `max_vcpu` (string, default `"16 vCPU"`): Maximum concurrent vCPU limit.
  - `max_memory` (string, default `"64 GB"`): Maximum concurrent memory limit.
  - `target_buckets` (list(string), default `[]`): Data lake buckets granted S3 read/write permissions.
- **Outputs**:
  - `application_id`: ID of the EMR Serverless Spark application.
  - `runtime_role_arn`: ARN of the IAM runtime role.

---

### 3. `compute-glue-etl`
- **Files**:
  - [`modules/compute-glue-etl/main.tf`](./compute-glue-etl/main.tf)
  - [`modules/compute-glue-etl/scripts/etl.py`](./compute-glue-etl/scripts/etl.py)
- **Architectural Role**: Managed AWS Glue 4.0 Spark batch ETL job provisioning. Features job bookmarking for incremental processing (Well-Architected Analytics Lens BP10) and optional EventBridge failure alerting.
- **Failure Modes closed (2026-08-17 live run)**: the job previously exited `SystemExit` because `--source_path`/`--target_path` were never set (MINUS-109), and 403'd on its first write to Silver because the role held no S3 write or KMS data-key grant (MINUS-108). Both are now wired by the synthesizer; leaving `data_buckets`/`source_bucket`/`target_bucket` empty reproduces the old behavior by design, for operators driving the module by hand.
- **Provisioned Resources**:
  - `aws_iam_role.glue`: Execution role for Glue service.
  - `aws_iam_role_policy.glue`: Access permissions for the script S3 bucket, CloudWatch logs, and (MINUS-108) two conditional `dynamic "statement"` blocks: `DataLake` (`s3:GetObject/PutObject/DeleteObject/ListBucket` scoped to `var.data_buckets`, never `"*"`) and `LakeKey` (`kms:Decrypt/GenerateDataKey/DescribeKey` on `var.kms_key_arn`). Emitted only when the matching input is non-empty, so a standalone use of the module produces no empty-resource statement.
  - `aws_s3_object.script` (`for_each = var.jobs`): Uploads `scripts/etl.py` for each declared job.
  - `aws_glue_job.this` (`for_each = var.jobs`): Glue ETL jobs. `default_arguments` is a `merge()` of `--job-bookmark-option = job-bookmark-enable` plus (MINUS-109) `--source_path = "s3://${var.source_bucket}/data/"` and `--target_path = "s3://${var.target_bucket}/data/"`. Both are omitted when their bucket input is empty, so an unwired module never emits a malformed `s3:///data/`.
  - `aws_cloudwatch_event_rule.glue_failed` (count-based): EventBridge rule catching Glue `FAILED`, `TIMEOUT`, or `STOPPED` job states.
  - `aws_cloudwatch_event_target.glue_failed_sns` (count-based): EventBridge target routing failure events to an SNS alert topic.
- **Inputs**:
  - `name_prefix` (string, required): Resource name prefix.
  - `tags` (map(string), default `{}`): Tag map.
  - `script_s3_bucket` (string, required): Bucket name for Glue scripts.
  - `jobs` (map(string), default `{}`): Map of `job_name => script_s3_key`.
  - `data_buckets` (list(string), default `[]`): Medallion bucket **names** the job reads and writes. Names, not ARNs, to match `dq-great-expectations`' `target_buckets` so both wire from `values(module.storage_medallion_s3.bucket_names)`.
  - `kms_key_arn` (string, default `""`): Lake CMK. Without `kms:GenerateDataKey` on it the job 403s writing to an SSE-KMS bucket even with the S3 actions allowed.
  - `source_bucket` (string, default `""`): Bucket the starter job reads (bronze).
  - `target_bucket` (string, default `""`): Bucket the starter job writes (silver).
  - `source_format` (string, default `"json"`) / `target_format` (string, default `"parquet"`): Spark reader/writer formats, passed as `--source_format`/`--target_format`. **Declared, never inferred.** `scripts/etl.py` previously chose the reader with `spark.read.parquet(p) if p.endswith("/") else spark.read.json(p)`, so the wired Bronze path (`s3://<bronze>/data/`, raw JSON) was read as Parquet and the job died on its first read -- the failure that remained after MINUS-108/109 fixed the startup crash.
  - `worker_type` (string, default `"G.1X"`): Worker type.
  - `number_of_workers` (number, default `2`): Number of workers per job.
  - `alarm_sns_topic_arn` (string, default `""`): SNS topic ARN for job failure notifications.
  - `enable_alarms` (bool, default `false`): Enable/disable creation of failure EventBridge rule.
- **Outputs**:
  - `glue_job_names`: Map of job key to generated Glue job name.
  - `glue_job_arns`: List of created Glue job ARNs.
  - `glue_role_arn`: ARN of the Glue execution role.
- **Scripts**:
  - [`scripts/etl.py`](./compute-glue-etl/scripts/etl.py): PySpark starter ETL script. Reads `--source_path`, `--target_path`, `--source_format`, `--target_format` via `getResolvedOptions`, then `spark.read.format(source_format).load(...)` -> passthrough transform -> `write.format(target_format).save(...)`. Still raises `SystemExit` when the paths are unset, so an unwired job fails loudly rather than silently no-op'ing; the formats fall back to `json`/`parquet`.

---

### 3a. `compute-emr-ec2-spot` (MINUS-128)
- **Files**: [`modules/compute-emr-ec2-spot/main.tf`](./compute-emr-ec2-spot/main.tf)
- **Architectural Role**: the top tier of the compute matrix. Use **only above ~5 TB/day** -- below that EMR Serverless costs less all-in once cluster idle time and operational burden are counted, and Glue costs less again.
- **The three-fleet split is the whole design**, and using fleets rather than groups is what makes it possible:
  - `master_instance_fleet` -> **on demand**. A lost master kills the cluster; the saving on one node is rounding error against re-running a day of batch.
  - `core_instance_fleet` -> **on demand**. Core nodes carry HDFS blocks, so reclaiming one loses shuffle data and forces a recompute -- Spot here trades a large risk for a small saving.
  - `aws_emr_instance_fleet.task` -> **Spot**, diversified. Task nodes hold no persistent data, so an interruption costs one re-executed task. This is where the ~70% saving comes from.
- **Diversification is enforced, not suggested**: `task_instance_types` carries a `validation` requiring at least 3. Each instance-type-and-AZ pair is one Spot pool; a single type is a single pool and one reclaim event takes the whole fleet. `allocation_strategy = "capacity-optimized"` then picks the deepest pools -- `lowest-price` would maximise interruptions.
- `weighted_capacity` is derived from the instance size via `regex`, so target capacity counts vCPUs and mixing a 2xlarge with a 4xlarge does not silently double the cluster.
- **`auto_termination_policy` defaults to 1 hour, not never.** A forgotten multi-TB cluster is the most expensive mistake this module can make.
- IAM data access is written inline rather than attached from the AWS managed EMR instance policy, which grants S3 far more broadly than one pipeline's own buckets (SEC-02).
- Related tier changes: `compute-glue-etl` gained `execution_class` (STANDARD/FLEX, validated) and `compute-emr-serverless` gained `architecture`, defaulting to **ARM64/Graviton** since the JVM runs on it unchanged and it is materially cheaper per vCPU-hour.

---

### 4. `consumption-redshift-serverless`
- **Files**:
  - [`modules/consumption-redshift-serverless/main.tf`](./consumption-redshift-serverless/main.tf)
- **Architectural Role**: Provision Redshift Serverless for high-concurrency BI analytics and complex data warehousing workloads where interactive query engines (like Athena) reach concurrency limits.
- **Provisioned Resources**:
  - `aws_redshiftserverless_namespace.this`: Logical Redshift Serverless database namespace (`${var.name_prefix}-analytics`).
  - `aws_redshiftserverless_workgroup.this`: Compute workgroup (`${var.name_prefix}-bi`) with configurable base RPU capacity.
- **Inputs**:
  - `name_prefix` (string, required): Resource name prefix.
  - `tags` (map(string), default `{}`): Tags map.
  - `base_capacity_rpu` (number, default `8`): Redshift Processing Units (RPU) base compute capacity.
  - `publicly_accessible` (bool, default `false`): Enable/disable public IP access.
- **Outputs**:
  - `namespace_name`: Name of the Redshift Serverless namespace.
  - `workgroup_name`: Name of the Redshift Serverless workgroup.

---

### 5. `databricks-workspace`
- **Files**:
  - [`modules/databricks-workspace/main.tf`](./databricks-workspace/main.tf)
  - [`modules/databricks-workspace/PROVENANCE.json`](./databricks-workspace/PROVENANCE.json)
- **Architectural Role**: Deploys a Databricks E2 workspace on AWS in a customer-managed VPC (VPC ID, subnets, security group supplied from `networking-vpc`). Configures AWS S3 root storage, Databricks cross-account IAM role with external ID validation (`SEC-05`), Unity Catalog metastore & assignment, optional Unity Catalog catalog, and optional SQL Warehouse.
- **Provisioned Resources**:
  - `aws_s3_bucket.root_storage_bucket`: Root S3 storage bucket (`${var.name_prefix}-dbx-root`).
  - `aws_s3_bucket_versioning.root_storage_bucket`: S3 versioning disabled.
  - `aws_s3_bucket_server_side_encryption_configuration.root_storage_bucket`: SSE-S3 (`AES256`).
  - `aws_s3_bucket_public_access_block.root_storage_bucket`: Blocks all public access.
  - `aws_s3_bucket_policy.root_storage_bucket`: S3 policy generated via `databricks_aws_bucket_policy`.
  - `aws_iam_role.cross_account_role`: IAM cross-account role with trust policy generated by `databricks_aws_assume_role_policy` (passing `external_id = var.databricks_account_id`).
  - `aws_iam_role_policy.cross_account_role`: IAM policy generated by `databricks_aws_crossaccount_policy`.
  - `databricks_mws_credentials.this`: MWS credentials object referencing the IAM role.
  - `databricks_mws_storage_configurations.this`: MWS storage configuration for the root S3 bucket.
  - `databricks_mws_networks.this`: MWS network configuration consuming VPC, subnets, and security groups.
  - `databricks_mws_workspaces.this`: Databricks E2 workspace.
  - `databricks_metastore.this` (count-based): Unity Catalog metastore (created if `existing_metastore_id` is empty).
  - `databricks_metastore_assignment.this`: Binds the metastore to the workspace.
  - `databricks_catalog.this` (count-based): Optional named Unity Catalog catalog.
  - `databricks_sql_endpoint.this` (count-based): Optional Databricks SQL Warehouse endpoint with auto-stop.
- **Inputs**:
  - `name_prefix` (string, required): Naming prefix.
  - `tags` (map(string), default `{}`): Tag map.
  - `databricks_account_id` (string, required): Databricks account ID.
  - `vpc_id` (string, required): Customer VPC ID from `networking-vpc`.
  - `subnet_ids` (list(string), required): Private subnet IDs from `networking-vpc`.
  - `security_group_ids` (list(string), required): Security group IDs from `networking-vpc`.
  - `existing_metastore_id` (string, default `""`): Existing Unity Catalog metastore ID to reuse.
  - `catalog_name` (string, default `""`): Unity Catalog catalog name to create (empty = skip).
  - `create_sql_warehouse` (bool, default `false`): Enable creation of SQL Warehouse.
  - `sql_warehouse_cluster_size` (string, default `"2X-Small"`): SQL Warehouse cluster size.
  - `sql_warehouse_auto_stop_mins` (number, default `10`): Idle timeout minutes before auto-stopping SQL Warehouse.
- **Outputs**:
  - `workspace_id`: Databricks workspace ID.
  - `workspace_url`: Databricks workspace URL.
  - `metastore_id`: ID of the assigned Unity Catalog metastore.
  - `catalog_name`: Name of created catalog (or null).
  - `sql_warehouse_id`: ID of created SQL Warehouse (or null).
  - `sql_warehouse_jdbc_url`: JDBC URL of created SQL Warehouse (or null).
- **Provenance**:
  - [`PROVENANCE.json`](./databricks-workspace/PROVENANCE.json): Tracks version 3 schema validation against `databricks/databricks >= 1.0` (confirmed against provider v1.121.0), documenting provider_config account-level patterns.

---

### 6. `dq-great-expectations`
- **Files**:
  - [`modules/dq-great-expectations/main.tf`](./dq-great-expectations/main.tf)
- **Architectural Role**: Automated data quality validation gate using Great Expectations running on AWS Glue (Python-shell). Stores validation results and Data Docs in a dedicated, lifecycle-managed S3 bucket.
- **Provisioned Resources**:
  - `aws_s3_bucket.results`: Dedicated S3 bucket for DQ results and Data Docs, suffixed with AWS Account ID and `md5(var.run_id)` to prevent collisions.
  - `aws_s3_bucket_public_access_block.results`: Blocks public ACLs/policies.
  - `aws_s3_bucket_lifecycle_configuration.results`: 90-day expiration rule for old validation results (`COST-01`).
  - `aws_iam_role.dq`: Glue execution role (`glue.amazonaws.com`).
  - `aws_iam_role_policy.dq`: IAM policy permitting S3 read on target data buckets and write on results bucket.
  - `aws_glue_job.dq`: Python 3.9 Glue Python-shell job (`pythonshell`) executing the Great Expectations runner script.
- **Inputs**:
  - `name_prefix` (string, required): Naming prefix.
  - `tags` (map(string), default `{}`): Resource tags.
  - `target_buckets` (list(string), required): List of data lake buckets to evaluate.
  - `fail_on_error` (bool, default `true`): Flag to halt pipeline execution on quality validation failure.
  - `script_s3_bucket` (string, required): S3 bucket containing the runner script.
  - `script_s3_key` (string, default `"scripts/great_expectations_runner.py"`): S3 key for runner script.
  - `run_id` (string, default `""`): MinusOps run ID used for unique bucket suffix hashing.
- **Outputs**:
  - `dq_job_name`: Name of the Glue data quality job.
  - `dq_results_bucket`: Name of the S3 bucket storing validation results.

---

### 7. `governance-observability`
- **Files**:
  - [`modules/governance-observability/main.tf`](./governance-observability/main.tf)
- **Architectural Role**: Cross-cutting governance guardrail providing real-time FinOps budget enforcement, CloudWatch billing metric alarms, and SNS alert notifications across pipeline deployments.
- **Provisioned Resources**:
  - `aws_sns_topic.alerts` (count-based): Created if `alarm_sns_topic_arn` is not supplied.
  - `aws_sns_topic_subscription.email` (`for_each`): Subscribes configured notification emails to the SNS topic.
  - `aws_budgets_budget.monthly`: Monthly AWS Cost budget with an 80% threshold breach alert trigger.
  - `aws_cloudwatch_metric_alarm.spend`: CloudWatch metric alarm on `AWS/Billing` (`EstimatedCharges`) triggering when monthly budget is exceeded.
  - **SIEM audit trail (MINUS-131), all gated on `local.siem_enabled` = `enable_siem_trail && length(siem_data_bucket_arns) > 0`:**
    - `aws_cloudtrail.siem`: Multi-region trail with log-file validation and an `advanced_event_selector` scoped to `eventCategory = Data` / `AWS::S3::Object` / `resources.ARN starts_with` the lake buckets. **Data events, not management events** -- management events tell you a bucket was created, not who read the objects in it.
    - `aws_s3_bucket.audit` + public-access block, versioning, SSE, lifecycle, and `aws_s3_bucket_policy.audit` (the `AWSCloudTrailAclCheck` / `AWSCloudTrailWrite` pair CloudTrail requires).
    - **`force_destroy = false` unconditionally, in every environment** -- an audit trail an operator can delete by re-running destroy is not an audit trail. Asserted by `tests/test_promotion_matrix.py`.
    - `aws_s3_bucket_object_lock_configuration.audit` uses **GOVERNANCE, not COMPLIANCE** mode: COMPLIANCE cannot be shortened or removed by anyone including root for the full window, which has stranded more teams than it has caught. GOVERNANCE blocks ordinary deletion but leaves an audited break-glass path.
    - **Off by default:** S3 data events are billed per event, and on a busy pipeline that is a real, volume-proportional bill. The synthesizer pre-wires `siem_data_bucket_arns` and `siem_kms_key_arn` so enabling it is a one-line tfvars change, but never flips the flag.
- **Inputs**:
  - `name_prefix` (string, required): Naming prefix.
  - `tags` (map(string), default `{}`): Tag map.
  - `monthly_budget_usd` (number, default `100`): Monthly budget limit in USD.
  - `alarm_sns_topic_arn` (string, default `""`): Optional external SNS topic ARN for alerts.
  - `notification_emails` (list(string), default `[]`): List of email addresses to receive alerts.
  - `enable_siem_trail` (bool, default `false`): Provision the CloudTrail data-event trail.
  - `siem_data_bucket_arns` (list(string), default `[]`): Bucket ARNs whose object-level access is audited. Wired by the synthesizer from the medallion zones.
  - `siem_kms_key_arn` (string, default `""`): CMK for the audit bucket and trail; empty falls back to SSE-S3.
  - `siem_retention_days` (number, default `365`): Retention window, applied to both the lifecycle rule and the Object Lock default.
- **Outputs**:
  - `budget_name`: Name of the created AWS Budget.
  - `alerts_topic_arn`: Effective SNS topic ARN used for notifications.
  - `siem_audit_bucket` / `siem_trail_arn`: Audit bucket id and trail ARN, or `""` when the trail is disabled.

---

### 8. `ingest-firehose`
- **Files**:
  - [`modules/ingest-firehose/main.tf`](./ingest-firehose/main.tf)
- **Architectural Role**: Micro-batch streaming ingestion into the S3 Bronze lake layer via Kinesis Data Firehose. Uses high buffering limits (64 MB / 300 s) to batch incoming events into ~128 MB scan-friendly objects and compress them using GZIP.
- **Provisioned Resources**:
  - `aws_iam_role.firehose`: IAM role assumed by `firehose.amazonaws.com`.
  - `aws_iam_role_policy.firehose`: IAM policy permitting S3 put/get/list operations on the landing bucket.
  - `aws_kinesis_firehose_delivery_stream.this`: Kinesis Firehose delivery stream configured for `extended_s3` with timestamp-partitioned prefixes (`streaming/ingest_date=!{timestamp:yyyy-MM-dd}/`) and error prefixes (`streaming-errors/`).
- **Inputs**:
  - `name_prefix` (string, required): Naming prefix.
  - `tags` (map(string), default `{}`): Tag map.
  - `destination_bucket_arn` (string, required): S3 landing (bronze) bucket ARN.
  - `buffering_size_mb` (number, default `64`): Buffer size limit in MB before delivery.
  - `buffering_interval_seconds` (number, default `300`): Buffer interval limit in seconds before delivery.
- **Outputs**:
  - `delivery_stream_name`: Name of the Kinesis Firehose delivery stream.
  - `delivery_stream_arn`: ARN of the delivery stream.

---

### 9. `networking-vpc`
- **Files**:
  - [`modules/networking-vpc/main.tf`](./networking-vpc/main.tf)
  - [`modules/networking-vpc/PROVENANCE.json`](./networking-vpc/PROVENANCE.json)
- **Architectural Role**: Foundation networking module providing a multi-AZ customer VPC with public/private subnet pairs, Internet Gateway, shared or per-AZ NAT Gateways, default self-referencing security group (for MWAA/Databricks), S3 Gateway VPC Endpoint (unconditional/free), and opt-in STS/Kinesis Interface VPC Endpoints.
- **Provisioned Resources**:
  - `aws_vpc.this`: Main VPC (`enable_dns_support = true`, `enable_dns_hostnames = true`).
  - `aws_internet_gateway.this`: Internet Gateway attached to VPC.
  - `aws_subnet.public` (count `az_count`): Public subnets with auto IP assignment.
  - `aws_subnet.private` (count `az_count`): Private subnets for compute workloads.
  - `aws_eip.nat` (count-based): Elastic IPs for NAT Gateways.
  - `aws_nat_gateway.this` (count-based): NAT Gateways (1 shared if `single_nat_gateway = true`, else `az_count`).
  - `aws_route_table.public` & `aws_route_table_association.public`: Public route table routing `0.0.0.0/0` to Internet Gateway.
  - `aws_route_table.private` & `aws_route_table_association.private`: Private route table(s) routing `0.0.0.0/0` to NAT Gateway(s).
  - `aws_default_security_group.this`: Manages default VPC security group with self-referencing ingress (all protocols) and open egress.
  - `aws_vpc_endpoint.s3`: Gateway VPC Endpoint for S3 attached to all route tables.
  - `aws_vpc_endpoint.sts` (count-based): Opt-in Interface VPC Endpoint for AWS STS.
  - `aws_vpc_endpoint.kinesis` (count-based): Opt-in Interface VPC Endpoint for Kinesis Streams.
- **Inputs**:
  - `name_prefix` (string, required): Naming prefix.
  - `tags` (map(string), default `{}`): Tag map.
  - `vpc_cidr` (string, default `"10.0.0.0/16"`): CIDR block for VPC.
  - `az_count` (number, default `2`): Number of AZs to span.
  - `single_nat_gateway` (bool, default `true`): Use 1 shared NAT gateway vs. 1 per AZ.
  - `enable_sts_endpoint` (bool, default `false`): Provision STS interface endpoint.
  - `enable_kinesis_endpoint` (bool, default `false`): Provision Kinesis interface endpoint.
- **Outputs**:
  - `vpc_id`: ID of the created VPC.
  - `private_subnet_ids`: List of private subnet IDs.
  - `public_subnet_ids`: List of public subnet IDs.
  - `default_security_group_id`: ID of default self-referencing security group.
  - `nat_gateway_ids`: List of NAT Gateway IDs.
- **Provenance**:
  - [`PROVENANCE.json`](./networking-vpc/PROVENANCE.json): Tracks version 1, content hash, live-testing validation from `runs/manual-mwaa-network-scratch` (15 resources created and destroyed cleanly).

---

### 10. `orchestrator-mwaa`
- **Files**:
  - [`modules/orchestrator-mwaa/main.tf`](./orchestrator-mwaa/main.tf)
- **Architectural Role**: Managed Workflows for Apache Airflow (MWAA) environment running inside customer private subnets for DAG-based pipeline orchestration.
- **Provisioned Resources**:
  - `aws_iam_role.mwaa`: MWAA execution role assumed by `airflow.amazonaws.com` and `airflow-env.amazonaws.com`.
  - `aws_iam_role_policy.mwaa`: IAM policy granting read access to DAG S3 bucket and CloudWatch log management.
  - `aws_mwaa_environment.this`: MWAA environment (`mw1.small` default, Airflow 2.8.1) configured with private subnets, security groups, DAG path (`dags`), and task/DAG processing logging enabled.
- **Inputs**:
  - `name_prefix` (string, required): Resource name prefix.
  - `tags` (map(string), default `{}`): Tag map.
  - `dag_s3_bucket_arn` (string, required): S3 bucket ARN containing Airflow DAGs.
  - `subnet_ids` (list(string), required): Two private subnet IDs in VPC.
  - `security_group_ids` (list(string), required): Security group IDs.
  - `airflow_version` (string, default `"2.8.1"`): Airflow runtime version.
  - `environment_class` (string, default `"mw1.small"`): MWAA instance class size.
- **Outputs**:
  - `airflow_environment`: Name of created MWAA environment.
  - `execution_role_arn`: Execution role ARN.

---

### 11. `orchestrator-stepfunctions`
- **Files**:
  - [`modules/orchestrator-stepfunctions/main.tf`](./orchestrator-stepfunctions/main.tf)
- **Architectural Role**: Serverless state machine orchestration for AWS workloads (Glue jobs, Lambda, EMR). Generates a sequential Glue job execution workflow in Amazon States Language (ASL) if no custom JSON definition is passed.
- **Provisioned Resources**:
  - `aws_iam_role.sfn`: Execution role assumed by `states.amazonaws.com`.
  - `aws_iam_role_policy.sfn`: IAM policy granting Glue job execution (`glue:StartJobRun`, `glue:GetJobRun`, `glue:BatchStopJobRun`).
  - `aws_sfn_state_machine.this`: AWS Step Functions state machine loaded with `effective_definition`.
  - `aws_cloudwatch_event_rule.schedule` / `aws_cloudwatch_event_target.sfn` / `aws_iam_role.events` / `aws_iam_role_policy.events` (all `count`-gated on `schedule_expression != ""`, MINUS-111): EventBridge schedule that starts the state machine. **EventBridge needs its own role** -- it cannot reuse the state machine's, whose trust policy names `states.amazonaws.com` -- so a separate role trusting `events.amazonaws.com` with `states:StartExecution` scoped to this one state machine is created alongside. Created only when a schedule is stated, so an event-driven pipeline is never handed a surprise cron.
- **Inputs**:
  - `name_prefix` (string, required): Naming prefix.
  - `tags` (map(string), default `{}`): Tag map.
  - `definition_json` (string, default `""`): Custom ASL JSON state machine definition. If empty, autogenerates sequential execution workflow for `glue_job_names`.
  - `glue_job_names` (list(string), default `[]`): List of Glue job names to orchestrate sequentially in starter workflow.
  - `task_role_arns` (list(string), default `[]`): Resource ARNs state machine can act upon.
  - `schedule_expression` (string, default `""`): EventBridge schedule, e.g. `"rate(1 day)"` or `"cron(0 3 * * ? *)"`. Empty means no schedule. **The synthesizer leaves this as a `REVIEW` item rather than defaulting to a cadence:** nothing in `requirements.json` states a batch schedule today, and inventing `rate(1 day)` would attach a real recurring cost to a pipeline nobody asked to run daily.
- **Outputs**:
  - `state_machine_arn`: ARN of created Step Functions state machine.
  - `role_arn`: Execution role ARN.
  - `schedule_rule_name`: Name of the EventBridge rule, or `""` when unscheduled.

---

### 12. `query-athena`
- **Files**:
  - [`modules/query-athena/main.tf`](./query-athena/main.tf)
- **Architectural Role**: Serving query layer for ad-hoc SQL analysts and BI tools (Tableau, PowerBI). Configures an Athena workgroup with mandatory per-query scan limits (10 GiB default) to prevent runaway costs, and a dedicated, lifecycle-managed query results S3 bucket.
- **Provisioned Resources**:
  - `aws_s3_bucket.results`: Dedicated query results bucket suffixed with AWS Account ID and `md5(var.run_id)`.
  - `aws_s3_bucket_public_access_block.results`: Blocks public access.
  - `aws_s3_bucket_lifecycle_configuration.results`: 30-day lifecycle expiration rule for query results.
  - `aws_athena_workgroup.this`: Athena workgroup (`${var.name_prefix}-analysts`) enforcing output location, KMS/SSE encryption, and `bytes_scanned_cutoff_per_query`.
  - `aws_glue_catalog_database.gold` (MINUS-110): The database Gold-zone tables live in, named `${replace(lower(var.name_prefix), "-", "_")}_gold` -- Glue database names allow only lowercase alphanumerics and underscores, so the hyphenated prefix is translated rather than passed through. `location_uri` is set from `var.gold_bucket` when wired, `null` otherwise (valid; table-level locations still work). **No table definitions are generated:** a table needs a real column schema, and an invented one fails on first query, which is worse than no table. Tables come from dbt models (`src/dbt/`), a CTAS, or a Glue crawler.
- **Inputs**:
  - `name_prefix` (string, required): Naming prefix.
  - `tags` (map(string), default `{}`): Tag map.
  - `results_kms_key_arn` (string, default `""`): Optional CMK ARN for result encryption (falls back to `SSE_S3`).
  - `bytes_scanned_cutoff` (number, default `10737418240` = 10 GiB): Per-query byte scan cap.
  - `run_id` (string, default `""`): MinusOps run ID for unique bucket suffix hashing.
  - `gold_bucket` (string, default `""`): Curated (Gold) bucket the catalog database points at.
- **Outputs**:
  - `workgroup_name`: Name of created Athena workgroup.
  - `results_bucket`: Name of S3 bucket holding query results.
  - `catalog_database`: Name of the Glue Data Catalog database. **`synthesizer.dbt_schema()` must return this same string** -- the generated `src/dbt/profiles.yml` uses it as dbt's `schema:`, and `tests/test_dbt_scaffold.py` asserts the two against this module's own HCL so they cannot drift.

---

### 13. `schema-registry-glue`
- **Files**:
  - [`modules/schema-registry-glue/main.tf`](./schema-registry-glue/main.tf)
- **Architectural Role**: Enforces data contracts and prevents downstream pipeline corruption by registering Avro schemas in AWS Glue Schema Registry with configurable evolution rules (`BACKWARD`, `FORWARD`, `FULL`).
- **Provisioned Resources**:
  - `aws_glue_registry.this`: AWS Glue Schema Registry (`${var.name_prefix}-registry`).
  - `aws_glue_schema.this` (`for_each = var.schemas`): Registers individual Avro schemas under the registry with specified compatibility mode.
- **Inputs**:
  - `name_prefix` (string, required): Naming prefix.
  - `tags` (map(string), default `{}`): Tag map.
  - `compatibility` (string, default `"BACKWARD"`): Schema evolution rule (`NONE`, `DISABLED`, `BACKWARD`, `BACKWARD_ALL`, `FORWARD`, `FORWARD_ALL`, `FULL`, `FULL_ALL`).
  - `schemas` (map(string), default `{}`): Map of `schema_name => Avro schema JSON string`.
- **Outputs**:
  - `registry_arn`: ARN of the Glue Schema Registry.
  - `schema_arns`: Map of schema key to schema ARN.

---

### 14. `speed-layer-kinesis`
- **Files**:
  - [`modules/speed-layer-kinesis/main.tf`](./speed-layer-kinesis/main.tf)
- **Architectural Role**: Real-time event streaming "speed layer" for Lambda/Kappa architectures using KMS-encrypted Kinesis Data Streams and optional Managed Service for Apache Flink applications.
- **Provisioned Resources**:
  - `aws_kinesis_stream.this`: KMS-encrypted Kinesis Data Stream (`alias/aws/kinesis`).
  - `aws_iam_role.flink` (count-based): IAM role assumed by `kinesisanalytics.amazonaws.com` if Flink is enabled.
  - `aws_iam_role_policy.flink` (count-based): Read policy on the Kinesis stream.
  - `aws_kinesisanalyticsv2_application.this` (count-based): Apache Flink application (`FLINK-1_18`) consuming ZIP code content from S3.
- **Inputs**:
  - `name_prefix` (string, required): Naming prefix.
  - `tags` (map(string), default `{}`): Tag map.
  - `shard_count` (number, default `1`): Number of stream shards.
  - `retention_hours` (number, default `24`): Data retention period in hours.
  - `enable_flink` (bool, default `false`): Provision Apache Flink stream processing application.
  - `flink_code_s3_bucket` (string, default `""`): S3 bucket containing Flink ZIP code.
  - `flink_code_s3_key` (string, default `""`): S3 key for Flink ZIP code.
- **Outputs**:
  - `stream_arn`: ARN of Kinesis Data Stream.
  - `stream_name`: Name of Kinesis Data Stream.

---

### 14a. Ingestion connectors (MINUS-123 to MINUS-125)

All four land data in the Bronze zone and are wired by the synthesizer with `target_bucket` and (except AppFlow, which writes through its own service role) `target_bucket_kms_key_arn` -- without the key grant the write 403s on an SSE-KMS bucket, the same failure MINUS-108 fixed for Glue.

**None of them takes a credential as a Terraform variable** (TerraShark FM-02). A password or token as a variable ends up in the plan and in state; each takes a Secrets Manager ARN or an out-of-band connector-profile name instead. [`tests/test_ingestion_modules.py`](../tests/test_ingestion_modules.py) asserts this across all four.

All four were validated against the **installed provider schema** (`terraform providers schema -json`, AWS v6.60.0), not the rendered registry docs. That caught two real breaks: `aws_dms_endpoint`'s `s3_settings` block was removed in provider v6 (the dedicated `aws_dms_s3_endpoint` resource replaces it), and `aws_appflow_flow`'s `connector_operator` fields are attributes rather than nested blocks.

#### `ingestion-dms`
- **Role**: CDC from an operational database (RDS, or on-premise Oracle/SAP over VPN/Direct Connect). Use when the requirement is "keep the lake in sync with the transactional system" rather than a periodic export.
- **Resources**: `aws_iam_role.dms` (+ policy), `aws_dms_replication_subnet_group.this`, `aws_dms_replication_instance.this`, `aws_dms_endpoint.source`, `aws_dms_s3_endpoint.target`, `aws_dms_replication_task.this`.
- **`publicly_accessible = false`**, asserted by test: a CDC instance reaching a production database has no reason to hold a public address, and DMS cannot be moved out of a public subnet after creation.
- Target writes **Parquet + GZIP**, not the CSV default: Bronze is read by Spark and Athena, and CSV costs a full scan per query plus loses the source's type information.
- `table_mappings_json` empty selects every schema and table -- correct for a first sync, wrong once the source has tables nobody agreed to replicate. Expressed as a `local` so that choice is visible in the plan.

#### `ingestion-appflow`
- **Role**: scheduled SaaS pulls. Preferred over a hand-rolled poller because AppFlow owns the pagination, retry, and rate-limit handling that a custom puller gets wrong first.
- The **connector profile is an input, not a resource**: a profile holds the OAuth grant for the SaaS tenant, so creating it in Terraform passes the credential through a plan and into state.
- `schedule_expression` **does** default here (unlike the orchestrator's), because a SaaS flow with no trigger never runs and there is no event to trigger it from.
- `NO_OP` field mapping with no field list copies the whole object: filtering at the ingestion boundary makes a mistake unrecoverable, because the original was never stored.

#### `ingestion-sftp`
- **Role**: managed SFTP for external partners who cannot call an API or assume a role -- the case VPC peering and PrivateLink do not cover.
- **One IAM role per user, scoped to `sftp/<user>/*`**, plus `home_directory_type = "LOGICAL"` chroot. A single shared role is the usual shortcut and the usual incident; a path traversal in a partner's client cannot walk into the rest of the bucket.
- SSH public keys only. Password auth would require a custom identity-provider Lambda holding partner credentials -- more attack surface than the problem justifies.

#### `ingestion-webhook`
- **Role**: HTTPS receiver for pushed events, buffered in SQS.
- **API Gateway integrates with SQS directly** (`integration_subtype = "SQS-SendMessage"`); a Lambda here would be a function whose whole body is `send_message`, plus a runtime to patch, a cold start per burst, and a per-invocation bill.
- `MessageBody = "$request.body"` enqueues the bytes verbatim -- the consumer needs exactly what the sender signed, and re-serializing would break every downstream signature check.
- DLQ (14-day retention) and stage throttling are not optional extras: without a DLQ one poison payload blocks the queue head until it ages out, and an unthrottled public endpoint is a billing denial-of-service.
- **HMAC verification is deliberately not implemented.** Every provider signs differently (Stripe's timestamped v1 scheme, GitHub's `X-Hub-Signature-256`, plain HMAC-SHA256); a generic verifier would be wrong for all of them. The module provisions an empty Secrets Manager container -- a secret with a Terraform-authored value is a secret in state -- and the consumer verifies before trusting the payload.

---

### 15. `storage-medallion-s3`
- **Files**:
  - [`modules/storage-medallion-s3/main.tf`](./storage-medallion-s3/main.tf)
- **Architectural Role**: Tiered S3 Data Lake (Bronze raw landing, Silver cleaned, Gold curated) with customer-managed KMS key (CMK) encryption, key rotation, block public access, bucket versioning, and Glacier lifecycle transition rules (`COST-01`).
- **Provisioned Resources**:
  - `aws_kms_key.lake`: Customer managed KMS key with 30-day deletion window and automated key rotation enabled. **No explicit `policy` (MINUS-112, decided 2026-08-18):** omitting it keeps AWS's default key policy, whose account-root delegation is what lets the IAM role policies in `compute-glue-etl`/`query-athena` grant KMS access at all. A service-principal-only policy is the classic CMK lockout and would not have fixed the observed 403 (a missing `kms:GenerateDataKey`, granted via IAM instead). Add service-principal statements with `kms:ViaService` conditions only for a service that cannot assume a role, and keep the root statement when doing so.
  - `aws_kms_alias.lake`: KMS alias `alias/${var.name_prefix}-${substr(md5(var.run_id), 0, 8)}-lake` (MINUS-102). A deleted CMK sits in `PendingDeletion` for 7-30 days but frees its alias immediately, so an unsuffixed alias collides on the next recreate; the suffix is the same run hash the buckets use.
  - `aws_s3_bucket.zone` (`for_each = toset(var.zones)`): Medallion S3 buckets suffixed with AWS Account ID and `md5(var.run_id)` hash, plus `force_destroy = var.force_destroy` (MINUS-101).
  - `aws_s3_bucket_public_access_block.zone` (`for_each`): Enforces full block on public ACLs and bucket policies.
  - `aws_s3_bucket_versioning.zone` (`for_each`): S3 versioning enabled.
  - `aws_s3_bucket_server_side_encryption_configuration.zone` (`for_each`): SSE-KMS encryption with KMS bucket key enabled.
  - `aws_s3_bucket_lifecycle_configuration.zone` (`for_each`): Transitions objects to `GLACIER` after `retention_days` (default 90).
  - **Disaster recovery (MINUS-132), gated on `replication_destination_bucket_arns` being non-empty:** `aws_iam_role.replication` + `aws_iam_role_policy.replication` + `aws_s3_bucket_replication_configuration.zone` (`for_each = local.replicated_zones`). The IAM policy grants **both** `kms:Decrypt` on the source key and `kms:Encrypt`/`kms:GenerateDataKey` on the destination key -- SSE-KMS replication silently stops without both -- and `source_selection_criteria.sse_kms_encrypted_objects` is enabled, without which encrypted objects are skipped entirely.
  - The destination is **per zone, a `map(string)`, not one shared bucket**: S3 replication preserves the object key exactly and cannot add a prefix, so three zones replicating into one destination would overwrite each other. Listing only the zones that matter (usually Gold alone) is the normal case. `tests/test_promotion_matrix.py` guards against a regression to a single-string ARN.
  - The module takes an **existing** destination rather than creating one: a cross-region bucket needs a second provider configuration that only the root module can supply, and enterprises commonly own the DR bucket in a separate account.
- **Inputs**:
  - `name_prefix` (string, required): Resource name prefix.
  - `tags` (map(string), default `{}`): Tag map.
  - `zones` (list(string), default `["bronze", "silver", "gold"]`): List of medallion storage zones.
  - `retention_days` (number, default `90`): Days before transitioning objects to S3 Glacier storage class.
  - `run_id` (string, default `""`): MinusOps run ID used for unique bucket name and KMS alias hashing.
  - `replication_destination_bucket_arns` (map(string), default `{}`): `zone => destination bucket ARN`. Empty disables replication. The destination must have versioning enabled or AWS rejects the configuration.
  - `replication_destination_kms_key_arn` (string, default `""`): CMK in the destination region used to re-encrypt replicas.
  - `multi_region_kms` (bool, default `false`): Create the lake CMK as a multi-region key. **Flipping this on an existing key REPLACES it** -- objects encrypted under the old key are not readable with the new one -- so it is a before-first-apply decision.
  - `force_destroy` (bool, default `false`): Allow `terraform destroy` to delete a non-empty bucket. Defaults to **false** so production data is never silently destroyable; the synthesizer emits `force_destroy = var.environment == "dev"`, expressed as the environment test rather than a literal so promoting the same Terraform to staging/prod flips it without an edit.
- **Outputs**:
  - `bucket_names`: Map of zone name to generated S3 bucket name.
  - `kms_key_arn`: ARN of customer-managed KMS key.

---

### 16. `table-format-iceberg`
- **Files**:
  - [`modules/table-format-iceberg/main.tf`](./table-format-iceberg/main.tf)
- **Architectural Role**: Apache Iceberg table format v2 provisioning in AWS Glue Catalog over S3 lake buckets (Gold/Curated zone), enabling ACID transactions, time-travel queries, snapshot isolation, and fast metadata scanning at 100 TB+ scale.
- **Provisioned Resources**:
  - `aws_glue_catalog_database.iceberg`: AWS Glue Catalog Database (`${var.name_prefix}_iceberg`).
  - `aws_glue_catalog_table.this`: Glue Catalog Table (`EXTERNAL_TABLE`) with `open_table_format_input.iceberg_input` (version 2) and dynamic column mapping.
- **Inputs**:
  - `name_prefix` (string, required): Naming prefix.
  - `tags` (map(string), default `{}`): Tag map.
  - `table_bucket` (string, required): S3 bucket name holding Iceberg data and metadata.
  - `table_name` (string, default `"curated_events"`): Name of the Iceberg catalog table.
  - `columns` (map(string), default `{ id = "string", event_time = "timestamp", payload = "string" }`): Map of `column_name => iceberg_data_type`.
- **Outputs**:
  - `database_name`: Name of created Glue Catalog Database.
  - `table_name`: Name of created Glue Catalog Table.
  - `table_location`: S3 location of Iceberg table (`s3://${var.table_bucket}/iceberg/${var.table_name}`).

---

### 17. `metadata-control-table` (Phase 3, PRD 6.8.4/6.8.5)
- **Files**:
  - [`modules/metadata-control-table/main.tf`](./metadata-control-table/main.tf)
  - [`modules/metadata-control-table/scripts/fetch_pipeline_config.py`](./metadata-control-table/scripts/fetch_pipeline_config.py)
- **Architectural Role**: dynamic Airflow/Step Function parameters (schedule, cluster size, worker count, timeout, status) read from a table at DAG-parse / pre-execution time instead of hardcoded in Python. **PRIMARY path is reading an EXISTING enterprise control table** -- any name, any column names -- through `fetch_pipeline_config.py`'s caller-supplied column mapping; that script never touches Terraform. This module is the **FALLBACK**: a DynamoDB table to provision only for a greenfield project with none yet. Nothing in `core/generation/modules.py`'s `_ENUMERABLE_FIELD_MODULES`/`derive_module_ids()` auto-selects it -- `match_modules()` can surface it, but composing it into a stack is an explicit choice, matching "opt-in, not the default." The corrected design deliberately diverges from the PRD's literal wording (a single MinusOps-owned schema named `tbl_pipeline_control_config`): an enterprise running metadata-driven pipelines already has this table under its own naming convention, and a rigid MinusOps schema would collide with it rather than integrate.
- **Provisioned Resources**:
  - `aws_dynamodb_table.control`: on-demand (`PAY_PER_REQUEST` default) table with `server_side_encryption` always enabled (optional customer CMK), `point_in_time_recovery` on by default, and a name suffixed `${name_prefix}-pipeline-control-${substr(md5(run_id), 0, 8)}` when `table_name` is left blank -- same run-hash collision guard `storage-medallion-s3`'s KMS alias uses, so two runs sharing a `name_prefix` don't collide. No GSIs (ponytail: add one only once a real access pattern beyond the primary key needs it).
- **Inputs**:
  - `name_prefix` (string, required), `tags` (map(string), default `{}`).
  - `run_id` (string, default `""`): folds into the default table name; ignored once `table_name` is set.
  - `table_name` (string, default `""`): explicit override to match an existing naming convention.
  - `partition_key_name` / `partition_key_type` (default `"feed_id"` / `"S"`) and `sort_key_name` / `sort_key_type` (default `""` / `"S"`, sort key omitted when blank): key attribute **names**, not just values, are inputs -- even the fallback table can be created under a company's own convention.
  - `billing_mode` (`"PAY_PER_REQUEST"` default or `"PROVISIONED"`, validated), `read_capacity` / `write_capacity` (used only when `PROVISIONED`).
  - `kms_key_arn` (string, default `""`): optional CMK; empty still encrypts under DynamoDB's AWS-owned default key.
  - `point_in_time_recovery` (bool, default `true`).
- **Outputs**: `table_name`, `table_arn`.
- **No IAM resource of any kind is provisioned here.** Readers (Airflow workers, Step Functions pre-execution steps) use their own existing execution role; this module never takes a credential as a variable, and any identity/access mapping a caller later adds to a control-table row must be an IAM role ARN or an Identity Center group id -- never a static access key. Asserted directly against the HCL (no `aws_iam_*` resource, no `Resource = "*"`) and the script (no credential parameter) by `tests/test_metadata_control_table.py`.
- **Runtime helper (`scripts/fetch_pipeline_config.py`)**: standalone stdlib-only script -- no `core/` imports, since it runs inside the customer's Airflow/Lambda environment, not this repo's control plane. Shells to the `aws` CLI (`aws dynamodb get-item`), the same technique `core/providers/aws.py` uses, rather than boto3. `parse_control_row(raw_item, column_map)` is the pure, unit-tested column-mapping indirection: `column_map` is `{normalized_key: caller's_own_column_name}`, so two enterprises' tables with completely different column names both resolve to the same normalized keys MinusOps' generated DAGs read. A mapped column absent from a row resolves to `None`, never a `KeyError` -- one stale/mid-migration column must not crash DAG parsing for every pipeline sharing the table. `fetch_control_row(table_name, key, column_map, region=None)` wraps that parser around the actual `get-item` call and returns `(row_or_None, error_string)`.
- **G5 disposition**: `aws_dynamodb_table` is reviewed into `core/governance/destructive_change_gate.py`'s `STATEFUL_RESOURCE_TYPES` -- it holds live pipeline-config rows every DAG queries at parse time, so a replace/recreate silently blanks every pipeline's schedule and cluster sizing. See that file's inline comment and `tests/test_destructive_change_gate.py::test_dynamodb_table_is_reviewed_stateful_not_unreviewed`.

---

## Third-Party Providers (ruling, 2026-08-21)

AWS-native modules are the core default. `databricks/databricks` is the one reviewed
exception and predates the ruling.

**Snowflake stays out of this catalog.** `warehouse-snowflake-aws` is deliberately the
AWS-side half of the handshake only -- an IAM role, its policy, and the Snowpipe SQS queue.
It declares no Snowflake provider, which is why it cannot set warehouse properties such as
`auto_suspend` even though PRD v3 asks for them. Adding the provider would introduce a
credential path into a catalog whose modules take secret ARNs rather than credentials
(FM-02), plus another provider version to track.

Snowflake-side resources are authored per-engagement as a registry-composed module through
the [`architect`](../.agents/skills/architect/SKILL.md) skill. `tests/test_modules.py` fails
if a new third-party provider appears here without amending its reviewed allowlist.

---

## Governance & Provenance Tracking
Every module in `modules/` is validated against security rules in [`core/reporting/optimize_analyzer.py`](../core/reporting/optimize_analyzer.py). Upstream dependencies and provider schema compatibility are tracked via [`PROVENANCE.json`](./databricks-workspace/PROVENANCE.json) files to ensure content hash integrity against infrastructure drift.

---

### `governance-lakeformation` (PRD v8 FR-01A)

- **Purpose:** Tag-based access control on the Gold zone -- row filters and column-level PII
  masking enforced for Athena and EMR.
- **Why tags, not direct grants:** a grant per (principal, database, table, column) is O(n*m)
  rows nobody prunes. Six months in, "who can read PII" needs a script. A tag grant is one row
  per (principal, tag value): tag a new column `Confidentiality=PII` and every existing grant
  applies immediately.
- **THE footgun this module exists to avoid.** Lake Formation ships in a compatibility mode
  where `IAMAllowedPrincipals` holds ALL on every new database and table. While that is true,
  IAM alone still opens the data and **every LF-Tag grant is bypassed** -- the console shows
  the tags attached, queries keep working, and nothing indicates the governance layer is
  inert. The empty `create_database_default_permissions {}` and
  `create_table_default_permissions {}` blocks are what actually turn LF-TBAC on. Deleting
  them looks like a simplification and silently disables the module; a test asserts they are
  present AND empty.
- **`consumer_tag_values` defaults to `["Public"]`,** never PII. A default that granted PII
  would be a module that quietly widened access on apply.
- **Validations:** at least one administrator (an unadministered lake is unmanageable) and at
  least one LF-Tag (tag-based control with no tags grants nothing and blocks nothing).

### `security-iam-scoped` (PRD v8 FR-01B)

- **Purpose:** least-privilege read access to Gold for BI and data-science consumers,
  including cross-account.
- **No `Resource = "*"`.** Every statement names the Gold bucket ARN, the CMK ARN, or the
  workgroup it was given. A least-privilege module with a wildcard is a wildcard with a
  reassuring name. (`gold_prefixes = ["*"]` is a KEY prefix inside one named bucket -- a
  different thing, and the test distinguishes them.)
- **No trust policy without an external ID.** A cross-account role naming only the peer
  account is the confused-deputy problem: role ARNs appear in logs, error messages and
  support tickets, so any principal in that account who learns this one can assume it. The
  combination `trusted_external_principals` set + `external_id` empty fails the PLAN via a
  `precondition`, so the defect is not expressible rather than merely discouraged.
- **KMS is issued with the read grant.** `s3:GetObject` on a CMK-encrypted object returns
  AccessDenied that names S3, not KMS -- one of the longer debugging sessions in this stack.

### `dbt-semantic-layer` / `cube-semantic-layer` (PRD v8 FR-02)

- **The deliverable is the scaffold**, not the infrastructure. `models/*.yml` and
  `cube/schema/*.js` are what the domain team edits after `minusctl export`; the Terraform is
  the manifest bucket and the pre-aggregation cache. Provisioning a dbt Cloud account or an
  EKS service from here would put credentials in a control plane that holds none, and would
  create a cluster MinusOps could not safely destroy.
- **Which one:** dbt compiles SQL against the warehouse per request; Cube adds a
  pre-aggregation cache, which is the entire reason to run a separate service. A Cube
  deployment with no pre-aggregations is a proxy that re-scans the lake on every dashboard
  refresh and costs more than the thing it fronts -- so a test asserts the schema declares
  one.
- **Both must agree on a metric.** `netRevenue` in the Cube schema and `net_revenue` in
  `metrics.yml` are the same number; two definitions of one metric is the problem a semantic
  layer exists to remove.
- **`partitionGranularity` and `+partitioned_by` must match the projected partition key**
  (`event_date`, see `query-athena`). Mismatched, every rollup rebuild scans the whole table
  and the cache costs more than having none.
