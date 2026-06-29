"""
The HCL scanner backs the §5 safety rule ("resolve SEC-* to zero"). The regression
that matters: SEC-02 must catch a wildcard resource written in HCL bareword form
(`Resource = "*"`), not only the quoted-JSON form — the old regex missed the former.
"""
import optimize_analyzer


def _scan(tmp_path, hcl):
    (tmp_path / "main.tf").write_text(hcl, encoding="utf-8")
    return {f["id"] for f in optimize_analyzer.scan_hcl_files(str(tmp_path))}


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


def test_security_findings_are_blocking(tmp_path):
    hcl = 'resource "aws_s3_bucket" "b" { bucket = "x" }'
    (tmp_path / "main.tf").write_text(hcl, encoding="utf-8")
    findings = optimize_analyzer.scan_hcl_files(str(tmp_path))

    blockers = optimize_analyzer.blocking_findings(findings)

    assert [finding["id"] for finding in blockers] == ["SEC-01"]


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
