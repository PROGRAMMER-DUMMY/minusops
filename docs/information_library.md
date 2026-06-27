# AWS Data Engineering & IaC Information Library

This document compiles the official documentation, best practices, pricing calculators, and resource links used to build, secure, and optimize AWS data pipelines.

---

## 🏆 1. Developer Actionability & Usability Ranking

| Rank | Documentation Portal | Actionability Score | Core Strength & Use Case |
| :--- | :--- | :--- | :--- |
| **#1** | **[Terraform Registry / Provider Docs](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)** | **10 / 10** | **Implementation Accelerator**: Copy-pasteable syntax blocks, required/optional arguments lookup, and parameter dependencies. |
| **#2** | **[Terraform CLI Reference](https://developer.hashicorp.com/terraform/cli)** | **9 / 10** | **State & Lifecycle Management**: Essential commands for resolving state locks, running plans, importing resources, and CLI configurations. |
| **#3** | **[AWS CLI v2 Command Reference](https://awscli.amazonaws.com/v2/documentation/api/latest/index.html)** | **8 / 10** | **Live Debugging**: The fastest way to verify resource states, test connections (`head-bucket`), and run queries directly from the terminal. |
| **#4** | **[AWS Developer Guides (Glue, SFN, S3)](https://docs.aws.amazon.com/index.html)** | **7 / 10** | **Logical Integration**: Crucial for deep-dives (e.g. PySpark optimizations, Spark OOM error logs, Step Functions SDK parameters). |
| **#5** | **[AWS Pricing Calculator](https://calculator.aws/)** | **6 / 10** | **Cost & Budget Planning**: Modeling estimates and computing DPU or storage tier costs. |
| **#6** | **[AWS Well-Architected Framework](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html)** | **5 / 10** | **Design & Security Audit**: Guidance on VPC layout isolation, network paths, IAM permissions, and structural patterns. |

---

## 🌐 2. Main AWS Documentation Portals
* **[AWS Documentation Home Portal](https://docs.aws.amazon.com/)**: The central index for all official AWS services, developer guides, API references, and user guides.
* **[AWS General Reference Guide](https://docs.aws.amazon.com/general/latest/gr/)**: Information on AWS service endpoints, regions, quotas, and service limits.
* **[AWS Architecture Center](https://aws.amazon.com/architecture/)**: Blueprints, reference architectures, and design patterns.
* **[AWS Well-Architected Framework Guide](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html)**: Key design principles across Security, Reliability, Performance, Cost, and Operational Excellence.
* **[AWS Solutions Library](https://aws.amazon.com/solutions/)**: Vetted architectural templates and deployable solutions.
* **[AWS Pricing Catalog](https://aws.amazon.com/pricing/)**: Main page for billing and pricing models of all AWS services.

---

## 💻 3. AWS CLI v2 Reference
* **[AWS CLI v2 User Guide](https://docs.aws.amazon.com/cli/latest/userguide/)**: Documentation covering configuration profiles, credential setups, output formats, and CLI usage.
* **[AWS CLI v2 Command Reference Portal](https://awscli.amazonaws.com/v2/documentation/api/latest/index.html)**: The direct API reference index containing arguments and usage examples for all commands in CLI v2.
* **[AWS CLI v2 SSO Configuration Guide](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html)**: Guide on configuring IAM Identity Center (SSO) login credentials (`aws configure sso`) in v2.
* **[AWS CLI v2 Interactive Prompts Guide](https://docs.aws.amazon.com/cli/latest/userguide/cli-usage-parameters-prompting.html)**: Manual for using interactive auto-prompts (`--cli-auto-prompt`) to format shell actions dynamically.
* **[AWS CLI v2 Installation Reference](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html)**: Guide on configuring CLI executables across different operating systems.

---

## ⚙️ 4. Terraform CLI Reference
* **[Terraform CLI Documentation](https://developer.hashicorp.com/terraform/cli)**: Official manual explaining configuration flags, settings, and CLI shells.
* **[Terraform CLI Commands Reference](https://developer.hashicorp.com/terraform/cli/commands)**: API reference for core commands (init, plan, apply, destroy, state, workspace, import).
* **[Terraform Installation Reference](https://developer.hashicorp.com/terraform/install)**: OS-specific packages and installation guides for Terraform.

---

## 🛠️ 5. Infrastructure as Code (IaC)
* **[Terraform AWS Provider Docs](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)**: Official configuration parameters for provisioning S3, Glue, SQS, Step Functions, and CloudWatch.
* **[Terraform Best Practices Guide](https://www.terraform-best-practices.com/)**: Standard naming conventions, layout configurations, and state security architectures.

---

## 📊 6. AWS Managed Data Services
### AWS Glue
* **[AWS Glue Developer Guide](https://docs.aws.amazon.com/glue/latest/dg/what-is-glue.html)**
* **[AWS Glue Spark Performance Tuning](https://docs.aws.amazon.com/glue/latest/dg/aws-glue-programming-etl-glue-optimization.html)**: Heuristics on DPU allocation, auto-scaling, and resolving Spark memory issues.

### Amazon EMR Serverless
* **[EMR Serverless User Guide](https://docs.aws.amazon.com/emr/latest/EMR-Serverless-User-Guide/what-is-emr-serverless.html)**
* **[EMR Serverless Operational Best Practices](https://docs.aws.amazon.com/emr/latest/EMR-Serverless-User-Guide/emr-serverless-best-practices.html)**: Optimizing application startup time, network setups, and worker allocation.

### AWS Databricks
* **[Databricks on AWS Home](https://docs.databricks.com/en/index.html)**
* **[Databricks security & workspace architecture](https://docs.databricks.com/en/administration-guide/index.html)**: VPC Peering, credential management, and workspace provisioning on AWS.

---

## 🎯 7. Orchestration & Analytics
* **[AWS Step Functions Developer Guide](https://docs.aws.amazon.com/step-functions/latest/dg/welcome.html)**
* **[Step Functions Design Patterns & Best Practices](https://docs.aws.amazon.com/step-functions/latest/dg/bp-best-practices.html)**: Error catching, task retries, Express workflow pricing structures, and monitoring.
* **[AWS Athena Performance Tuning](https://docs.aws.amazon.com/athena/latest/ug/performance-tuning.html)**: Optimizing partition projection, bucketing, and query structures for cost reduction.
