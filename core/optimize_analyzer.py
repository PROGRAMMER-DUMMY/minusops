"""
HCL security / cost / observability scanner — the gate's policy layer.

Per-resource analysis: rules that concern a specific resource (e.g. "every S3
bucket needs a public-access-block") are evaluated against each resource block, so
a directory with four buckets and one public-access-block correctly flags the three
unprotected buckets — the previous whole-file substring approach missed that.

Optionally merges findings from an external policy engine (checkov / tfsec / trivy)
when present on PATH, so an enterprise can layer its own ruleset on top. Native
SEC-* findings remain blocking; external findings are advisory unless promoted.
"""
import os
import re
import json
import argparse
import subprocess
import sys


BLOCKING_PREFIXES = ("SEC-",)


def strip_comments(text):
    text = re.sub(r'(#|//).*', '', text)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    return text


def _extract_block(content, open_brace_idx):
    """Return the text inside the {...} that starts at open_brace_idx (brace-matched)."""
    depth = 0
    for i in range(open_brace_idx, len(content)):
        c = content[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return content[open_brace_idx + 1:i]
    return content[open_brace_idx + 1:]


def resource_blocks(content):
    """Yield (type, name, body) for every `resource "type" "name" { ... }` block."""
    blocks = []
    for m in re.finditer(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', content):
        type_, name = m.group(1), m.group(2)
        body = _extract_block(content, m.end() - 1)
        blocks.append((type_, name, body))
    return blocks


def _finding(rule_id, category, title, description, severity, resource=None):
    f = {"id": rule_id, "category": category, "title": title,
         "description": description, "severity": severity}
    if resource:
        f["resource"] = resource
    return f


def _referenced_by(blocks, target_type, addr):
    """True if any block of target_type references `addr` (handles single + for_each)."""
    return any(addr in body for t, _n, body in blocks if t == target_type)


def scan_hcl_files(source_dir):
    """Return a list of findings. SEC-* findings block governed deployment."""
    content = ""
    for root, _dirs, files in os.walk(source_dir):
        for name in files:
            if name.endswith(".tf"):
                try:
                    with open(os.path.join(root, name), encoding="utf-8") as f:
                        content += f.read() + "\n"
                except OSError:
                    continue
    clean = strip_comments(content)
    blocks = resource_blocks(clean)
    findings = []

    # ---- Per-resource rules (this is the fix for whole-file false negatives) ----
    for rtype, name, body in blocks:
        addr = f"aws_s3_bucket.{name}"
        if rtype == "aws_s3_bucket":
            if not _referenced_by(blocks, "aws_s3_bucket_public_access_block", addr):
                findings.append(_finding(
                    "SEC-01", "Security", "S3 Public Access Block Missing",
                    "Every S3 bucket needs an aws_s3_bucket_public_access_block to prevent accidental exposure.",
                    "HIGH", resource=addr))
            if not _referenced_by(blocks, "aws_s3_bucket_lifecycle_configuration", addr):
                findings.append(_finding(
                    "COST-01", "Cost", "S3 Bucket Missing Lifecycle Policy",
                    "Configure aws_s3_bucket_lifecycle_configuration to transition or expire old data.",
                    "MEDIUM", resource=addr))
        elif rtype == "aws_redshift_cluster":
            if re.search(r'\bencrypted\s*=\s*true\b', body) is None:
                findings.append(_finding(
                    "SEC-03", "Security", "Unencrypted Redshift Cluster",
                    "Redshift clusters must set encrypted = true to secure data at rest.",
                    "HIGH", resource=f"aws_redshift_cluster.{name}"))
        elif rtype == "aws_msk_cluster":
            if "encryption_info" not in body:
                findings.append(_finding(
                    "SEC-04", "Security", "Unencrypted MSK Cluster",
                    "Amazon MSK clusters should declare encryption_info (TLS in-transit + KMS at rest).",
                    "HIGH", resource=f"aws_msk_cluster.{name}"))
        elif rtype == "databricks_cluster":
            if re.search(r'\bautotermination_minutes\s*=\s*\d+', body) is None:
                findings.append(_finding(
                    "COST-02", "Cost", "Databricks Cluster Missing Auto-Termination",
                    "Set autotermination_minutes so idle Databricks clusters stop billing.",
                    "HIGH", resource=f"databricks_cluster.{name}"))
        elif rtype == "aws_emr_cluster":
            if re.search(r'\bbid_price\s*=', body) is None:
                findings.append(_finding(
                    "COST-03", "Cost", "EMR Cluster Lacks Spot Instance Pricing",
                    "EMR task instances should use Spot pricing (bid_price) to cut cost.",
                    "MEDIUM", resource=f"aws_emr_cluster.{name}"))

    # ---- Whole-config rules (genuinely global) ----
    if re.search(r'["\']?Resource["\']?\s*[:=]\s*"\*"', clean):
        findings.append(_finding(
            "SEC-02", "Security", "Wildcard IAM Policy Permissions",
            "IAM statements should target specific resource ARNs; avoid Resource = \"*\".",
            "MEDIUM"))
    if blocks and "aws_cloudwatch_metric_alarm" not in clean:
        findings.append(_finding(
            "OBS-01", "Observability", "Missing CloudWatch Alarms",
            "No aws_cloudwatch_metric_alarm is configured to alert on failures or timeouts.",
            "MEDIUM"))

    return findings


# ---------------------------------------------------------------------------
# Optional external policy engines (checkov / tfsec / trivy). Advisory by default.
# ---------------------------------------------------------------------------
def run_external_scanners(source_dir):
    findings = []
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import toolpath  # local import keeps the core scanner dependency-free

    checkov = toolpath.find_tool("checkov")
    if checkov:
        try:
            res = subprocess.run([checkov, "-d", source_dir, "-o", "json", "--compact"],
                                 capture_output=True, text=True, timeout=120)
            data = json.loads(res.stdout or "{}")
            results = data.get("results", {}) if isinstance(data, dict) else {}
            for item in results.get("failed_checks", []):
                findings.append(_finding(
                    item.get("check_id", "CKV"), "External:checkov",
                    item.get("check_name", "checkov finding"),
                    f"{item.get('resource', '')} ({item.get('file_path', '')})",
                    "EXTERNAL", resource=item.get("resource")))
        except Exception as exc:
            print(f"[OPTIMIZER] checkov run skipped: {exc}", file=sys.stderr)

    tfsec = toolpath.find_tool("tfsec")
    if tfsec:
        try:
            res = subprocess.run([tfsec, source_dir, "-f", "json", "--no-color"],
                                 capture_output=True, text=True, timeout=120)
            data = json.loads(res.stdout or "{}")
            for item in (data.get("results") or []):
                findings.append(_finding(
                    item.get("rule_id", "TFSEC"), "External:tfsec",
                    item.get("description", "tfsec finding"),
                    (item.get("location", {}) or {}).get("filename", ""),
                    "EXTERNAL", resource=item.get("resource")))
        except Exception as exc:
            print(f"[OPTIMIZER] tfsec run skipped: {exc}", file=sys.stderr)

    return findings


def generate_report(findings, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, "optimization_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# AWS Infrastructure Optimization Report\n\n")
        f.write("Per-resource scan for security, cost, and observability gaps.\n\n")
        if not findings:
            f.write("**No issues detected.** Your infrastructure aligns with all scanned best practices.\n")
            return
        f.write("| ID | Category | Severity | Resource | Issue | Recommendation |\n")
        f.write("| :--- | :--- | :--- | :--- | :--- | :--- |\n")
        for fnd in findings:
            f.write(f"| {fnd['id']} | **{fnd['category']}** | `{fnd['severity']}` | "
                    f"`{fnd.get('resource', '-')}` | **{fnd['title']}** | {fnd['description']} |\n")
    print(f"[OPTIMIZER] Report successfully generated at: {report_path}")


def blocking_findings(findings):
    """Findings that must block governed deployment (native SEC-* only)."""
    return [f for f in findings if any(f["id"].startswith(p) for p in BLOCKING_PREFIXES)]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Infrastructure Scanner & Optimizer (per-resource)")
    parser.add_argument("--source-dir", required=True, help="Directory containing Terraform configs to scan")
    parser.add_argument("--log-dir", default=os.path.join(os.getcwd(), "artifacts", "review"),
                        help="Report output directory")
    parser.add_argument("--report-only", action="store_true",
                        help="Write the report but do not fail on blocking findings")
    parser.add_argument("--external", action="store_true",
                        help="Also run external policy engines (checkov/tfsec) if present; advisory")
    args = parser.parse_args(argv)

    findings = scan_hcl_files(args.source_dir)
    if args.external:
        findings = findings + run_external_scanners(args.source_dir)
    generate_report(findings, args.log_dir)

    blockers = blocking_findings(findings)
    if blockers and not args.report_only:
        ids = ", ".join(f"{b['id']}({b.get('resource', '-')})" for b in blockers)
        print(f"[OPTIMIZER] Blocking findings detected: {ids}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
