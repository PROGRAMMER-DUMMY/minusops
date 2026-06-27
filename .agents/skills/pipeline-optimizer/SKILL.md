---
name: pipeline-optimizer
description: Scans and optimizes existing AWS data pipeline infrastructures for security, cost, performance, triggers, and observability, supporting Databricks, Glue, EMR, and Redshift integrations.
---

# AWS Pipeline & Databricks Infrastructure Optimizer

This skill equips `agy` with checklists, heuristics, and scripts to scan existing customer AWS architecture (Glue, Databricks, EMR, Athena, Redshift, S3) and apply target optimizations across critical operational domains.

---

## 🛠️ Optimization Scenarios & Auditing Checklist

Whenever analyzing an existing AWS data architecture, you must evaluate the following 6 vectors:

### 1. Security & Compliance
* **IAM Privilege Overreach**: Check for wildcard (`*`) resources in S3 access, Glue databases, or KMS key actions.
* **Public exposure**: Verify that all S3 buckets block public access (`block_public_acls = true`).
* **Unencrypted Data**: Look for S3 buckets without SSE configurations or KMS keys without rotation.
* **Network Isolation**: Ensure Glue Connections and Databricks workspaces run inside private subnets with VPC endpoints for S3, instead of traversing the public internet.

### 2. Cost Control & Unnecessary Services
* **Over-Provisioned Compute**: Find EMR/Databricks clusters running on standard instances instead of using **Spot Instances** for task nodes and **Graviton (ARM)** instances (e.g. `m6g` vs `m5`).
* **Idling Clusters**: Check if clusters lack auto-scaling policies or auto-termination limits (e.g. Databricks auto-terminate after 20 mins of inactivity).
* **S3 Storage Bloat**: Find buckets missing Lifecycle configuration policies.
* **Service Redundancy**: Audit cases where EMR, Glue, and Lambda are chained redundantly (e.g. running a Glue Spark Job just to copy files that a simple S3 event or S3 DistCP could handle).

### 3. Performance & Speed (Spark / SQL Tuning)
* **The "Small File Problem"**: If S3 contains thousands of KB-sized files, Spark read times degrade. Advise introducing **compaction / file coalescing** (e.g., `coalesce` or `repartition` in Glue/Databricks).
* **Missing Partitioning**: Ensure tables are queried using partition filters (e.g. by `date` or `region`) to prevent full S3 scans.
* **Athena Performance**: Ensure queries query Parquet/ORC columns instead of raw CSV/JSON.

### 4. Event-Driven Triggers
* **Polling vs Events**: Replace time-based CRON schedules (polling S3 every 10 mins) with S3 ObjectCreated events via **EventBridge** or **AWS Lambda**.

### 5. Observability & Auditability
* **Missing Alerts**: Verify existence of CloudWatch alarms for step failures, Glue job timeouts, or EMR node failures.
* **Execution Logs**: Ensure Continuous Logging is enabled on Glue/Databricks and sent to CloudWatch Logs with a retention limit (e.g. 14 days, not infinite).
* **Lineage Tracking**: Ensure OpenLineage or AWS Glue Catalog data lineage is enabled to track data flows.

---

## 🚀 Execution Workflow
1. Execute the optimizer scanner script:
   ```bash
   python .agents/skills/pipeline-optimizer/scripts/optimize_analyzer.py --source-dir "./aws-medallion-pipeline"
   ```
2. Present the generated markdown recommendations report (`.agents/logs/optimization_report.md`) to the user.
3. Once approved by the user, prepare a terraform refactoring plan (`tfplan`) applying the recommended changes.
