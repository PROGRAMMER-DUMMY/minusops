"""
intent_assertions.py -- Phase 4 (G3/G4, docs/phase4_scope.md), auto-generated checks that
verify a run's declared intent against the REAL generated plan. ADVISORY ONLY: nothing here
blocks generation, plan, or apply. Findings are logged and surfaced in the deploy report
alongside conformance()'s existing findings, the same way G6's shadow findings are logged
alongside the regex path -- never enforced until a separate, later, evidence-reviewed decision.

Three claim classes, each traceable to something generation actually consumes today (confirmed
live before writing this, not assumed -- see docs/phase4_scope.md's own "what currently exists"
section): free-text functional/non-functional requirements answers are explicitly OUT of scope,
since terraform_generator.py does not consume most of them -- an assertion checking a free-text
answer against the plan would be checking something generation never used to shape the build,
passing or failing by coincidence rather than real traceability.

1. Module presence: every `architecture_decision.json.selected_modules` entry must resolve to a
   real `module.<id>.*` address in the plan (module ids have hyphens; a real composed plan's
   module label replaces them with underscores -- verified live against synthesizer.compose()
   output, synthesizer._label()).
2. Blueprint control mapping: each blueprint's `controls[]` claim maps (via a hand-authored,
   reviewed table below -- never derived from the English string at runtime) to a concrete
   plan-JSON check. A control with no mapping entry logs `control_unmapped` loudly -- never
   silently skipped, never counted as passed. Verified against the real demo blueprint's
   generated Terraform (terraform_generator.generate_aws_data_pipeline): two of the six
   plan-checkable controls are genuinely NOT fully upheld today (no log-group retention despite
   the "log retention" claim; no cost-anomaly-detection resource despite the "anomaly detection"
   hooks claim) -- real, previously invisible gaps this checker exists to surface, not
   hypothetical test cases.
3. Numeric ceilings: requirements.json's canonical `parse_budget_usd()` checked against the
   real plan's `aws_budgets_budget` resource. A parser returning (0, "") -- nothing parseable --
   skips the check entirely, never a pass or a block, matching the parser's own "never guess"
   contract. `parse_daily_gb()` is deliberately NOT independently re-checked here: its only
   plausible new-check candidate (cross-verifying architecture_model.conformance()'s own
   volume_tier()-driven TIER-* findings) would just re-verify logic conformance() already runs
   internally, not add a new independent signal -- scoped out and disclosed rather than built
   as low-value redundant surface.
"""
import os
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
import plan_reader  # noqa: E402
import requirements as reqgate  # noqa: E402


def _finding(fid, category, title, detail, severity, resource=None, finding_kind="standard"):
    return {"id": fid, "category": category, "title": title, "detail": detail,
            "severity": severity, "resource": resource, "finding_kind": finding_kind}


def _plan_malformed_finding(plan_json):
    """Shared fail-closed guard, used by every check_* function below (condition 4 of the
    approved Phase 4 scope: plan JSON malformed/unreadable must BLOCK the assertion pass itself,
    same evaluation_failed-style verdict shape as rego_gate.py, distinct from a legitimately
    empty/no-op plan). Returns a single-item finding list on malformed input, [] otherwise --
    callers short-circuit on a non-empty return."""
    _, error = plan_reader.read_resource_changes(plan_json, treat_absent_as_error=False)
    if error:
        return [_finding("INTENT-PLAN-MALFORMED", "Intent", "Plan JSON malformed",
                          f"Could not read resource_changes: {error}", "HIGH",
                          finding_kind="evaluation_failed")]
    return []


# ---------------------------------------------------------------------------
# 1. Module presence -- architecture_decision.json.selected_modules vs the real plan
# ---------------------------------------------------------------------------

def _module_label(module_id):
    return module_id.replace("-", "_")


def check_module_presence(architecture_decision, plan_json):
    """For every selected module id, confirm at least one resource_change address begins with
    `module.<label>.`. Verified live: a real composed plan (synthesizer.compose()) carries both
    an address prefix and a direct `module_address` field with the same value -- this checks
    the address prefix (works even if module_address is absent for some reason), not assumed
    from a single field."""
    malformed = _plan_malformed_finding(plan_json)
    if malformed:
        return malformed
    findings = []
    raw_rc, _ = plan_reader.read_resource_changes(plan_json, treat_absent_as_error=False)
    resource_changes = raw_rc or []
    prefixes = set()
    for rc in resource_changes:
        if not isinstance(rc, dict):
            continue
        addr = rc.get("address", "")
        if addr.startswith("module."):
            prefixes.add(addr.split(".")[1])

    selected = (architecture_decision or {}).get("selected_modules") or []
    for module_id in selected:
        label = _module_label(module_id)
        if label not in prefixes:
            findings.append(_finding(
                "INTENT-MODULE-MISSING", "Intent", "Selected module absent from real plan",
                f"architecture_decision.json selected '{module_id}' but no module.{label}.* "
                "resource appears in the plan -- the decision record and the real build have "
                "diverged.", "HIGH", resource=module_id))
    return findings


# ---------------------------------------------------------------------------
# 2. Blueprint control mapping -- hand-authored, reviewed, never derived from the control
#    string at runtime. Each entry is a callable: (plan_json) -> True (satisfied) / False
#    (violated) / None (not applicable to this plan's resource types at all).
# ---------------------------------------------------------------------------

def _has_type(config_resources_or_rcs, *needles):
    types = {r.get("type", "") for r in config_resources_or_rcs if isinstance(r, dict)}
    return any(any(n in t for n in needles) for t in types)


def _check_sse_kms(plan_json):
    rc, _ = plan_reader.read_resource_changes(plan_json, treat_absent_as_error=False)
    managed, _ = plan_reader.managed_only(rc or [])
    # Bare aws_s3_bucket only -- "s3_bucket" as a substring would also match its own sibling
    # types (aws_s3_bucket_versioning etc.), so this checks for exact-type presence.
    has_bucket = any(r.get("type") == "aws_s3_bucket" for r in managed)
    if not has_bucket:
        return None  # no storage in this plan at all -- not applicable
    has_kms = _has_type(managed, "aws_kms_key")
    has_sse = _has_type(managed, "server_side_encryption")
    return bool(has_kms and has_sse)


def _check_public_access_blocks(plan_json):
    rc, _ = plan_reader.read_resource_changes(plan_json, treat_absent_as_error=False)
    managed, _ = plan_reader.managed_only(rc or [])
    bucket_addrs = {plan_reader.base_address(r["address"]) for r in managed if r.get("type") == "aws_s3_bucket"}
    if not bucket_addrs:
        return None
    pab_configs = plan_reader.config_resources(plan_json)
    protected = set()
    for cfg in pab_configs:
        if cfg.get("type") != "aws_s3_bucket_public_access_block":
            continue
        refs = set(cfg.get("expressions", {}).get("bucket", {}).get("references", []))
        refs |= set(cfg.get("for_each_expression", {}).get("references", []))
        for bucket_addr in bucket_addrs:
            if bucket_addr in refs:
                protected.add(bucket_addr)
    return bucket_addrs.issubset(protected)


def _check_versioning_and_lifecycle(plan_json):
    rc, _ = plan_reader.read_resource_changes(plan_json, treat_absent_as_error=False)
    managed, _ = plan_reader.managed_only(rc or [])
    bucket_addrs = {plan_reader.base_address(r["address"]) for r in managed if r.get("type") == "aws_s3_bucket"}
    if not bucket_addrs:
        return None
    configs = plan_reader.config_resources(plan_json)

    def _referenced_by(sibling_type):
        covered = set()
        for cfg in configs:
            if cfg.get("type") != sibling_type:
                continue
            refs = set(cfg.get("expressions", {}).get("bucket", {}).get("references", []))
            refs |= set(cfg.get("for_each_expression", {}).get("references", []))
            covered |= (bucket_addrs & refs)
        return covered

    versioned = _referenced_by("aws_s3_bucket_versioning")
    lifecycled = _referenced_by("aws_s3_bucket_lifecycle_configuration")
    return bucket_addrs.issubset(versioned) and bucket_addrs.issubset(lifecycled)


def _check_scoped_iam(plan_json):
    """Returns True (satisfied), False (a resolved policy has a wildcard Resource), None (no
    IAM in this plan at all -- not applicable), or "unresolved" (at least one policy's content
    is genuinely unknown until apply -- e.g. built from a not-yet-created bucket's ARN, a real
    shape confirmed live against the demo blueprint's own generated Terraform). "unresolved"
    must never silently fall through to True: a real bug caught here before shipping -- the
    demo blueprint's IAM policies reference aws_s3_bucket.zone[*].arn, so `policy` itself is
    after_unknown at plan time, and treating "couldn't check" as "checked and fine" is exactly
    the fail-open shape this session's whole discipline exists to close."""
    import json as _json
    rc, _ = plan_reader.read_resource_changes(plan_json, treat_absent_as_error=False)
    managed, _ = plan_reader.managed_only(rc or [])
    roles = [r for r in managed if r.get("type") == "aws_iam_role"]
    policies = [r for r in managed if r.get("type") in ("aws_iam_role_policy", "aws_iam_policy")]
    if not roles and not policies:
        return None
    if not roles:
        return False  # policies with no dedicated role -- not "per-service" roles
    any_unresolved = False
    for p in policies:
        change = p.get("change") or {}
        if (change.get("after_unknown") or {}).get("policy"):
            any_unresolved = True
            continue
        after = change.get("after") or {}
        policy_str = after.get("policy")
        if not policy_str:
            continue
        try:
            doc = _json.loads(policy_str)
        except (TypeError, ValueError):
            return False  # can't parse a resolved value -- fail closed on the claim, not silently pass
        statements = doc.get("Statement", [])
        if isinstance(statements, dict):
            statements = [statements]
        for stmt in statements:
            resource = stmt.get("Resource")
            resources = resource if isinstance(resource, list) else [resource]
            if any(r == "*" for r in resources):
                return False
    return "unresolved" if any_unresolved else True


def _check_alarms_and_log_retention(plan_json):
    rc, _ = plan_reader.read_resource_changes(plan_json, treat_absent_as_error=False)
    managed, _ = plan_reader.managed_only(rc or [])
    types = {r.get("type", "") for r in managed}
    has_alarm = any("cloudwatch_metric_alarm" in t for t in types)
    has_log_group = any("cloudwatch_log_group" in t for t in types)
    if not has_alarm and not has_log_group:
        return None
    return has_alarm and has_log_group


def _check_budget_and_anomaly(plan_json):
    rc, _ = plan_reader.read_resource_changes(plan_json, treat_absent_as_error=False)
    managed, _ = plan_reader.managed_only(rc or [])
    types = {r.get("type", "") for r in managed}
    has_budget = any("budgets_budget" in t for t in types)
    has_anomaly = any("ce_anomaly" in t for t in types)
    if not has_budget and not has_anomaly:
        return None
    return has_budget and has_anomaly


# Hand-authored, reviewed mapping. A control string with no entry here logs `control_unmapped`
# -- never silently skipped, never counted as passed (docs/phase4_scope.md section 2/proof-bar
# item 2).
CONTROL_CHECKS = {
    "SSE-KMS for storage and logs": _check_sse_kms,
    "S3 public access blocks": _check_public_access_blocks,
    "Versioning and lifecycle policies": _check_versioning_and_lifecycle,
    "Per-service IAM roles with scoped resource permissions": _check_scoped_iam,
    "CloudWatch alarms and log retention": _check_alarms_and_log_retention,
    "Budget and anomaly detection hooks": _check_budget_and_anomaly,
    # "Terraform plan hash approval before apply" is deliberately absent: it is a claim about
    # the deploy gate's OWN process (plan_gate.py's hash-approval flow), not a property of the
    # generated Terraform -- no plan-JSON check can verify it. Logged as control_unmapped, not
    # silently dropped from the table.
}


def check_controls(blueprint, plan_json):
    malformed = _plan_malformed_finding(plan_json)
    if malformed:
        return malformed
    findings = []
    controls = (blueprint or {}).get("controls") or []
    for control in controls:
        check_fn = CONTROL_CHECKS.get(control)
        if check_fn is None:
            findings.append(_finding(
                "INTENT-CONTROL-UNMAPPED", "Intent", "Blueprint control has no mapped check",
                f"'{control}' is declared but has no plan-JSON verification -- named gap, not "
                "silently skipped.", "MEDIUM", resource=control, finding_kind="control_unmapped"))
            continue
        result = check_fn(plan_json)
        if result is None:
            continue  # not applicable to this plan's resource types -- not a failure
        if result == "unresolved":
            # Distinct from both pass and violation, same convention as G6's field_unresolved:
            # a value this claim depends on isn't known until apply. Must never silently fall
            # through to "no finding" (a real bug caught before this shipped -- see
            # _check_scoped_iam's docstring).
            findings.append(_finding(
                "INTENT-CONTROL-UNRESOLVED", "Intent", "Blueprint control not verifiable yet",
                f"'{control}' depends on a value that is unknown until apply -- cannot be "
                "verified from this plan alone.", "MEDIUM", resource=control,
                finding_kind="control_unresolved"))
            continue
        if not result:
            findings.append(_finding(
                "INTENT-CONTROL-VIOLATED", "Intent", "Blueprint control not upheld by the plan",
                f"'{control}' is declared but the real plan does not satisfy it.", "HIGH",
                resource=control))
    return findings


# ---------------------------------------------------------------------------
# 3. Numeric ceilings -- requirements.json's own canonical parsers, checked against the plan.
# ---------------------------------------------------------------------------

def check_numerics(requirements, plan_json):
    malformed = _plan_malformed_finding(plan_json)
    if malformed:
        return malformed
    findings = []
    budget, budget_text = reqgate.parse_budget_usd(requirements or {})
    if budget > 0:
        rc, _ = plan_reader.read_resource_changes(plan_json, treat_absent_as_error=False)
        managed, _ = plan_reader.managed_only(rc or [])
        budgets = [r for r in managed if r.get("type") == "aws_budgets_budget"]
        if not budgets:
            findings.append(_finding(
                "INTENT-BUDGET-MISSING", "Intent", "Budget ceiling declared but no budget resource in plan",
                f"requirements.json declares a budget ceiling (${budget:g}, from \"{budget_text}\") "
                "but the plan has no aws_budgets_budget resource.", "MEDIUM"))
    return findings


def evaluate(requirements=None, architecture_decision=None, blueprint=None, plan_json=None):
    """Run all three advisory claim classes against one real plan. Any missing input (no
    architecture_decision, no blueprint) simply skips that claim class -- a run using only one
    generation path (blueprint-based demo, or modules.py-catalog production) is not expected to
    have both records.

    Checks plan validity ONCE upfront (same evaluation_failed-style shape as rego_gate.py):
    malformed/unreadable plan JSON blocks the whole assertion pass rather than producing the
    same evaluation_failed finding once per claim class."""
    malformed = _plan_malformed_finding(plan_json)
    if malformed:
        return {"advisory": True, "evaluation_failed": True, "findings": malformed}
    findings = []
    if architecture_decision is not None:
        findings.extend(check_module_presence(architecture_decision, plan_json))
    if blueprint is not None:
        findings.extend(check_controls(blueprint, plan_json))
    if requirements is not None:
        findings.extend(check_numerics(requirements, plan_json))
    return {"advisory": True, "evaluation_failed": False, "findings": findings}
