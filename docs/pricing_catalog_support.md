# Pricing Catalog and BCM Support

MinusOps does not hardcode service prices, usage quantities, or data-pipeline-specific
service rows.

Cost evidence is split into two layers:

1. **Catalog lookup / mapping**
   - Resolve Terraform resource types and internal service names to AWS catalog fields.
   - Required BCM fields are `serviceCode`, `usageType`, `operation`, `usageAccountId`,
     and monthly `amount`.
   - Teams may supply a reviewed JSON usage profile with `--usage-profile`.

2. **BCM estimate execution**
   - `core/cost/bcm_pricing_calculator.py prepare` writes reviewable payloads only. It makes
     no AWS calls.
   - `core/cost/bcm_pricing_calculator.py run` is an AWS-side effect. It routes through
     `approval.py`, creates the BCM workload estimate, adds usage lines, and reads the
     estimate result.

## Terraform `aws_pricing_product` comparison

Terraform's `aws_pricing_product` data source is useful for product/SKU lookup through
the AWS Price List catalog. It can help discover catalog attributes, but it does not
create a governed AWS Billing and Cost Management estimate for a Terraform plan.

MinusOps support is intentionally different:

- Terraform plan JSON is inspected to discover resource types and addresses.
- `bcm-usage.json` is generated with `REVIEW_REQUIRED` placeholders unless a reviewed
  usage profile is supplied.
- The project refuses to run the BCM estimate while placeholders remain.
- The final cost report should publish totals only after the BCM API returns evidence.

## Usage Profile Contract

An internal team can supply a JSON file like this:

```json
{
  "name": "reviewed-team-workload-profile",
  "usage": [
    {
      "serviceCode": "AmazonS3",
      "usageType": "USE1-ExampleUsage",
      "operation": "ExampleOperation",
      "key": "S3USAGE1",
      "group": "tf-us-east-1",
      "usageAccountId": "123456789012",
      "amount": 20
    }
  ]
}
```

The profile is reviewed evidence, not an engine default. If it contains placeholders,
`bcm_pricing_calculator.py run` fails closed.

Generated `bcm-usage.json` keeps only AWS BCM usage fields. Terraform resource addresses
and resource types are stored in `bcm-assumptions.json` under `usage_line_map`, keyed by
the short BCM `key` value.

## AWS CLI BCM Operation Groups

The local AWS CLI service model for `bcm-pricing-calculator` exposes these operation
groups:

- **Workload estimates:** create, update, delete, get, list workload estimates; batch
  create, update, delete usage; list workload estimate usage.
- **Bill scenarios:** create, update, delete, get, list bill scenarios; batch create,
  update, delete usage modifications; batch create, update, delete commitment
  modifications; list scenario usage and commitment modifications.
- **Bill estimates:** create, update, delete, get, list bill estimates; list estimate
  line items, commitments, input usage modifications, and input commitment
  modifications.
- **Preferences:** get and update pricing calculator preferences.
- **Tags:** list, tag, and untag calculator resources.
