# Official Cloud, CLI, and IaC Information Library

This document compiles official documentation, best practices, pricing catalogs, and
resource links used to build, secure, and govern cloud infrastructure. Use it as the
first redirect target before relying on memory or general web search.

---

## 1. Developer Actionability & Usability Ranking

| Rank | Documentation Portal | Actionability Score | Core Strength & Use Case |
| :--- | :--- | :--- | :--- |
| **#1** | **[Terraform Registry / Provider Docs](https://registry.terraform.io/browse/providers)** | **10 / 10** | **Implementation Accelerator**: provider discovery, copy-pasteable syntax blocks, required/optional arguments lookup, and parameter dependencies. |
| **#2** | **[Terraform CLI Reference](https://developer.hashicorp.com/terraform/cli)** | **9 / 10** | **State & Lifecycle Management**: Essential commands for resolving state locks, running plans, importing resources, and CLI configurations. |
| **#3** | **[AWS CLI Command Reference](https://docs.aws.amazon.com/cli/latest/)** | **8 / 10** | **Live Debugging**: The fastest way to verify resource states, test connections (`head-bucket`), and run queries directly from the terminal. |
| **#4** | **[AWS Developer Guides (Glue, SFN, S3)](https://docs.aws.amazon.com/index.html)** | **7 / 10** | **Logical Integration**: Crucial for deep-dives (e.g. PySpark optimizations, Spark OOM error logs, Step Functions SDK parameters). |
| **#5** | **[AWS Pricing Calculator](https://calculator.aws/)** | **6 / 10** | **Cost & Budget Planning**: Modeling estimates and computing DPU or storage tier costs. |
| **#6** | **[HashiCorp Well-Architected Framework](https://developer.hashicorp.com/well-architected-framework)** / **[AWS Well-Architected Framework](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html)** | **5 / 10** | **Design & Security Audit**: Guidance on secure architecture, VPC layout isolation, IAM permissions, and structural patterns. |

---

## 2. Main AWS Documentation Portals
* **[AWS Documentation Home Portal](https://docs.aws.amazon.com/)**: The central index for all official AWS services, developer guides, API references, and user guides.
* **[AWS General Reference Guide](https://docs.aws.amazon.com/general/latest/gr/)**: Information on AWS service endpoints, regions, quotas, and service limits.
* **[AWS Architecture Center](https://aws.amazon.com/architecture/)**: Blueprints, reference architectures, and design patterns.
* **[AWS Well-Architected Framework Guide](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html)**: Key design principles across Security, Reliability, Performance, Cost, and Operational Excellence.
* **[AWS Solutions Library](https://aws.amazon.com/solutions/)**: Vetted architectural templates and deployable solutions.
* **[AWS Pricing Catalog](https://aws.amazon.com/pricing/)**: Main page for billing and pricing models of all AWS services.

---

## 3. AWS CLI v2 Reference
* **[AWS CLI latest docs](https://docs.aws.amazon.com/cli/latest/)**: Official landing page for the AWS CLI command reference. Check the page's displayed CLI version against the installed `aws --version` before relying on command shape.
* **[AWS CLI v2 User Guide](https://docs.aws.amazon.com/cli/latest/userguide/)**: Documentation covering configuration profiles, credential setups, output formats, and CLI usage.
* **[AWS CLI v2 Command Reference Portal](https://awscli.amazonaws.com/v2/documentation/api/latest/index.html)**: The direct API reference index containing arguments and usage examples for all commands in CLI v2.
* **[AWS CLI v2 SSO Configuration Guide](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html)**: Guide on configuring IAM Identity Center (SSO) login credentials (`aws configure sso`) in v2.
* **[AWS CLI v2 Interactive Prompts Guide](https://docs.aws.amazon.com/cli/latest/userguide/cli-usage-parameters-prompting.html)**: Manual for using interactive auto-prompts (`--cli-auto-prompt`) to format shell actions dynamically.
* **[AWS CLI v2 Installation Reference](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html)**: Guide on configuring CLI executables across different operating systems.

---

## 4. Terraform CLI Reference
* **[Terraform CLI Documentation](https://developer.hashicorp.com/terraform/cli)**: Official manual explaining configuration flags, settings, and CLI shells.
* **[Terraform CLI Commands Reference](https://developer.hashicorp.com/terraform/cli/commands)**: API reference for core commands (init, plan, apply, destroy, state, workspace, import).
* **[Terraform Installation Reference](https://developer.hashicorp.com/terraform/install)**: OS-specific packages and installation guides for Terraform.

---

## 5. Infrastructure as Code (IaC)
* **[Terraform Provider Registry Browser](https://registry.terraform.io/browse/providers)**: Official provider discovery index. Use it when a request names a provider that is not yet in this library.
* **[Terraform AWS Provider Docs](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)**: Official configuration parameters for provisioning S3, Glue, SQS, Step Functions, and CloudWatch.
* **[Terraform Best Practices Guide](https://www.terraform-best-practices.com/)**: Standard naming conventions, layout configurations, and state security architectures.

---

## 6. AWS Managed Data Services
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

## 7. Orchestration & Analytics
* **[AWS Step Functions Developer Guide](https://docs.aws.amazon.com/step-functions/latest/dg/welcome.html)**
* **[Step Functions Design Patterns & Best Practices](https://docs.aws.amazon.com/step-functions/latest/dg/bp-best-practices.html)**: Error catching, task retries, Express workflow pricing structures, and monitoring.
* **[AWS Athena Performance Tuning](https://docs.aws.amazon.com/athena/latest/ug/performance-tuning.html)**: Optimizing partition projection, bucketing, and query structures for cost reduction.

---

## 8. Security and Architecture Frameworks
* **[HashiCorp Well-Architected Framework](https://developer.hashicorp.com/well-architected-framework)**: HashiCorp guidance for secure, reliable, and maintainable infrastructure workflows.

---

## 9. Source Discovery Rules for New Providers and CLIs
When a user asks for a provider, cloud, or CLI that is not already listed here:

1. Prefer the vendor's official documentation domain and official CLI reference.
2. Check the locally installed tool version first (`aws --version`, `terraform version`,
   `az version`, `gcloud version`, or the provider lock file when present).
3. Prefer docs matching the installed major/minor version. If only `latest` docs are
   available, state that the docs are latest and note the local version used.
4. Add the official source link here and add a direct URL pattern to
   [`documentation_ledger.md`](./documentation_ledger.md) if the portal has predictable
   resource, data-source, or command URLs.
5. Avoid community posts unless official docs are missing or insufficient; label them as
   non-authoritative when used.
