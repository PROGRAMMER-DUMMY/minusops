"""
FinOps circuit breakers asserted against the modules' own HCL (PRD s11).

These three limits are the difference between a bad run and a bad month, and each has a
silent-failure mode: an absent Glue `timeout` inherits AWS's 48-hour default, an absent
Athena cutoff bills a full-table scan, and an absent lifecycle rule keeps Bronze in Standard
storage forever. None of them raises an error when missing -- the bill is the only signal.
So they are asserted here rather than assumed.

Fast: reads module HCL as text. No Terraform, no AWS.

Depends on: nothing (stdlib only)
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _module_hcl(module_id, filename="main.tf"):
    with open(os.path.join(_ROOT, "modules", module_id, filename), encoding="utf-8") as fh:
        return fh.read()


def _attr(hcl, name):
    """Value assigned to `name` at any indentation, spaces collapsed."""
    match = re.search(rf"^\s*{re.escape(name)}\s*=\s*(.+)$", hcl, re.MULTILINE)
    return match.group(1).strip() if match else None


# --- Glue execution timeout -----------------------------------------------------------

def test_glue_job_sets_an_explicit_timeout():
    """AWS Glue defaults to 2880 minutes (48h) when `timeout` is absent. A job stuck in a
    shuffle loop then bills DPU-seconds for two days. The cap must be on the resource, not
    only described in an alarm."""
    hcl = _module_hcl("compute-glue-etl")
    job = hcl.split('resource "aws_glue_job" "this"', 1)[1]
    assert _attr(job, "timeout") == "var.timeout_minutes", (
        "aws_glue_job.this must set timeout; without it AWS applies a 48-hour default"
    )


def test_glue_timeout_defaults_to_the_prd_ceiling():
    hcl = _module_hcl("compute-glue-etl")
    block = hcl.split('variable "timeout_minutes"', 1)[1].split("\n}", 1)[0]
    assert _attr(block, "default") == "120"


def test_glue_timeout_cannot_be_set_above_aws_own_ceiling():
    """A validation block is what stops `timeout_minutes = 99999` from silently becoming a
    plan error at apply time instead of a review comment."""
    hcl = _module_hcl("compute-glue-etl")
    block = hcl.split('variable "timeout_minutes"', 1)[1].split("\n}\n\nvariable", 1)[0]
    assert "validation {" in block
    assert "2880" in block, "the ceiling should name AWS's own limit, not an invented one"


# --- Athena scan cutoff ---------------------------------------------------------------

def test_athena_workgroup_enforces_a_scan_cutoff():
    """Without a cutoff a single `SELECT *` over Bronze scans the whole lake at $5/TB."""
    hcl = _module_hcl("query-athena")
    assert "bytes_scanned_cutoff_per_query" in hcl
    block = hcl.split('variable "bytes_scanned_cutoff"', 1)[1].split("\n}", 1)[0]
    assert _attr(block, "default") == "10737418240", "10 GiB, per PRD s11"


# --- S3 lifecycle ---------------------------------------------------------------------

def test_medallion_zones_transition_out_of_standard_storage():
    """Bronze grows without bound. A lifecycle rule is the only thing that stops last
    year's raw drops from being billed at Standard rates forever."""
    hcl = _module_hcl("storage-medallion-s3")
    assert 'resource "aws_s3_bucket_lifecycle_configuration" "zone"' in hcl
    assert "GLACIER" in hcl
    assert _attr(hcl.split('variable "retention_days"', 1)[1].split("\n}", 1)[0], "default")


def demo():
    """Runnable without pytest: python tests/test_finops_circuit_breakers.py"""
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")


if __name__ == "__main__":
    demo()
