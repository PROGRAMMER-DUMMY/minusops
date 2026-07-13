"""
HCL security / cost / observability scanner — the gate's policy layer.

Per-resource analysis: rules that concern a specific resource (e.g. "every S3
bucket needs a public-access-block") are evaluated against each resource block, so
a directory with four buckets and one public-access-block correctly flags the three
unprotected buckets — the previous whole-file substring approach missed that.

Optionally merges findings from external policy engines (checkov / trivy) when
present on PATH. Native SEC-* findings always block; external findings are
advisory in dev mode and blocking in production policy mode.

G7 (tracked in HANDOFF.md's gate taxonomy): tfsec is retired in favor of Trivy's `config`
subcommand -- tfsec itself was archived upstream in favor of Trivy, which absorbed its
Terraform misconfiguration ruleset (aquasecurity/tfsec README: "tfsec is joining Trivy").
Running an archived scanner would mean this repo's own external-policy layer silently stops
receiving new checks/CVE-style rule updates -- the same "verifier that quietly stops verifying"
shape this session has repeatedly found and fixed elsewhere, just for a scanner instead of a
gate. Trivy's real JSON output shape (`Results[].Misconfigurations[]`) was verified live against
one of this repo's own real modules, not assumed from documentation, before writing the parser
below.
"""
import os
import re
import json
import argparse
import subprocess
import sys


BLOCKING_PREFIXES = ("SEC-",)
EXTERNAL_SCANNERS = ("checkov", "trivy")
SKIP_DIRS = {".terraform", ".git", "__pycache__", ".minus"}


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


def data_blocks(content):
    """Yield (type, name, body) for every `data "type" "name" { ... }` block."""
    blocks = []
    for m in re.finditer(r'data\s+"([^"]+)"\s+"([^"]+)"\s*\{', content):
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
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
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
        # ---- Data-pipeline performance/cost rules (WA Analytics Lens BP 10 + incremental) ----
        elif rtype == "aws_glue_job":
            if "job-bookmark" not in body:
                findings.append(_finding(
                    "DATA-01", "Performance", "Glue Job Without Job Bookmarks",
                    "Enable '--job-bookmark-option = job-bookmark-enable' so the job processes only "
                    "new/changed data. Full reloads are slow and costly (WA Analytics Lens BP 10; "
                    "Glue job bookmarks).",
                    "LOW", resource=f"aws_glue_job.{name}"))
        elif rtype == "aws_glue_catalog_table":
            if "partition_keys" not in body:
                findings.append(_finding(
                    "DATA-02", "Performance", "Glue Table Not Partitioned",
                    "Declare partition_keys so queries prune partitions instead of scanning whole "
                    "datasets (WA Analytics Lens BP 10.4 — partition for pruning).",
                    "LOW", resource=f"aws_glue_catalog_table.{name}"))
        elif rtype == "aws_athena_workgroup":
            if "bytes_scanned_cutoff" not in body:
                findings.append(_finding(
                    "DATA-03", "Cost", "Athena Workgroup Without Scan Cutoff",
                    "Set bytes_scanned_cutoff_per_query to cap runaway scan cost on unpartitioned or "
                    "large tables.",
                    "LOW", resource=f"aws_athena_workgroup.{name}"))

    # ---- Cross-account IAM trust policy shape (SEC-05) ----
    # Two distinct shapes, both cross-account trust policies, both checked here:
    #
    # 1. Hand-rolled `data "aws_iam_policy_document"` with a principals { type = "AWS" }
    #    block -- the shape a hand-authored cross-account role uses. Its HCL is static text,
    #    so we can check the actual condition/principal content directly.
    # 2. `data "databricks_aws_assume_role_policy"` -- the canonical generator the Databricks
    #    workspace module uses instead of hand-rolling JSON (docs/project_plan.md Phase E
    #    addendum). Its emitted policy JSON is computed inside the provider at plan/apply time
    #    -- invisible to a static-text scanner -- so this check is deliberately narrower: it
    #    only verifies the *input* the module author controls (external_id supplied), not the
    #    provider's output. This is intentional, not a shortcut: what actually protects against
    #    a malformed trust policy here is using the vetted data source at all; shape 1's check
    #    is the backstop for a future maintainer bypassing it and hand-rolling JSON again.
    #
    # Neither `terraform validate`/`plan` nor SEC-01..04 can catch a malformed one of these;
    # this rule exists specifically because that gap would otherwise let a missing external-ID
    # condition or an over-broad principal reach production undetected.
    for dtype, name, body in data_blocks(clean):
        if dtype == "databricks_aws_assume_role_policy":
            if not re.search(r'external_id\s*=', body):
                findings.append(_finding(
                    "SEC-05", "Security", "Databricks Cross-Account Trust Policy Missing External ID",
                    "data \"databricks_aws_assume_role_policy\" has no external_id argument -- "
                    "the generated trust policy only includes the external-ID condition when "
                    "external_id is supplied.",
                    "HIGH", resource=f"data.databricks_aws_assume_role_policy.{name}"))
            continue
        if dtype != "aws_iam_policy_document" or "sts:AssumeRole" not in body:
            continue
        if not re.search(r'type\s*=\s*"AWS"', body):
            continue
        addr = f"data.aws_iam_policy_document.{name}"
        if not re.search(r'sts:ExternalId', body, re.IGNORECASE):
            findings.append(_finding(
                "SEC-05", "Security", "Cross-Account Trust Policy Missing External ID",
                "A cross-account AssumeRole trust policy (principals type = \"AWS\") has no "
                "sts:ExternalId condition. Without one, the role can be assumed by anyone who "
                "later controls that principal ARN elsewhere -- the standard cross-account "
                "confused-deputy risk.",
                "HIGH", resource=addr))
        if re.search(r'identifiers\s*=\s*\[[^\]]*"\*"', body):
            findings.append(_finding(
                "SEC-05", "Security", "Cross-Account Trust Policy Has Wildcard Principal",
                "A cross-account AssumeRole trust policy grants identifiers = [\"*\"] instead "
                "of a specific account/role ARN, allowing any AWS principal to assume the role.",
                "HIGH", resource=addr))

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
# Optional external policy engines (checkov / trivy). Advisory in dev, blocking in production.
# ---------------------------------------------------------------------------
def _scanner_error(scanner, message, required=False):
    severity = "BLOCKING" if required else "EXTERNAL"
    return _finding(
        "POLICY-EXT", f"External:{scanner}", "External policy scanner unavailable",
        message, severity, resource=scanner)


def run_external_scanners(source_dir, required=False):
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
            msg = f"checkov run failed: {exc}"
            findings.append(_scanner_error("checkov", msg, required))
            print(f"[OPTIMIZER] {msg}", file=sys.stderr)

    trivy = toolpath.find_tool("trivy")
    if trivy:
        try:
            # `trivy config` exits non-zero whenever it finds real misconfigurations (confirmed
            # live -- exit 32 against a real module with genuine findings, valid JSON on stdout
            # regardless) -- this is Trivy's normal "findings present" signal, not a run
            # failure, so returncode is deliberately not checked here; only a genuinely
            # unparseable/absent stdout (subprocess raising, or malformed JSON) counts as
            # _scanner_error below.
            res = subprocess.run([trivy, "config", "-f", "json", source_dir],
                                 capture_output=True, text=True, timeout=120)
            data = json.loads(res.stdout or "{}")
            for result in (data.get("Results") or []):
                for item in (result.get("Misconfigurations") or []):
                    cause = item.get("CauseMetadata") or {}
                    findings.append(_finding(
                        item.get("ID", "TRIVY"), "External:trivy",
                        item.get("Title", "trivy finding"),
                        item.get("Message", item.get("Description", "")),
                        "EXTERNAL", resource=cause.get("Resource")))
        except Exception as exc:
            msg = f"trivy run failed: {exc}"
            findings.append(_scanner_error("trivy", msg, required))
            print(f"[OPTIMIZER] {msg}", file=sys.stderr)

    if required and not (checkov or trivy):
        findings.append(_scanner_error(
            "required",
            "Production policy mode requires at least one supported external scanner "
            "(checkov or trivy) on PATH.",
            required=True))

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


def blocking_findings(findings, external_blocking=False):
    """Findings that must block governed deployment.

    Native SEC-* findings always block. External scanner findings block only when
    production policy mode requires external evidence.
    """
    blockers = [f for f in findings if any(f["id"].startswith(p) for p in BLOCKING_PREFIXES)]
    if external_blocking:
        blockers.extend(
            f for f in findings
            if f.get("category", "").startswith("External:") and f not in blockers
        )
    return blockers


def _policy_mode(value=None):
    mode = (value or os.environ.get("MINUS_POLICY_MODE") or "dev").strip().lower()
    if mode not in {"dev", "production"}:
        raise ValueError("policy mode must be 'dev' or 'production'")
    return mode


def main(argv=None):
    parser = argparse.ArgumentParser(description="Infrastructure Scanner & Optimizer (per-resource)")
    parser.add_argument("--source-dir", required=True, help="Directory containing Terraform configs to scan")
    parser.add_argument("--log-dir", default=os.path.join(os.getcwd(), "artifacts", "review"),
                        help="Report output directory")
    parser.add_argument("--report-only", action="store_true",
                        help="Write the report but do not fail on blocking findings")
    parser.add_argument("--external", action="store_true",
                        help="Also run external policy engines (checkov/trivy) if present; advisory")
    parser.add_argument("--require-external", action="store_true",
                        help="Require at least one external scanner and make external findings blocking")
    parser.add_argument("--policy-mode", choices=["dev", "production"],
                        default=os.environ.get("MINUS_POLICY_MODE", "dev"),
                        help="dev keeps external scanners advisory; production requires and blocks on them")
    args = parser.parse_args(argv)

    policy_mode = _policy_mode(args.policy_mode)
    require_external = args.require_external or policy_mode == "production"
    findings = scan_hcl_files(args.source_dir)
    if args.external or require_external:
        findings = findings + run_external_scanners(args.source_dir, required=require_external)
    generate_report(findings, args.log_dir)

    blockers = blocking_findings(findings, external_blocking=require_external)
    if blockers and not args.report_only:
        ids = ", ".join(f"{b['id']}({b.get('resource', '-')})" for b in blockers)
        print(f"[OPTIMIZER] Blocking findings detected: {ids}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
