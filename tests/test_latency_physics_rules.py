"""
Anti-egress HCL rules and the cross-region latency law (PRD-FINOPS-2026-005, FR-20).

Both failures here are invisible until the invoice or the incident. Cross-region traffic
does not error, it bills at $0.02/GB in each direction. A VPC with no S3 gateway endpoint
works perfectly and routes every lake read through the NAT gateway at roughly $0.045/GB. And
an architecture promising sub-100ms synchronous cross-region writes passes every syntax check
ever written, because the thing that refuses it is physics, not a schema.

Depends on: core/reporting/optimize_analyzer.py, core/architecture/architecture_model.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import architecture_model
import optimize_analyzer


def _scan(tmp_path, hcl, name="main.tf"):
    (tmp_path / name).write_text(hcl, encoding="utf-8")
    return optimize_analyzer.scan_hcl_files(str(tmp_path))


def _ids(findings):
    return {f["id"] for f in findings}


# --- COST-04: cross-region data transfer ----------------------------------------------

def test_two_regions_in_one_workspace_is_flagged(tmp_path):
    """Compute in one region reading a bucket in another is the $12.8k/month trap. Nothing
    in Terraform objects -- the plan is valid and the pipeline works."""
    hcl = '''
provider "aws" {
  region = "us-east-1"
}
provider "aws" {
  alias  = "west"
  region = "us-west-2"
}
resource "aws_s3_bucket" "far" {
  bucket   = "far-bucket"
  provider = aws.west
}
'''
    assert "COST-04" in _ids(_scan(tmp_path, hcl))


def test_a_single_region_workspace_is_not_flagged(tmp_path):
    hcl = '''
provider "aws" {
  region = "us-east-1"
}
resource "aws_s3_bucket" "near" {
  bucket = "near-bucket"
}
'''
    assert "COST-04" not in _ids(_scan(tmp_path, hcl))


def test_the_same_region_named_twice_is_not_a_finding(tmp_path):
    """A second provider alias in the same region is an ordinary pattern, not egress."""
    hcl = '''
provider "aws" {
  region = "us-east-1"
}
provider "aws" {
  alias  = "logging"
  region = "us-east-1"
}
'''
    assert "COST-04" not in _ids(_scan(tmp_path, hcl))


# --- COST-05: un-endpointed S3 traffic ------------------------------------------------

def test_a_vpc_without_an_s3_gateway_endpoint_is_flagged(tmp_path):
    """Every lake read then leaves through the NAT gateway and is billed per GB, on top of
    the hourly NAT charge. The gateway endpoint is free."""
    hcl = '''
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}
resource "aws_nat_gateway" "main" {
  subnet_id = "subnet-123"
}
'''
    assert "COST-05" in _ids(_scan(tmp_path, hcl))


def test_a_vpc_with_an_s3_gateway_endpoint_is_clean(tmp_path):
    hcl = '''
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}
resource "aws_vpc_endpoint" "s3" {
  vpc_id       = aws_vpc.main.id
  service_name = "com.amazonaws.us-east-1.s3"
}
'''
    assert "COST-05" not in _ids(_scan(tmp_path, hcl))


def test_no_vpc_means_no_endpoint_finding(tmp_path):
    """A serverless-only stack has no VPC to endpoint. Flagging it would train operators to
    ignore the rule."""
    hcl = 'resource "aws_s3_bucket" "b" {\n  bucket = "b"\n}\n'
    assert "COST-05" not in _ids(_scan(tmp_path, hcl))


def test_egress_findings_are_cost_not_security(tmp_path):
    """SEC-* findings block a governed deploy. An expensive-but-working topology is a
    finding for a human to weigh, not a gate."""
    hcl = '''
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}
'''
    findings = [f for f in _scan(tmp_path, hcl) if f["id"] == "COST-05"]
    assert findings and findings[0]["category"] == "Cost"
    assert not optimize_analyzer.blocking_findings(findings)


# --- The cross-region law -------------------------------------------------------------

def test_synchronous_sub_100ms_across_regions_is_refused():
    """Cross-region fibre RTT is 30-200ms. A 50ms synchronous commitment is not aggressive,
    it is impossible, and no amount of instance sizing changes that."""
    violation = architecture_model.latency_floor_violation(50, cross_region=True)
    assert violation is not None
    assert "cross-region" in violation["reason"].lower()
    assert violation["floor_ms"] >= 30


def test_a_relaxed_cross_region_target_is_achievable():
    assert architecture_model.latency_floor_violation(500, cross_region=True) is None


def test_sub_100ms_within_a_region_is_fine():
    """Intra-AZ RTT is under a millisecond; the same 50ms target is comfortable here."""
    assert architecture_model.latency_floor_violation(50, cross_region=False) is None


def test_sub_millisecond_within_a_region_still_hits_a_floor():
    """Inter-AZ synchronous replication cannot beat the 1-4ms RTT it depends on."""
    violation = architecture_model.latency_floor_violation(0.5, cross_region=False,
                                                           multi_az=True)
    assert violation is not None


def test_an_undeclared_latency_target_is_not_a_violation():
    """Undeclared means unknown, and inventing a breach from silence is how a gate loses
    the operator's trust."""
    assert architecture_model.latency_floor_violation(None, cross_region=True) is None
