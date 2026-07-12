"""
plan_reader.py -- shared, fail-closed reading of real Terraform plan JSON (`terraform show
-json` shape).

Consolidates plan-JSON access that existed independently in three places (the G4-consolidation
condition from Phase 4's approved scope, docs/phase4_scope.md section 3): destructive_change_
gate.py's own inline resource_changes validation, architecture_model.py's extract_resources()/
module_dependencies() (which didn't fail closed at all -- `(plan or {}).get(...)` silently
defaulted and could crash on a malformed entry), and rego_gate.py's top-level shape check.

rego_gate.py's OWN Rego rules (policy/g6/rules.rego) read plan JSON natively inside Rego and
cannot import a Python module -- that is a hard language boundary, not a deferred migration.
This module is Python-side only; G6 stays exactly as closed, its shape facts independently
verified there already.

Every shape fact here was verified live against real `terraform plan`/`show -json` output this
session (not assumed from documentation), several correcting wrong initial assumptions:
  - `resource_changes` is OMITTED entirely (not an empty list) when a plan has zero managed
    resource changes (a data-source-only or genuine no-op plan) -- confirmed twice.
  - Data sources never appear in `resource_changes`; they resolve into
    `prior_state.values.root_module.resources` (mode == "data"), already-resolved `.values`,
    no `.change`/`.after_unknown` wrapper (data sources resolve synchronously at plan time).
  - A `for_each`/`count` sibling relationship is expressed at the config's BASE resource address
    in `configuration.root_module.resources[].for_each_expression.references`, never inside
    `expressions.<attr>.references` (which only ever holds the symbolic `each.value`/
    `each.value.id`) -- caught as a real false-positive bug in policy/g6/rules.rego and fixed
    there; this module exposes the same `for_each_expression` field so any future Python-side
    consumer doesn't have to rediscover it.
  - A real multi-module composition (`synthesizer.compose()`) produces `module.<id>.*` addresses
    (id with hyphens replaced by underscores, see synthesizer._label) with a convenient
    `module_address` field directly on each resource_change entry -- verified live against a
    real composed plan (storage-medallion-s3 + compaction-glue, dummy AWS credentials).

Two existing callers (destructive_change_gate.py's classify(), a closed, real-enforcing gate;
and architecture_model.py's conformance(), advisory-only) have DIFFERENT, deliberate policies
for what an absent `resource_changes` means -- G5 treats it as a fail-closed block (a genuinely
no-op/data-source-only plan still routes to the staged path, conservative but never a safety
regression to change post-close); a shadow/advisory reader (like G6's) treats it as "nothing
managed to check." This module does not force those two policies to converge -- it exposes the
raw fact (`resource_changes` present vs. absent vs. wrong-typed) and a `treat_absent_as_error`
flag so each caller keeps its own already-proven policy.
"""


def read_resource_changes(plan_json, treat_absent_as_error):
    """Return (resource_changes_list_or_None, error_reason_or_None).

    error_reason is one of "plan_json_not_a_dict", "resource_changes_missing_or_null" (only
    when treat_absent_as_error is True), or "resource_changes_not_a_list". A non-error return
    is always a real list (possibly empty) -- never None paired with error_reason None.
    """
    if not isinstance(plan_json, dict):
        return None, "plan_json_not_a_dict"
    raw = plan_json.get("resource_changes")
    if raw is None:
        if treat_absent_as_error:
            return None, "resource_changes_missing_or_null"
        return [], None
    if not isinstance(raw, list):
        return None, "resource_changes_not_a_list"
    return raw, None


def managed_only(resource_changes):
    """Filter to managed resource changes, reporting malformed entries distinctly rather than
    silently dropping or crashing on them. Returns (managed_list, malformed_entries) where each
    malformed entry is {"reason": "malformed_resource_change_entry"} -- callers that want a
    finding shape wrap this themselves (address/type are never available for a non-dict entry).

    Excludes mode == "data" only (not: requires mode == "managed"). A data source's plan-time
    read is not a resource being changed and must never be treated as a mutation. A DENYLIST on
    "data" (not an allowlist on "managed") is deliberate: an unrecognized/missing `mode` field
    stays in scope rather than being silently excluded -- the exact fail-open shape a systematic
    sweep of destructive_change_gate.py found and closed.
    """
    managed = []
    malformed = []
    for rc in resource_changes:
        if not isinstance(rc, dict):
            malformed.append({"reason": "malformed_resource_change_entry"})
            continue
        if rc.get("mode") == "data":
            continue
        managed.append(rc)
    return managed, malformed


def data_sources(plan_json):
    """Data source reads, normalized from `prior_state.values.root_module.resources` (never
    `resource_changes` -- verified live, twice, that data sources don't appear there at all).
    Each entry: {"address", "type", "mode": "data", "values"}. Fails soft (empty list) on any
    missing/malformed nesting -- this is a read of already-resolved state, not a change to
    classify, so there is no "block" outcome here; callers needing fail-closed behavior over
    resource_changes should use read_resource_changes above."""
    prior_state = plan_json.get("prior_state") if isinstance(plan_json, dict) else None
    if not isinstance(prior_state, dict):
        return []
    resources = (
        prior_state.get("values", {})
        if isinstance(prior_state.get("values"), dict) else {}
    ).get("root_module", {})
    resources = resources.get("resources", []) if isinstance(resources, dict) else []
    if not isinstance(resources, list):
        return []
    return [r for r in resources if isinstance(r, dict) and r.get("mode") == "data"]


def config_resources(plan_json):
    """`configuration.root_module.resources` -- the static, plan-time config for every
    resource (managed and data), independent of whether values have resolved. Each entry may
    carry `for_each_expression` as a SEPARATE top-level field from `expressions` (verified
    live -- see module docstring); callers correlating for_each siblings must check both."""
    cfg = plan_json.get("configuration") if isinstance(plan_json, dict) else None
    if not isinstance(cfg, dict):
        return []
    root = cfg.get("root_module")
    if not isinstance(root, dict):
        return []
    resources = root.get("resources")
    return resources if isinstance(resources, list) else []


def module_calls(plan_json):
    """`configuration.root_module.module_calls` -- {module_name: call_config}, used to trace
    which modules reference which via input expressions."""
    cfg = plan_json.get("configuration") if isinstance(plan_json, dict) else None
    if not isinstance(cfg, dict):
        return {}
    root = cfg.get("root_module")
    if not isinstance(root, dict):
        return {}
    calls = root.get("module_calls")
    return calls if isinstance(calls, dict) else {}


def base_address(address):
    """Strip a trailing for_each/count index suffix (`aws_s3_bucket.zone["bronze"]` ->
    `aws_s3_bucket.zone`) -- a for_each/count sibling relationship in `configuration` is only
    ever expressed at this base form, never per expanded instance (verified live; see module
    docstring)."""
    import re
    return re.sub(r"\[[^\]]*\]$", "", address or "")
