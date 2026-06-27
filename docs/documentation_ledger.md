# AWS & Terraform Documentation Navigational Ledger

This ledger describes the layout, structural patterns, and query heuristics for cloud developer portals. It outlines how to bypass manual UI clicking by constructing direct URL patterns and leveraging programmatic API interfaces.

---

## 🛠️ 1. Terraform Registry (AWS Provider)
* **Website URL**: `https://registry.terraform.io/providers/hashicorp/aws/latest/docs`
* **What it Contains**: Every HCL resource block schema, required/optional parameters, return attributes, and connection details.
* **The Manual UI Bottleneck**: The website is a Single-Page App (SPA). Finding resources manually requires expanding a nested, lazy-loaded left sidebar, scrolling through hundreds of services, and selecting target blocks.

### 🚀 Direct URL Construction Shortcuts (Bypassing Clicks)
You can jump directly to any resource or data source by typing or requesting these path formats:
* **Resources (Creations)**:
  `https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/<resource_type_without_aws_prefix>`
  * *Example (S3 Bucket)*: [aws_s3_bucket](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket)
  * *Example (Glue Job)*: [aws_glue_job](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/glue_job)
* **Data Sources (Lookups)**:
  `https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/<data_source_type_without_aws_prefix>`
  * *Example (Caller ID)*: [aws_caller_identity](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/caller_identity)

---

## 💻 2. AWS CLI v2 Command Reference
* **Website URL**: `https://awscli.amazonaws.com/v2/documentation/api/latest/index.html`
* **What it Contains**: Parameter flags, argument values, and JSON schema structures for every command line action.
* **The Manual UI Bottleneck**: The index page lists all AWS services. Finding a command manually requires clicking the service name, then searching the command list, and clicking the individual action page.

### 🚀 Direct URL Construction Shortcuts (Bypassing Clicks)
AWS CLI v2 maintains a highly predictable static HTML structure. You can map any terminal command to its documentation page using this formula:
* **Pattern**: `https://awscli.amazonaws.com/v2/documentation/api/latest/reference/<service>/<action>.html`
* **Translation Examples**:
  * Command: `aws s3api head-bucket` &rarr; [s3api/head-bucket.html](https://awscli.amazonaws.com/v2/documentation/api/latest/reference/s3api/head-bucket.html)
  * Command: `aws glue start-job-run` &rarr; [glue/start-job-run.html](https://awscli.amazonaws.com/v2/documentation/api/latest/reference/glue/start-job-run.html)
  * Command: `aws stepfunctions start-execution` &rarr; [stepfunctions/start-execution.html](https://awscli.amazonaws.com/v2/documentation/api/latest/reference/stepfunctions/start-execution.html)

---

## 💵 3. AWS Pricing Calculator
* **Website URL**: `https://calculator.aws/`
* **What it Contains**: Estimator pages where you select regions, service tiers, instance types, and durations.
* **The Manual UI Bottleneck**: The site requires logging in anonymously, clicking "Add Service", typing search terms, selecting custom configurations, dragging sliders, and clicking "Save". This cannot be automated or crawled easily due to complex JavaScript layouts.

### 🚀 Programmatic Query Alternatives (The Real Source of Truth)
To extract pricing without clicking the web interface, developers can use these options:
1. **AWS CLI Pricing Command**:
   Use the `pricing` service client in terminal commands to query exact rates for resources:
   ```bash
   aws pricing get-products --service-code "AWSGlue" --filters "Type=TERM_MATCH,Field=productFamily,Value=Compute" --region us-east-1
   ```
2. **AWS Price List JSON API**:
   AWS publishes raw JSON indices for all pricing offers. You can fetch these directly to parse costs programmatically:
   * **Main Pricing Offer Index**: `https://pricing.us-east-1.amazonaws.com/offers-v1.0/aws/index.json`
   * **Target Service Index (e.g. Glue)**: `https://pricing.us-east-1.amazonaws.com/offers-v1.0/aws/AWSGlue/current/index.json`
