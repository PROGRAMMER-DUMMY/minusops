"""
ephemeral_apply.py -- G9 (docs/phase5_scope.md, Phase 5), ephemeral apply against LocalStack.

Static analysis (G1 `terraform validate`, G2 schema lint, G6 OPA policy) runs pre-apply and
catches everything derivable from HCL/plan JSON alone. G9 exists for the class of failure that
only surfaces once resources are actually created, in real dependency order, against a real
(emulated) provider: missing/implicit `depends_on` that plans fine but fails at apply time,
provider-side validation Terraform's own type system can't express, and apply-time computed
values that only resolve once real IDs exist. A G9 finding is never a re-run of what G1/G2/G5/G6
already checked -- see docs/phase5_scope.md section 3.

Structurally AWS-only: LocalStack has no Databricks emulation. This module never claims more
assurance than it earned -- every verdict carries a `coverage` field distinguishing "full"
(every resource in the plan is an AWS type G9 actually exercised), "partial" (a mixed AWS+
Databricks plan -- G9 covers only the AWS portion), and "none" (Databricks-only -- G9 never ran
at all, never reported as if it passed). This composes with, and does not duplicate or override,
destructive_change_gate.py's (G5) existing `reduced_assurance`/`databricks_resources` fields.

Endpoint isolation is structural, not "configured once and trusted": the ephemeral-apply
provider override (_generate_provider_override) is the ONLY provider configuration this module
ever writes -- dummy credentials, a hard-coded LocalStack endpoint, `skip_credentials_
validation` -- never derived from or falling back to ambient AWS credentials. A resource type
not on RESOURCE_TYPE_ALLOWLIST blocks outright (`resource_type_unverified`) rather than being
attempted against an emulator whose real coverage for it has not been confirmed -- both the
hand-maintained `endpoints{}` block and the official `tflocal` wrapper have a documented,
non-hypothetical gap where an unlisted service silently falls through to real AWS.

STATUS: every entry in RESOURCE_TYPE_ALLOWLIST is currently unverified. Real per-type
verification (and the required negative-fidelity check for security-critical types -- IAM,
KMS, S3 bucket policies) needs a paid LocalStack account (LOCALSTACK_AUTH_TOKEN) this agent
cannot provision. This is a disclosed, real gap -- see HANDOFF.md and docs/phase5_scope.md.
"""
import json
import os
import subprocess
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
import audit_chain  # noqa: E402
import plan_reader  # noqa: E402
import toolpath  # noqa: E402

LOCALSTACK_ENDPOINT_ENV = "MINUS_LOCALSTACK_ENDPOINT"
DEFAULT_LOCALSTACK_ENDPOINT = "http://localhost:4566"
_DATABRICKS_PREFIX = "databricks_"
_PLAN_FILE = "g9_ephemeral.tfplan"
_OVERRIDE_FILE = "g9_localstack_override.tf"
_APPLY_TIMEOUT_SECONDS = 600
_DESTROY_TIMEOUT_SECONDS = 300
_PLAN_TIMEOUT_SECONDS = 120
_INIT_TIMEOUT_SECONDS = 180

# Every AWS service this repo's modules use, as endpoint-override keys (Terraform AWS provider
# `endpoints{}` block, one entry per service, all pointing at the same LocalStack endpoint --
# verified live against LocalStack's own documented Terraform integration pattern before
# writing this). Kept as an explicit, reviewed list rather than relying solely on `tflocal`
# (whose own changelog admits incremental, incomplete service coverage) or omitting the block
# entirely (which would silently fall through to real AWS for every unlisted service).
_ENDPOINT_SERVICES = (
    "athena", "budgets", "cloudwatch", "cloudwatchevents", "cloudwatchlogs", "ec2",
    "emrserverless", "glue", "iam", "kinesis", "firehose", "kinesisanalyticsv2", "kms", "mwaa",
    "redshiftserverless", "s3", "sfn", "sns", "sts",
)

# Reviewed allowlist of AWS resource types this repo's modules can actually produce -- same
# design shape as destructive_change_gate.py's STATEFUL_RESOURCE_TYPES/IAM_RESOURCE_TYPES
# (scoped deliberately to what the 16-module catalog produces today, extended when a new
# module introduces a new type, never guessed). Enumerated directly via
# `grep -rhoE '^resource "aws_[a-z_0-9]+"' modules/*/main.tf`, not assumed.
#
# Each value is (verified, security_critical). `verified=True` is only honest once a real,
# live LocalStack run has actually applied that type successfully; security_critical types
# additionally require a negative-fidelity check (LocalStack must REJECT something real AWS
# rejects, not merely accept a valid config) before verified=True is meaningful for them --
# see docs/phase5_scope.md proof-bar item 1. Every entry is False here; this file does not
# pretend verification that has not happened.
RESOURCE_TYPE_ALLOWLIST = {
    "aws_athena_workgroup": (False, False),
    "aws_budgets_budget": (False, False),
    "aws_cloudwatch_event_rule": (False, False),
    "aws_cloudwatch_event_target": (False, False),
    "aws_cloudwatch_metric_alarm": (False, False),
    "aws_default_security_group": (False, False),
    "aws_eip": (False, False),
    "aws_emrserverless_application": (False, False),
    "aws_glue_catalog_database": (False, False),
    "aws_glue_catalog_table": (False, False),
    "aws_glue_job": (False, False),
    "aws_glue_registry": (False, False),
    "aws_glue_schema": (False, False),
    "aws_glue_trigger": (False, False),
    "aws_iam_role": (False, True),
    "aws_iam_role_policy": (False, True),
    "aws_internet_gateway": (False, False),
    "aws_kinesis_firehose_delivery_stream": (False, False),
    "aws_kinesis_stream": (False, False),
    "aws_kinesisanalyticsv2_application": (False, False),
    "aws_kms_alias": (False, False),
    "aws_kms_key": (False, True),
    "aws_mwaa_environment": (False, False),
    "aws_nat_gateway": (False, False),
    "aws_redshiftserverless_namespace": (False, False),
    "aws_redshiftserverless_workgroup": (False, False),
    "aws_route_table": (False, False),
    "aws_route_table_association": (False, False),
    "aws_s3_bucket": (False, False),
    "aws_s3_bucket_lifecycle_configuration": (False, False),
    "aws_s3_bucket_policy": (False, True),
    "aws_s3_bucket_public_access_block": (False, False),
    "aws_s3_bucket_server_side_encryption_configuration": (False, False),
    "aws_s3_bucket_versioning": (False, False),
    "aws_s3_object": (False, False),
    "aws_sfn_state_machine": (False, False),
    "aws_sns_topic": (False, False),
    "aws_sns_topic_subscription": (False, False),
    "aws_subnet": (False, False),
    "aws_vpc": (False, False),
    "aws_vpc_endpoint": (False, False),
}


def _fail(reason, detail="", coverage=None, databricks_resources=None):
    return {
        "evaluation_failed": True, "reason": reason, "detail": detail,
        "coverage": coverage, "databricks_resources": databricks_resources or [],
        "findings": [],
    }


def classify_coverage(plan_json):
    """Return (coverage, databricks_addresses, aws_addresses). "none" means every managed
    resource in the plan is Databricks -- G9 never runs. "partial" means a genuine mix. "full"
    means AWS-only. Uses plan_reader.py's shared, fail-closed managed-resource read (absent
    resource_changes is a legitimate zero-managed-changes plan, not an error, matching G6's own
    shadow-reader policy -- this is an advisory classification, not G5's enforcing gate)."""
    raw_rc, _error = plan_reader.read_resource_changes(plan_json, treat_absent_as_error=False)
    managed, _malformed = plan_reader.managed_only(raw_rc or [])
    databricks = sorted(
        rc.get("address") for rc in managed
        if isinstance(rc.get("type"), str) and rc["type"].startswith(_DATABRICKS_PREFIX)
    )
    aws = sorted(
        rc.get("address") for rc in managed
        if isinstance(rc.get("type"), str) and not rc["type"].startswith(_DATABRICKS_PREFIX)
    )
    if not managed:
        return "none", databricks, aws
    if not aws:
        return "none", databricks, aws
    if databricks:
        return "partial", databricks, aws
    return "full", databricks, aws


def unverified_types_in_plan(plan_json):
    """AWS resource types present in the plan that are either entirely unknown to the allowlist
    (a new module introduced a type this file hasn't reviewed at all) or known but not yet
    verified=True. Never attempted against LocalStack -- see module docstring."""
    raw_rc, _error = plan_reader.read_resource_changes(plan_json, treat_absent_as_error=False)
    managed, _malformed = plan_reader.managed_only(raw_rc or [])
    unverified = set()
    for rc in managed:
        rtype = rc.get("type")
        if not isinstance(rtype, str) or rtype.startswith(_DATABRICKS_PREFIX):
            continue
        entry = RESOURCE_TYPE_ALLOWLIST.get(rtype)
        if entry is None or not entry[0]:
            unverified.add(rtype)
    return unverified


def _generate_provider_override(endpoint):
    """The ONLY provider configuration this module ever writes: dummy credentials, every AWS
    service this repo's modules use pointed at the same hard-coded LocalStack endpoint. Never
    derived from ambient AWS credentials -- there is no code path in this module that reads
    real AWS_* environment variables for this override."""
    endpoints_lines = "\n".join(f'    {svc} = "{endpoint}"' for svc in _ENDPOINT_SERVICES)
    return f'''# Generated by ephemeral_apply.py (G9) -- the only provider override this module ever
# writes. Do not edit; regenerated on every run and removed after teardown.
provider "aws" {{
  access_key                  = "test"
  secret_key                  = "test"
  region                      = "us-east-1"
  s3_use_path_style            = true
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {{
{endpoints_lines}
  }}
}}
'''


def _parse_apply_json_stream(text):
    """Parse `terraform apply -json` output. Returns (events, error) -- error is a string on
    the first non-JSON line, None otherwise. Real Terraform output CAN genuinely mix valid JSON
    lines with non-JSON trailing content -- confirmed live: a crashed provider plugin dumps a
    Go panic stack trace directly into what's otherwise a pure JSON stream. That must block
    (apply_result_malformed), not be silently skipped while treating whatever DID parse as
    sufficient."""
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            return events, f"non-JSON line in apply output: {line[:200]!r}"
    return events, None


def _cleanup(override_path, plan_path):
    """Remove the generated override/plan files for an early exit (before any apply was
    attempted, so no destroy is needed -- nothing was ever created)."""
    for path in (override_path, plan_path):
        if path and os.path.exists(path):
            os.remove(path)


def _resource_outcomes(events):
    """address -> 'complete' | 'errored', from apply_complete/apply_errored hook events --
    verified live against real terraform apply -json output (both the success and the crashed-
    plugin failure case) before writing this."""
    outcomes = {}
    for evt in events:
        etype = evt.get("type")
        if etype not in ("apply_complete", "apply_errored"):
            continue
        addr = ((evt.get("hook") or {}).get("resource") or {}).get("addr")
        if addr:
            outcomes[addr] = "complete" if etype == "apply_complete" else "errored"
    return outcomes


def run_ephemeral_apply(dir_, localstack_endpoint=None,
                         apply_timeout=_APPLY_TIMEOUT_SECONDS,
                         destroy_timeout=_DESTROY_TIMEOUT_SECONDS):
    """Orchestrate one full ephemeral create+destroy cycle against LocalStack for the Terraform
    configuration in `dir_`. Never raises for an expected failure mode -- every case in
    docs/phase5_scope.md section 4's table maps to a returned verdict, not an exception.

    REAL BUG CAUGHT BEFORE THIS SHIPPED, not assumed away: the first draft wrote the LocalStack
    provider override AFTER the initial classification plan, meaning that first plan ran under
    whatever provider config was ambient in `dir_` -- not protected by dummy credentials at all.
    On a machine with real ambient AWS credentials, that is exactly the "falls back to ambient
    credentials" violation condition 5 exists to prevent, even though `plan` itself never
    mutates anything. Confirmed directly: a real end-to-end smoke test surfaced a confusing
    `teardown_failed` verdict that traced back to an orphaned provider-plugin process from a
    timed-out apply holding the state lock -- itself a symptom of debugging this the hard way
    instead of catching the design flaw first. Fixed: the override is written FIRST, before any
    terraform command runs at all, so every single invocation in this function -- including the
    read-only classification plan -- is isolated from the very first command, never ambient.
    """
    localstack_endpoint = localstack_endpoint or os.environ.get(
        LOCALSTACK_ENDPOINT_ENV, DEFAULT_LOCALSTACK_ENDPOINT)

    terraform = toolpath.find_tool("terraform")
    if not terraform:
        return _fail("terraform_not_found", "terraform binary not found on PATH")

    override_path = os.path.join(dir_, _OVERRIDE_FILE)
    plan_path = os.path.join(dir_, _PLAN_FILE)
    with open(override_path, "w", encoding="utf-8") as f:
        f.write(_generate_provider_override(localstack_endpoint))

    try:
        reinit = subprocess.run(
            [terraform, f"-chdir={dir_}", "init", "-input=false", "-reconfigure"],
            capture_output=True, text=True, timeout=_INIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _cleanup(override_path, None)
        return _fail("init_timeout", f"init did not complete within {_INIT_TIMEOUT_SECONDS}s")
    if reinit.returncode != 0:
        _cleanup(override_path, None)
        return _fail("init_failed", (reinit.stderr or "").strip()[:2000])

    try:
        plan_result = subprocess.run(
            [terraform, f"-chdir={dir_}", "plan", "-out", _PLAN_FILE, "-input=false"],
            capture_output=True, text=True, timeout=_PLAN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _cleanup(override_path, plan_path)
        return _fail("plan_timeout", f"plan did not complete within {_PLAN_TIMEOUT_SECONDS}s")
    if plan_result.returncode != 0:
        _cleanup(override_path, plan_path)
        return _fail("plan_failed", (plan_result.stderr or plan_result.stdout or "").strip()[:2000])

    show_result = subprocess.run(
        [terraform, f"-chdir={dir_}", "show", "-json", _PLAN_FILE],
        capture_output=True, text=True, timeout=60)
    if show_result.returncode != 0:
        _cleanup(override_path, plan_path)
        return _fail("plan_show_failed", (show_result.stderr or "").strip()[:2000])
    try:
        plan_json = json.loads(show_result.stdout)
    except json.JSONDecodeError as exc:
        _cleanup(override_path, plan_path)
        return _fail("plan_malformed", str(exc))

    coverage, databricks_addresses, aws_addresses = classify_coverage(plan_json)
    if coverage == "none":
        # Not a failure -- a structural non-applicability. Reported honestly, never as "passed".
        _cleanup(override_path, plan_path)
        return {
            "evaluation_failed": False, "coverage": "none",
            "databricks_resources": databricks_addresses, "aws_resources_applied": [],
            "findings": [],
            "detail": "plan has no AWS resources -- G9 does not run" if not databricks_addresses
                       else "plan touches only Databricks resources -- G9 does not run",
        }

    unverified = unverified_types_in_plan(plan_json)
    if unverified:
        _cleanup(override_path, plan_path)
        return _fail(
            "resource_type_unverified",
            f"plan contains resource type(s) not confirmed on the reviewed LocalStack "
            f"allowlist: {sorted(unverified)}",
            coverage=coverage, databricks_resources=databricks_addresses)

    # Single-exit design, deliberately: a `return` inside a `finally` block silently swallows
    # any real exception raised in the try block (confirmed directly -- a plain `raise
    # ValueError` inside try, with a bare `return` in finally, is swallowed without a trace).
    # `verdict` is instead built up here and only returned once, AFTER the finally block runs
    # to completion without its own return -- a genuine bug in this function still propagates
    # normally instead of being hidden behind teardown cleanup.
    verdict = None
    try:
        try:
            apply_result = subprocess.run(
                [terraform, f"-chdir={dir_}", "apply", "-auto-approve", "-json", _PLAN_FILE],
                capture_output=True, text=True, timeout=apply_timeout)
        except subprocess.TimeoutExpired:
            verdict = _fail("apply_timeout", f"apply did not complete within {apply_timeout}s",
                             coverage=coverage, databricks_resources=databricks_addresses)
        else:
            events, parse_error = _parse_apply_json_stream(apply_result.stdout)
            if parse_error:
                verdict = _fail("apply_result_malformed", parse_error,
                                 coverage=coverage, databricks_resources=databricks_addresses)
            else:
                outcomes = _resource_outcomes(events)
                succeeded = sorted(a for a, s in outcomes.items() if s == "complete")
                errored = sorted(a for a, s in outcomes.items() if s == "errored")
                if apply_result.returncode != 0:
                    reason = "apply_partial_failure" if succeeded else "apply_failed"
                    verdict = _fail(
                        reason,
                        f"succeeded={succeeded} errored={errored} "
                        f"{(apply_result.stderr or '').strip()[:1500]}",
                        coverage=coverage, databricks_resources=databricks_addresses)
                else:
                    verdict = {
                        "evaluation_failed": False, "coverage": coverage,
                        "databricks_resources": databricks_addresses,
                        "aws_resources_applied": succeeded, "findings": [],
                    }
    finally:
        try:
            destroy_result = subprocess.run(
                [terraform, f"-chdir={dir_}", "destroy", "-auto-approve"],
                capture_output=True, text=True, timeout=destroy_timeout)
            teardown_ok = destroy_result.returncode == 0
            teardown_detail = (destroy_result.stderr or "").strip()[:2000]
        except subprocess.TimeoutExpired:
            teardown_ok = False
            teardown_detail = f"destroy did not complete within {destroy_timeout}s"
        for path in (override_path, plan_path):
            if os.path.exists(path):
                os.remove(path)
        if not teardown_ok and verdict is not None and not verdict.get("evaluation_failed"):
            # A failed teardown overrides an otherwise-clean verdict -- an ephemeral
            # environment that doesn't tear down is a real operational problem (cost, leaked
            # state), never a footnote on an otherwise-green result. No `return` here (see the
            # note above this try block) -- just reassigning the variable the function returns
            # once this finally completes.
            verdict = _fail("teardown_failed", teardown_detail, coverage=coverage,
                             databricks_resources=databricks_addresses)

    return verdict


def compose_with_g5(g5_classification, g9_result):
    """Merge a destructive_change_gate.classify() result with a G9 verdict into one visible
    assurance summary -- docs/phase5_scope.md section 2's explicit requirement that G9 compose
    with, not silently duplicate or override, G5's existing reduced_assurance signal.

    G9 does not run synchronously inside plan_gate.py's stage_plan() (it needs a live LocalStack
    instance, which an interactive dev/production plan flow does not have -- G9 is a separate,
    CI-only verification step; see log_result()'s own docstring). This function is how a report
    or reviewer combines the two signals after the fact, whether or not a G9 run actually
    happened for a given plan -- the `g9_ran` field distinguishes "G9 verified this" from "no
    G9 evidence exists for this plan" explicitly, never conflating the two."""
    reduced_assurance = bool((g5_classification or {}).get("reduced_assurance"))
    databricks_resources = (g5_classification or {}).get("databricks_resources") or []

    if g9_result is None:
        return {
            "g9_ran": False,
            "reduced_assurance": reduced_assurance,
            "databricks_resources": databricks_resources,
            "summary": (
                "No G9 (ephemeral-apply) evidence exists for this plan. "
                + ("G5 already marks this reduced-assurance for Databricks resources "
                   f"{databricks_resources}." if reduced_assurance else
                   "G5 does not flag reduced assurance, but no ephemeral-apply verification "
                   "has run either -- absence of a G9 result is not the same as a clean G9 "
                   "verdict.")
            ),
        }

    coverage = g9_result.get("coverage")
    g9_databricks = g9_result.get("databricks_resources") or []
    return {
        "g9_ran": True,
        "reduced_assurance": reduced_assurance,
        "databricks_resources": databricks_resources,
        "g9_coverage": coverage,
        "g9_evaluation_failed": g9_result.get("evaluation_failed"),
        "summary": (
            f"G9 ran with coverage={coverage!r}. "
            + (f"Databricks resources {g9_databricks} were never exercised by G9 (AWS-only "
               "emulation) -- G5's reduced_assurance for this plan is not offset by any G9 "
               "evidence for those resources specifically."
               if coverage in ("partial", "none") else
               "Every resource in this plan is an AWS type G9 actually exercised.")
        ),
    }


def log_result(dir_, result):
    """Log a G9 verdict to the SAME audit chain plan_gate.py's _audit() writes to (via the same
    underlying audit_chain.append() primitive -- no circular import with plan_gate.py, since G9
    does not run synchronously inside stage_plan(); see this module's own docstring for why).
    Advisory only, same as G6/Phase 4's shadow logging -- this never blocks anything on its
    own; composing with G5's reduced_assurance is a report-reading/audit-reviewing concern, not
    an enforcement path this function adds.

    Path resolution matches plan_gate.py's own LOG_DIR exactly (os.getcwd()-based, not relative
    to this module's file location) -- this is what makes it genuinely "the same audit chain"
    rather than a second, differently-rooted one that happens to share a directory name."""
    import datetime
    import getpass
    log_path = os.path.join(os.getcwd(), ".agents", "logs", "audit.jsonl")
    rec = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "operator": getpass.getuser(), "component": "ephemeral_apply", "action": "g9_apply",
        "status": "OK" if not result.get("evaluation_failed") else "BLOCKED",
        "dir": dir_, "g9_result": result,
    }
    try:
        audit_chain.append(log_path, rec)
    except Exception as exc:
        print(f"[g9] WARNING: could not write audit record: {exc}", file=sys.stderr)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="G9 ephemeral apply against LocalStack (docs/phase5_scope.md, Phase 5)")
    ap.add_argument("--dir", required=True, help="Terraform directory to ephemeral-apply")
    ap.add_argument("--localstack-endpoint", default=None,
                    help=f"defaults to ${LOCALSTACK_ENDPOINT_ENV} or {DEFAULT_LOCALSTACK_ENDPOINT}")
    ap.add_argument("--apply-timeout", type=int, default=_APPLY_TIMEOUT_SECONDS)
    ap.add_argument("--destroy-timeout", type=int, default=_DESTROY_TIMEOUT_SECONDS)
    ap.add_argument("--no-audit-log", action="store_true",
                    help="skip writing to the audit chain (useful for local smoke tests)")
    args = ap.parse_args(argv)

    result = run_ephemeral_apply(args.dir, localstack_endpoint=args.localstack_endpoint,
                                  apply_timeout=args.apply_timeout,
                                  destroy_timeout=args.destroy_timeout)
    print(json.dumps(result, indent=2))
    if not args.no_audit_log:
        log_result(args.dir, result)
    return 1 if result["evaluation_failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
