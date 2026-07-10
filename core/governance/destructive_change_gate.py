"""
Destructive-change gate — the plan-JSON classifier that decides whether a plan is eligible for
autonomous ship-on-green, or must route to the staged/guarded path.

Direction (docs/project_plan.md, generation-time-authoring architecture spec, Phase 1 of the
gate stack): validate/test/plan passing is necessary but not sufficient proof of safety for
stateful or destructive changes (external research cited: TerraProbe's 71.4% deceptive-fix rate
that passes every automated check; correctness-doesn't-predict-security, r=0.20). The autonomy
boundary this module enforces:

  - Ship-on-green is allowed ONLY for net-new, non-destructive, non-stateful creates.
  - Any plan showing delete/replace actions, touching a stateful/data resource type, touching
    IAM, or touching any Databricks resource (see below) routes to the staged/guarded path.
  - This is a hard, non-overridable gate: `classify()` never asks an LLM or a human for an
    opinion, it only reads `terraform show -json` plan facts.

Action-shape ground truth (empirically verified against real `terraform show -json` output,
Terraform 1.15.7, via a local-only `random_id` resource forced through both replace orderings --
not assumed from memory, per this project's own verification discipline):

  create                                 -> actions == ["create"]
  delete                                 -> actions == ["delete"]
  replace (destroy-then-create, default) -> actions == ["delete", "create"]
  replace (create_before_destroy=true)   -> actions == ["create", "delete"]

Both replace orderings also carry a non-empty `replace_paths` list and an `action_reason` field
(e.g. "replace_because_cannot_update") -- used here for reporting, not as the primary signal.
The primary gate is a strict allowlist (exactly `["create"]` passes) specifically because two
real, different orderings exist for the same underlying operation: enumerating "bad" shapes
risks missing an ordering; allowlisting the one safe shape can't be fooled by an ordering it
doesn't recognize.

Databricks/LocalStack asymmetry: the ephemeral-apply gate (G9 in the architecture spec) is
LocalStack, which emulates AWS APIs only -- it has no Databricks coverage. A Databricks-touching
change therefore reaches "green" with one fewer real gate behind it than an AWS-only change.
This module makes that asymmetry an explicit, structural part of the classification (see
`reduced_assurance`) rather than letting it be invisible: any plan touching a `databricks_*`
resource type is never autonomous-eligible, regardless of action shape, until a real Databricks
sandbox-workspace apply equivalent to G9 exists.
"""
import json
import sys

_SAFE_ACTIONS = ("create",)

# Scoped deliberately to what MinusOps' own 16 modules can actually produce today (see
# modules/*/main.tf) -- not a general-purpose cloud-resource classifier. Extend this list when
# a new module introduces a new data-bearing or catastrophic-blast-radius resource type; don't
# try to pre-empt resource types nothing in this repo provisions.
STATEFUL_RESOURCE_TYPES = frozenset({
    "aws_s3_bucket",                       # holds objects (data)
    "aws_kms_key",                         # anything encrypted under it is unrecoverable if lost
    "aws_redshiftserverless_namespace",    # holds warehouse data (tables/schemas)
    "aws_glue_catalog_table",              # catalog entry pointing at real underlying data
    "aws_kinesis_stream",                  # in-flight/retained streaming data
    "aws_kinesis_firehose_delivery_stream",
    "aws_mwaa_environment",                # DAG run history/connections/variables; ~20-30min to recreate
    "databricks_metastore",                # root of the entire Unity Catalog governance tree
    "databricks_metastore_assignment",     # governs which workspace can reach which metastore's data
    "databricks_catalog",                  # schemas/tables/permissions
    "databricks_mws_workspaces",           # root of an entire environment (notebooks, jobs, clusters)
})

# IAM is a separate dimension from "holds data" -- a privilege-escalation-risk category the
# architecture spec calls out explicitly ("high IAM changes"). Routed to staged regardless of
# action, including a first-time create, same reasoning as STATEFUL_RESOURCE_TYPES: the risk is
# in what the resource TYPE represents, not only in what this specific plan does to it.
IAM_RESOURCE_TYPES = frozenset({
    "aws_iam_role",
    "aws_iam_role_policy",
})

_DATABRICKS_PREFIX = "databricks_"

_REDUCED_ASSURANCE_REASON = (
    "LocalStack (the AWS ephemeral-apply gate, G9) has no Databricks coverage -- a "
    "Databricks-touching change has one fewer real gate behind it than an AWS-only change "
    "until a real Databricks sandbox-workspace apply equivalent exists."
)


def _fail_closed(reason, address=None, rtype=None):
    """Every malformed-input path below returns through here: never a crash, never a silent
    'nothing to gate' -- a plan/entry this classifier can't understand is exactly the case it
    exists to catch, not an edge case to shrug off."""
    return {
        "autonomous_eligible": False,
        "findings": [{"address": address, "type": rtype, "reason": reason}],
        "reduced_assurance": False,
        "reduced_assurance_reason": None,
        "databricks_resources": [],
        "resource_change_count": 0,
    }


def classify(plan_json):
    """Classify a parsed `terraform show -json` plan. Fail-closed on any malformed input --
    2026-07-10 audit finding: the mode-field fix (below) was one gap found by accident; a
    systematic sweep of every field this function reads found five more of the same shape
    (three silent fail-opens, three crashes instead of a graceful fail-closed), all fixed here
    together. "Real terraform show -json always sets X" is exactly the reasoning that caused
    the original mode bug -- this function no longer assumes well-formed input anywhere, it
    only classifies a genuinely empty, well-typed `resource_changes: []` as autonomous-eligible
    (a real no-op plan, correctly safe), never a missing/malformed one."""
    if not isinstance(plan_json, dict):
        return _fail_closed("plan_json_not_a_dict")

    raw_resource_changes = plan_json.get("resource_changes")
    if raw_resource_changes is None:
        return _fail_closed("resource_changes_missing_or_null")
    if not isinstance(raw_resource_changes, list):
        return _fail_closed("resource_changes_not_a_list")

    # Exclude mode == "data" only (not: require mode == "managed"). A data source's plan-time
    # "read" is not a resource being changed and must never be treated as a destructive action
    # -- same filter coverage_audit.py already applies, see its test_classify_ignores_data_
    # sources. An ALLOWLIST on "managed" would fail OPEN on a missing/unrecognized `mode` field
    # (the original bug this docstring refers to); a DENYLIST on "data" only excludes what
    # we're confident is a non-mutating read, so anything else stays in scope.
    resource_changes = []
    findings = []
    for rc in raw_resource_changes:
        if not isinstance(rc, dict):
            findings.append({"address": None, "type": None, "reason": "malformed_resource_change_entry"})
            continue
        if rc.get("mode") == "data":
            continue
        resource_changes.append(rc)

    for rc in resource_changes:
        address = rc.get("address")
        rtype = rc.get("type")
        change = rc.get("change")

        # A missing or non-string type can't be looked up in STATEFUL_RESOURCE_TYPES/
        # IAM_RESOURCE_TYPES (a real lookup would just safely return False) -- but silently
        # treating "don't know what this is" as "therefore not stateful/IAM" is the exact
        # fail-open shape this sweep exists to close. Malformed type data blocks outright.
        if not isinstance(rtype, str) or not rtype:
            findings.append({"address": address, "type": rtype, "reason": "missing_or_invalid_resource_type"})
            continue
        if not isinstance(change, dict):
            findings.append({"address": address, "type": rtype, "reason": "malformed_change_block"})
            continue

        actions = tuple(change.get("actions") or [])
        if actions != _SAFE_ACTIONS:
            findings.append({
                "address": address, "type": rtype, "reason": "non_create_action",
                "actions": list(actions),
                "replace_paths": change.get("replace_paths"),
                "action_reason": rc.get("action_reason"),
            })
            continue
        if rtype in STATEFUL_RESOURCE_TYPES:
            findings.append({"address": address, "type": rtype, "reason": "stateful_resource_type"})
        elif rtype in IAM_RESOURCE_TYPES:
            findings.append({"address": address, "type": rtype, "reason": "iam_resource_type"})

    databricks_resources = sorted(
        rc.get("address") for rc in resource_changes
        if isinstance(rc.get("type"), str) and rc["type"].startswith(_DATABRICKS_PREFIX)
    )

    autonomous_eligible = not findings and not databricks_resources
    return {
        "autonomous_eligible": autonomous_eligible,
        "findings": findings,
        "reduced_assurance": bool(databricks_resources),
        "reduced_assurance_reason": _REDUCED_ASSURANCE_REASON if databricks_resources else None,
        "databricks_resources": databricks_resources,
        "resource_change_count": len(resource_changes),
    }


def classify_file(path):
    with open(path, encoding="utf-8") as f:
        plan_json = json.load(f)
    return classify(plan_json)


def _format(result):
    if result["autonomous_eligible"]:
        return f"[destructive-change-gate] AUTONOMOUS-ELIGIBLE ({result['resource_change_count']} resource change(s), all create-only)"
    lines = [f"[destructive-change-gate] STAGED PATH REQUIRED ({len(result['findings'])} finding(s))"]
    for f_ in result["findings"]:
        detail = f_["reason"]
        if f_["reason"] == "non_create_action":
            detail += f" actions={f_['actions']}"
        lines.append(f"  - {f_['address']} ({f_['type']}): {detail}")
    if result["reduced_assurance"]:
        lines.append(f"  - reduced assurance: {result['reduced_assurance_reason']}")
        for addr in result["databricks_resources"]:
            lines.append(f"    - {addr}")
    return "\n".join(lines)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python core/governance/destructive_change_gate.py <plan.json>", file=sys.stderr)
        return 2
    result = classify_file(argv[0])
    print(_format(result))
    print(json.dumps(result, indent=2))
    return 0 if result["autonomous_eligible"] else 1


if __name__ == "__main__":
    sys.exit(main())
