"""
The HCL scanner backs the §5 safety rule ("resolve SEC-* to zero"). The regression
that matters: SEC-02 must catch a wildcard resource written in HCL bareword form
(`Resource = "*"`), not only the quoted-JSON form — the old regex missed the former.
"""
import os

import optimize_analyzer
import toolpath

_REPO_ROOT = os.path.dirname(os.path.dirname(__file__))


def _scan(tmp_path, hcl):
    (tmp_path / "main.tf").write_text(hcl, encoding="utf-8")
    return {f["id"] for f in optimize_analyzer.scan_hcl_files(str(tmp_path))}


def test_data01_flags_glue_job_without_bookmarks(tmp_path):
    hcl = 'resource "aws_glue_job" "j" { name = "x"\n command { name = "glueetl" } }'
    assert "DATA-01" in _scan(tmp_path, hcl)


def test_data01_absent_when_bookmarks_enabled(tmp_path):
    hcl = ('resource "aws_glue_job" "j" { name = "x"\n'
           '  default_arguments = { "--job-bookmark-option" = "job-bookmark-enable" }\n }')
    assert "DATA-01" not in _scan(tmp_path, hcl)


def test_data03_flags_athena_workgroup_without_scan_cutoff(tmp_path):
    hcl = 'resource "aws_athena_workgroup" "w" { name = "wg" }'
    ids = _scan(tmp_path, hcl)
    assert "DATA-03" in ids
    hcl2 = ('resource "aws_athena_workgroup" "w" { name = "wg"\n'
            '  configuration { bytes_scanned_cutoff_per_query = 10737418240 } }')
    assert "DATA-03" not in _scan(tmp_path, hcl2)


def test_sec02_detects_bareword_wildcard_resource(tmp_path):
    hcl = '''
    resource "aws_iam_policy" "p" {
      policy = jsonencode({
        Statement = [{ Effect = "Allow", Action = ["s3:*"], Resource = "*" }]
      })
    }
    '''
    assert "SEC-02" in _scan(tmp_path, hcl)


def test_sec02_detects_quoted_json_wildcard_resource(tmp_path):
    # Realistic quoted-JSON form: a heredoc policy with unescaped quotes.
    hcl = '''
    resource "aws_iam_policy" "p" {
      policy = <<-POLICY
        { "Statement": [{ "Effect": "Allow", "Resource": "*" }] }
      POLICY
    }
    '''
    assert "SEC-02" in _scan(tmp_path, hcl)


def test_sec02_clean_when_resource_is_scoped(tmp_path):
    hcl = '''
    resource "aws_iam_policy" "p" {
      policy = jsonencode({
        Statement = [{ Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::b/*" }]
      })
    }
    '''
    assert "SEC-02" not in _scan(tmp_path, hcl)


def test_sec01_flags_bucket_without_public_access_block(tmp_path):
    hcl = 'resource "aws_s3_bucket" "b" { bucket = "x" }'
    assert "SEC-01" in _scan(tmp_path, hcl)


def test_sec01_clean_with_public_access_block(tmp_path):
    hcl = '''
    resource "aws_s3_bucket" "b" { bucket = "x" }
    resource "aws_s3_bucket_public_access_block" "b" { bucket = aws_s3_bucket.b.id }
    resource "aws_s3_bucket_lifecycle_configuration" "b" { bucket = aws_s3_bucket.b.id }
    '''
    ids = _scan(tmp_path, hcl)
    assert "SEC-01" not in ids and "COST-01" not in ids


def test_sec05_flags_cross_account_trust_without_external_id(tmp_path):
    hcl = '''
    data "aws_iam_policy_document" "cross_account" {
      statement {
        actions = ["sts:AssumeRole"]
        principals {
          type        = "AWS"
          identifiers = ["arn:aws:iam::414351767826:root"]
        }
      }
    }
    '''
    assert "SEC-05" in _scan(tmp_path, hcl)


def test_sec05_flags_cross_account_trust_with_wildcard_principal(tmp_path):
    hcl = '''
    data "aws_iam_policy_document" "cross_account" {
      statement {
        actions = ["sts:AssumeRole"]
        principals {
          type        = "AWS"
          identifiers = ["*"]
        }
        condition {
          test     = "StringEquals"
          variable = "sts:ExternalId"
          values   = ["some-external-id"]
        }
      }
    }
    '''
    assert "SEC-05" in _scan(tmp_path, hcl)


def test_sec05_clean_with_external_id_and_scoped_principal(tmp_path):
    hcl = '''
    data "aws_iam_policy_document" "cross_account" {
      statement {
        actions = ["sts:AssumeRole"]
        principals {
          type        = "AWS"
          identifiers = ["arn:aws:iam::414351767826:root"]
        }
        condition {
          test     = "StringEquals"
          variable = "sts:ExternalId"
          values   = ["some-external-id"]
        }
      }
    }
    '''
    assert "SEC-05" not in _scan(tmp_path, hcl)


def test_sec05_ignores_same_account_service_role_trust(tmp_path):
    # This is the existing house pattern (modules/dq-great-expectations/main.tf) -- a same-
    # account service-role trust must never false-positive as a cross-account finding.
    hcl = '''
    data "aws_iam_policy_document" "assume" {
      statement {
        actions = ["sts:AssumeRole"]
        principals {
          type        = "Service"
          identifiers = ["glue.amazonaws.com"]
        }
      }
    }
    '''
    assert "SEC-05" not in _scan(tmp_path, hcl)


def test_sec05_flags_databricks_assume_role_policy_without_external_id(tmp_path):
    hcl = '''
    data "databricks_aws_assume_role_policy" "this" {
    }
    '''
    assert "SEC-05" in _scan(tmp_path, hcl)


def test_sec05_clean_when_databricks_assume_role_policy_has_external_id(tmp_path):
    hcl = '''
    data "databricks_aws_assume_role_policy" "this" {
      external_id = var.databricks_account_id
    }
    '''
    assert "SEC-05" not in _scan(tmp_path, hcl)


def test_sec05_clean_against_the_real_databricks_workspace_module():
    # Proves the rule against the actual authored HCL, not just a synthetic test string --
    # modules/databricks-workspace/main.tf really does supply external_id.
    module_dir = os.path.join(_REPO_ROOT, "modules", "databricks-workspace")
    findings = optimize_analyzer.scan_hcl_files(module_dir)
    assert "SEC-05" not in {f["id"] for f in findings}


def test_security_findings_are_blocking(tmp_path):
    hcl = 'resource "aws_s3_bucket" "b" { bucket = "x" }'
    (tmp_path / "main.tf").write_text(hcl, encoding="utf-8")
    findings = optimize_analyzer.scan_hcl_files(str(tmp_path))

    blockers = optimize_analyzer.blocking_findings(findings)

    assert [finding["id"] for finding in blockers] == ["SEC-01"]


def test_external_findings_are_advisory_until_production_mode():
    findings = [{
        "id": "CKV_AWS_1",
        "category": "External:checkov",
        "title": "checkov finding",
        "description": "external policy finding",
        "severity": "EXTERNAL",
    }]

    assert optimize_analyzer.blocking_findings(findings) == []
    assert [f["id"] for f in optimize_analyzer.blocking_findings(findings, external_blocking=True)] == ["CKV_AWS_1"]


def test_required_external_scanner_blocks_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(toolpath, "find_tool", lambda _name: None)

    findings = optimize_analyzer.run_external_scanners(str(tmp_path), required=True)

    assert [f["id"] for f in findings] == ["POLICY-EXT"]
    assert optimize_analyzer.blocking_findings(findings, external_blocking=True)


def test_trivy_findings_parsed_from_the_real_confirmed_json_shape(tmp_path, monkeypatch):
    """G7 (tfsec -> Trivy swap). The exact nested shape here (Results[].Misconfigurations[]
    with ID/Title/Message/CauseMetadata.Resource) was verified live against `trivy config -f
    json` run on a real module in this repo (aws_s3_bucket.zone["bronze"], AWS-0089 "S3 Bucket
    Logging"), not assumed from documentation -- this fixture is that real output, trimmed."""
    import json as _json
    import subprocess as _subprocess
    from unittest import mock

    real_trivy_output = _json.dumps({
        "SchemaVersion": 2,
        "Trivy": {"Version": "0.72.0"},
        "ArtifactName": "modules/storage-medallion-s3",
        "ArtifactType": "filesystem",
        "Results": [
            {"Target": ".", "Class": "config", "Type": "terraform",
             "MisconfSummary": {"Successes": 61, "Failures": 0}},
            {"Target": "main.tf", "Class": "config", "Type": "terraform",
             "MisconfSummary": {"Successes": 0, "Failures": 1},
             "Misconfigurations": [{
                 "Type": "Terraform Security Check", "ID": "AWS-0089",
                 "Title": "S3 Bucket Logging",
                 "Description": "Ensures S3 bucket logging is enabled for S3 buckets",
                 "Message": "Bucket has logging disabled",
                 "Namespace": "builtin.aws.s3.aws0089", "Severity": "LOW",
                 "PrimaryURL": "https://avd.aquasec.com/misconfig/aws-0089",
                 "Status": "FAIL",
                 "CauseMetadata": {"Resource": 'aws_s3_bucket.zone["bronze"]',
                                   "Provider": "AWS", "Service": "s3",
                                   "StartLine": 48, "EndLine": 55},
             }]},
        ],
    })
    monkeypatch.setattr(toolpath, "find_tool", lambda name: "trivy" if name == "trivy" else None)
    fake_result = _subprocess.CompletedProcess(args=[], returncode=32, stdout=real_trivy_output, stderr="")
    with mock.patch.object(optimize_analyzer.subprocess, "run", return_value=fake_result):
        findings = optimize_analyzer.run_external_scanners(str(tmp_path))

    assert len(findings) == 1
    finding = findings[0]
    assert finding["id"] == "AWS-0089"
    assert finding["category"] == "External:trivy"
    assert finding["title"] == "S3 Bucket Logging"
    assert finding["description"] == "Bucket has logging disabled"
    assert finding["resource"] == 'aws_s3_bucket.zone["bronze"]'
    # Real, confirmed behavior: trivy config exits non-zero (32) whenever it finds real
    # misconfigurations -- this must NOT be treated as a scanner-unavailable error the way a
    # genuinely failed subprocess would be; the finding above proves it parsed correctly despite
    # the non-zero returncode.
    assert finding["id"] != "POLICY-EXT"


def test_per_resource_flags_only_uncovered_buckets(tmp_path):
    # The whole-file scanner's blind spot: many buckets, one public-access-block.
    # Per-resource analysis must flag exactly the three unprotected buckets.
    hcl = '''
    resource "aws_s3_bucket" "a" { bucket = "a" }
    resource "aws_s3_bucket" "b" { bucket = "b" }
    resource "aws_s3_bucket" "c" { bucket = "c" }
    resource "aws_s3_bucket" "d" { bucket = "d" }
    resource "aws_s3_bucket_public_access_block" "a" { bucket = aws_s3_bucket.a.id }
    resource "aws_s3_bucket_lifecycle_configuration" "a" { bucket = aws_s3_bucket.a.id }
    '''
    findings = optimize_analyzer.scan_hcl_files(_w(tmp_path, hcl))
    sec01 = {f["resource"] for f in findings if f["id"] == "SEC-01"}
    assert sec01 == {"aws_s3_bucket.b", "aws_s3_bucket.c", "aws_s3_bucket.d"}
    assert "aws_s3_bucket.a" not in sec01


def test_per_resource_covers_for_each_buckets(tmp_path):
    # The real generated pipeline uses for_each; one block covers all buckets.
    hcl = '''
    resource "aws_s3_bucket" "zone" { for_each = toset(["bronze","silver"]) bucket = each.key }
    resource "aws_s3_bucket_public_access_block" "zone" { for_each = aws_s3_bucket.zone bucket = each.value.id }
    resource "aws_s3_bucket_lifecycle_configuration" "zone" { for_each = aws_s3_bucket.zone bucket = each.value.id }
    '''
    ids = {f["id"] for f in optimize_analyzer.scan_hcl_files(_w(tmp_path, hcl))}
    assert "SEC-01" not in ids
    assert "COST-01" not in ids


def _w(tmp_path, hcl):
    (tmp_path / "main.tf").write_text(hcl, encoding="utf-8")
    return str(tmp_path)
