import os
import re
import json
import argparse

def strip_comments(text):
    # Remove single line comments starting with # or //
    text = re.sub(r'(#|//).*', '', text)
    # Remove multi-line comments starting with /* and ending with */
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    return text

def scan_hcl_files(source_dir):
    findings = []
    
    # Generic, multi-service optimization rules using strict HCL syntax regexes
    rules = [
        {
            "id": "SEC-01",
            "category": "Security",
            "title": "S3 Public Access Block Missing",
            "description": "Every S3 bucket should have a corresponding aws_s3_bucket_public_access_block resource to prevent accidental exposure.",
            "severity": "HIGH",
            "detect": lambda content: "aws_s3_bucket" in content and "aws_s3_bucket_public_access_block" not in content
        },
        {
            "id": "SEC-02",
            "category": "Security",
            "title": "Wildcard IAM Policy Permissions",
            "description": "IAM policy statements should restrict resources and actions; avoid wildcard '*' resource permissions.",
            "severity": "MEDIUM",
            "detect": lambda content: re.search(r'"Resource"\s*=\s*"\*"', content) is not None
        },
        {
            "id": "SEC-03",
            "category": "Security",
            "title": "Unencrypted Redshift Cluster",
            "description": "Redshift clusters must be encrypted at rest (encrypted = true) to secure business data warehouses.",
            "severity": "HIGH",
            "detect": lambda content: "aws_redshift_cluster" in content and re.search(r'\bencrypted\s*=\s*true\b', content) is None
        },
        {
            "id": "SEC-04",
            "category": "Security",
            "title": "Unencrypted MSK Topics",
            "description": "Amazon MSK clusters should enforce TLS encryption in-transit and KMS encryption at rest.",
            "severity": "HIGH",
            "detect": lambda content: "aws_msk_cluster" in content and "encryption_info" not in content
        },
        {
            "id": "COST-01",
            "category": "Cost",
            "title": "S3 Bucket Missing Lifecycle Policy",
            "description": "S3 Buckets should configure lifecycle rules (aws_s3_bucket_lifecycle_configuration) to transition logs/old data to standard-IA or Glacier.",
            "severity": "MEDIUM",
            "detect": lambda content: "aws_s3_bucket" in content and "aws_s3_bucket_lifecycle_configuration" not in content
        },
        {
            "id": "COST-02",
            "category": "Cost",
            "title": "Databricks Cluster Missing Auto-Termination",
            "description": "Databricks clusters should configure autotermination_minutes (default: 20 mins) to prevent billing for idle compute.",
            "severity": "HIGH",
            "detect": lambda content: "databricks_cluster" in content and re.search(r'\bautotermination_minutes\s*=\s*\d+', content) is None
        },
        {
            "id": "COST-03",
            "category": "Cost",
            "title": "EMR Cluster Lacks Spot Instance Pricing",
            "description": "EMR task instances should use Spot pricing models (bid_price) to cut processing costs by up to 90%.",
            "severity": "MEDIUM",
            "detect": lambda content: "aws_emr_cluster" in content and re.search(r'\bbid_price\s*=\s*', content) is None
        },
        {
            "id": "OBS-01",
            "category": "Observability",
            "title": "Missing CloudWatch Alarms",
            "description": "No CloudWatch alarms (aws_cloudwatch_metric_alarm) are configured to alert on job failures or timeouts.",
            "severity": "MEDIUM",
            "detect": lambda content: "aws_cloudwatch_metric_alarm" not in content
        }
    ]

    hcl_files = []
    for root, _, files in os.walk(source_dir):
        for file in files:
            if file.endswith(".tf"):
                hcl_files.append(os.path.join(root, file))
                
    # Concatenate all content to run checks
    all_content = ""
    for file_path in hcl_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                all_content += f.read() + "\n"
        except Exception:
            pass

    # Strip comments to prevent false negatives/positives from text comments
    clean_content = strip_comments(all_content)

    for rule in rules:
        if rule["detect"](clean_content):
            findings.append({
                "id": rule["id"],
                "category": rule["category"],
                "title": rule["title"],
                "description": rule["description"],
                "severity": rule["severity"]
            })
            
    return findings

def generate_report(findings, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, "optimization_report.md")
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 🔍 AWS Infrastructure Optimization Report\n\n")
        f.write("This report presents structural recommendations for your AWS data infrastructure scanned by the `pipeline-optimizer` engine.\n\n")
        
        if not findings:
            f.write("🎉 **No issues detected!** Your infrastructure aligns with all scanned best practices.\n")
            return
            
        f.write("| ID | Category | Severity | Issue | Recommendation |\n")
        f.write("| :--- | :--- | :--- | :--- | :--- |\n")
        
        for fnd in findings:
            f.write(f"| {fnd['id']} | **{fnd['category']}** | `{fnd['severity']}` | **{fnd['title']}** | {fnd['description']} |\n")
            
        f.write("\n\n*Review these recommendations inside your `agy` control panel to generate auto-remediation plans.*")
        
    print(f"[OPTIMIZER] Report successfully generated at: {report_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Infrastructure Scanner & Optimizer")
    parser.add_argument("--source-dir", required=True, help="Directory containing Terraform configs to scan")
    parser.add_argument("--log-dir", default=os.path.join(os.getcwd(), ".agents", "logs"), help="Logs output directory")
    
    args = parser.parse_args()
    findings = scan_hcl_files(args.source_dir)
    generate_report(findings, args.log_dir)
