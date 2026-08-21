"""
Logging, retention and key-hierarchy governance, asserted against module HCL
(TASK-TDD-2026-002 WP3/WP4).

Every gap here is silent. A log group with no retention keeps data forever and bills for it
forever -- nothing errors, the line item just grows. An unencrypted log group holds whatever
the application printed, which for a data pipeline is row content. A secret on the AWS-managed
key cannot have its access audited or revoked independently of the account. None of these
fail a plan, so they are pinned here.

Fast: reads module HCL as text. No Terraform, no AWS.

Depends on: nothing (stdlib only)
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULES = os.path.join(_ROOT, "modules")


def _iter_module_hcl():
    """(module_id, filename, text) for every .tf file in the catalog."""
    for module_id in sorted(os.listdir(_MODULES)):
        directory = os.path.join(_MODULES, module_id)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if name.endswith(".tf"):
                with open(os.path.join(directory, name), encoding="utf-8") as fh:
                    yield module_id, name, fh.read()


def _blocks(hcl, resource_type):
    """Bodies of every `resource "<type>" "<name>"` block, brace-matched."""
    out = []
    for match in re.finditer(rf'resource\s+"{re.escape(resource_type)}"\s+"([^"]+)"\s*{{', hcl):
        depth, i = 1, match.end()
        while i < len(hcl) and depth:
            depth += (hcl[i] == "{") - (hcl[i] == "}")
            i += 1
        out.append((match.group(1), hcl[match.end():i - 1]))
    return out


def _find(resource_type):
    for module_id, filename, hcl in _iter_module_hcl():
        for name, body in _blocks(hcl, resource_type):
            yield module_id, filename, name, body


# --- CloudWatch Logs ------------------------------------------------------------------

def test_cloudwatch_log_groups_have_explicit_retention():
    """Absent retention_in_days means "never expire". Nothing errors; the bill grows."""
    missing = [f"{m}/{f}:{n}" for m, f, n, body in _find("aws_cloudwatch_log_group")
               if "retention_in_days" not in body]
    assert not missing, f"log groups with unbounded retention: {missing}"


def test_cloudwatch_log_groups_are_encrypted_with_a_customer_managed_key():
    """Pipeline logs carry whatever the job printed, which routinely includes row content.
    Default CloudWatch encryption uses an AWS-owned key that cannot be audited or revoked."""
    unencrypted = [f"{m}/{f}:{n}" for m, f, n, body in _find("aws_cloudwatch_log_group")
                   if "kms_key_id" not in body]
    assert not unencrypted, f"log groups without a CMK: {unencrypted}"


# --- Secrets --------------------------------------------------------------------------

def test_secrets_use_a_customer_managed_key():
    """A secret on the AWS-managed key inherits account-wide access. A dedicated CMK is what
    makes 'who read this secret' answerable and revocable."""
    unencrypted = [f"{m}/{f}:{n}" for m, f, n, body in _find("aws_secretsmanager_secret")
                   if "kms_key_id" not in body]
    assert not unencrypted, f"secrets without a CMK: {unencrypted}"


# --- S3 access logging ----------------------------------------------------------------

def test_medallion_zones_can_emit_server_access_logs():
    """Server access logs answer 'who read the Gold data', which no other signal covers --
    CloudTrail data events are billed per request and are off by default here.

    Asserted as wiring rather than as always-on: the target bucket must already exist and
    delivery is billed, so this follows the same opt-in shape as replication and the SIEM
    trail in this catalog."""
    path = os.path.join(_MODULES, "storage-medallion-s3", "main.tf")
    with open(path, encoding="utf-8") as fh:
        hcl = fh.read()
    assert 'variable "access_log_bucket"' in hcl, (
        "storage-medallion-s3 has no way to emit server access logs at all"
    )
    logging_blocks = _blocks(hcl, "aws_s3_bucket_logging")
    assert logging_blocks, "no aws_s3_bucket_logging resource"
    body = logging_blocks[0][1]
    assert "count" in body or "for_each" in body, (
        "logging must be conditional; an unset target bucket would otherwise fail the plan"
    )


def test_audit_bucket_is_not_its_own_log_target():
    """Writing a bucket's access logs into itself creates a feedback loop: each delivery is
    an object write, which produces another log record."""
    path = os.path.join(_MODULES, "storage-medallion-s3", "main.tf")
    with open(path, encoding="utf-8") as fh:
        hcl = fh.read()
    for _, body in _blocks(hcl, "aws_s3_bucket_logging"):
        assert "aws_s3_bucket.zone" not in body.split("target_bucket")[-1].split("\n")[0], (
            "target_bucket must be an external bucket, never a medallion zone"
        )
