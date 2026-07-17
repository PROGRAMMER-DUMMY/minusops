"""
Destructive-change gate — the plan-JSON classifier that decides whether a plan is eligible for
autonomous ship-on-green, or must route to the staged/guarded path.

Direction (docs/project_plan.md, generation-time-authoring architecture spec, Phase 1 of the
gate stack): validate/test/plan passing is necessary but not sufficient proof of safety for
stateful or destructive changes. This gate does not depend on any single citation to justify
existing -- the load-bearing evidence is TerraProbe's 71.4% deceptive-fix rate (arXiv 2606.26590,
peer-reviewed methodology, layered-oracle framework applied to 288 real repairs across three
models): a fix that passes every automated check while leaving the actual vulnerability in place,
confirming that "the checks passed" cannot be trusted as "this is safe" for exactly the class of
change this gate exists to route to staged review. One additional, secondary data point, corrected
here after review (2026-07): a single unverified preprint ("Hallucinated Resources, Brittle
Oracles, Decoupled Security") reports no detected correctness-to-security transfer at n=55
(Pearson r=0.20, p=0.14, 95% CI crossing zero -- a non-significant null result, not a weak positive
correlation; do not soften this to "weakly correlated" if it is ever restated). Read correctly, a
null result here is corroborating, not incidental: it means correctness signals (validate/test/plan
passing) carry no detectable predictive power over security outcomes, which is the same conclusion
TerraProbe's own real, adjudicated failures demonstrate directly. This citation is included as
color, single-source and unverified in its own right -- if TerraProbe's number were ever
retracted, this module's autonomy boundary would still stand on its own design logic (validate/
test/plan is a syntax-and-plan-shape check, never a safety check) and should never be presented as
though it depends on the preprint. The autonomy boundary this module enforces:

  - Ship-on-green is allowed ONLY for net-new, non-destructive, non-stateful creates OF A
    REVIEWED-SAFE RESOURCE TYPE (see AUTO_SHIP_ELIGIBLE_TYPES below).
  - Any plan showing delete/replace actions, touching a stateful/data resource type, touching
    IAM, touching a resource type nobody has reviewed as safe yet, or touching any Databricks
    resource (see below) routes to the staged/guarded path.
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

FAIL-CLOSED ON UNKNOWN RESOURCE TYPE (docs/g5_autonomy_boundary_scope.md, Phase 6 Step 0,
2026-07-14): STATEFUL_RESOURCE_TYPES/IAM_RESOURCE_TYPES alone used to be the ENTIRE gating
condition -- membership meant "stage it," but a type in NEITHER set produced no finding at all,
regardless of how genuinely stateful or sensitive it was. That is an allowlist-of-DANGER, which
is fail-OPEN by construction: any resource type this repo's fixed 16-module catalog has never
produced -- which is exactly what generation-time authoring exists to produce -- would silently
ship autonomously. Real, not hypothetical: confirmed live, `aws_dynamodb_table` (create-only,
genuinely stateful, not in either set) classified `autonomous_eligible=True` before this fix.
STATEFUL_RESOURCE_TYPES/IAM_RESOURCE_TYPES stay exactly as they are -- they still give the most
specific, most informative staged-reason when a type is known-dangerous -- but they are no
longer the only gate. AUTO_SHIP_ELIGIBLE_TYPES (below) is a REVIEWED allowlist of types
confirmed safe, same fail-closed shape as ephemeral_apply.py's RESOURCE_TYPE_ALLOWLIST (G9): a
type absent from it -- because it's dangerous, or because nobody has reviewed it yet -- stages,
tagged `unreviewed_resource_type`, distinguishable in the audit trail from a known-dangerous
finding. No guessing in either direction: membership is a deliberate, reviewed fact.

A heuristic (does the type NAME or SCHEMA SHAPE "look" stateful/sensitive) was considered and
rejected -- found, not assumed, to be structurally blind to a real case in this repo's own
catalog: `aws_s3_bucket_policy` holds no data itself and has no stateful-looking schema (a single
opaque JSON string), yet its CONTENT is exactly what this session's own G6 SEC-07 rule exists to
catch (a bare `Principal: "*"` grants public access). A heuristic keyed on shape would very
plausibly miss it; an explicit review does not, because nobody has reviewed it as safe, so it
simply isn't on the list -- no inference required.
"""
import json
import sys

import plan_reader

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

# Types explicitly reviewed and found NOT safe to auto-ship, despite being neither
# STATEFUL_RESOURCE_TYPES nor IAM_RESOURCE_TYPES -- distinct from AUTO_SHIP_ELIGIBLE_TYPES
# simply lacking an entry (`unreviewed_resource_type`, meaning "nobody has looked at this yet").
# A finding here means "reviewed, and rejected," a materially different, more informative fact
# for an audit-chain reader than "never reviewed" -- same reasoning that keeps
# stateful_resource_type/iam_resource_type as their own distinct reasons instead of collapsing
# everything into one generic "staged" bucket.
REVIEWED_UNSAFE_TYPES = frozenset({
    # aws_s3_bucket_policy: its own schema carries no stateful shape (a single opaque policy
    # string) but its CONTENT can grant public access -- the exact case this session's own G6
    # SEC-07 rule exists for. G6 is shadow-only, so this classifier is the only thing that could
    # actually stage it today.
    "aws_s3_bucket_policy",
    # aws_default_security_group: a real decision made here, not defaulted either way. Confirmed
    # live against this repo's OWN real module (modules/networking-vpc/main.tf): even this
    # repo's correctly-configured usage sets `egress { cidr_blocks = ["0.0.0.0/0"] }` -- an
    # unrestricted CIDR block is present in the type's typical, intended real-world
    # configuration here, not merely a hypothetical misconfiguration. A security group's content
    # (which direction, which CIDR) is a network-layer equivalent of an IAM/KMS/S3 policy's
    # Principal/Action content, and this classifier reads only the plan's resource type and
    # action, never rule content -- it has no way to tell "this occurrence is the standard
    # self-referencing-ingress pattern" from "this occurrence just opened ingress to 0.0.0.0/0"
    # any more than a heuristic could. Asymmetric downside decided this: staging a genuine
    # hardening change costs one human glance; auto-shipping the one that opens inbound to the
    # world is the exact failure mode this whole fix exists to prevent.
    "aws_default_security_group",
})

# Reviewed allowlist of resource types confirmed safe to auto-ship (docs/
# g5_autonomy_boundary_scope.md section 3) -- the inverted gate. A type absent from this set
# stages, tagged `unreviewed_resource_type` (or `reviewed_unsafe_resource_type` if it's in
# REVIEWED_UNSAFE_TYPES above). Reviewed against the real 41-type catalog (ephemeral_apply.py's
# RESOURCE_TYPE_ALLOWLIST) one type at a time, not migrated wholesale from "not currently
# flagged".
#
# CONFIG-DEPENDENT ENTRIES -- RESOLVED (docs/phase6_step1_authoring_scope.md section 4.2,
# 2026-07-14). 6 entries were flagged at Step 0 for Step-1 re-examination before generation
# could produce novel configurations of them (the scope doc's own prose said "7"; the actual
# count in this set has always been 6 -- a real miscount in the scope doc, corrected here
# rather than silently carried forward). Each got its own per-type disposition, same standard
# `aws_default_security_group`'s exclusion was held to above -- decided, not left implicit:
#
#   (a) NEW G6 RULE, stays eligible -- `aws_redshiftserverless_workgroup`, `aws_subnet`,
#       `aws_s3_object`. Each has a real, schema-verified boolean/string attribute that flips
#       public exposure without changing resource type (`publicly_accessible`,
#       `map_public_ip_on_launch`, `acl`). SEC-08/SEC-09/SEC-10 (policy/g6/rules.rego) now
#       check exactly those attributes, shadow-proven zero-FP the same way SEC-06/SEC-07 were.
#       A generated instance setting the risky attribute is caught on CONTENT by G6, even
#       though G5 still only sees the type and stays silent.
#   (b) NO G6 RULE POSSIBLE, stays eligible on reasoned exception -- `aws_glue_job`,
#       `aws_kinesisanalyticsv2_application`, `aws_sfn_state_machine`. Each carries an
#       arbitrary executable payload (a Spark script, a Flink/SQL app, a state-machine
#       definition) whose risk is in what it DOES at runtime, not in any single attribute a
#       plan-time Rego rule could pattern-match -- there is no attribute-content check
#       equivalent to SEC-08/09/10 to write here. The actual privilege boundary for all three
#       is the IAM role each one assumes: a genuinely new role attached to one of these is
#       itself a separate `aws_iam_role`/`aws_iam_policy` resource, independently caught by
#       SEC-02/SEC-05 (wildcard resource/action, missing external ID) if it's newly authored.
#       Accepted as the real, disclosed boundary -- not a gap silently left open -- because
#       the alternative (moving all three to REVIEWED_UNSAFE_TYPES) would stage every future
#       occurrence of an already-reviewed-safe type shape for a risk this gate structurally
#       cannot see any better staged than un-staged.
AUTO_SHIP_ELIGIBLE_TYPES = frozenset({
    "aws_athena_workgroup",
    "aws_budgets_budget",
    "aws_cloudwatch_event_rule",
    "aws_cloudwatch_event_target",
    "aws_cloudwatch_metric_alarm",
    "aws_eip",
    "aws_emrserverless_application",
    "aws_glue_catalog_database",
    "aws_glue_job",                        # CONFIG-DEPENDENT (b): no G6 rule, IAM role bounds it
    "aws_glue_registry",
    "aws_glue_schema",
    "aws_glue_trigger",
    "aws_internet_gateway",
    "aws_kinesisanalyticsv2_application",  # CONFIG-DEPENDENT (b): no G6 rule, IAM role bounds it
    "aws_kms_alias",
    "aws_nat_gateway",
    "aws_redshiftserverless_workgroup",    # CONFIG-DEPENDENT (a): G6 SEC-08 covers publicly_accessible
    "aws_route_table",                     # real bug: reviewed safe, initially left out of this set
    "aws_route_table_association",
    "aws_s3_bucket_lifecycle_configuration",
    "aws_s3_bucket_public_access_block",
    "aws_s3_bucket_server_side_encryption_configuration",
    "aws_s3_bucket_versioning",
    "aws_s3_object",                       # CONFIG-DEPENDENT (a): G6 SEC-10 covers acl
    "aws_sfn_state_machine",               # CONFIG-DEPENDENT (b): no G6 rule, IAM role bounds it
    "aws_sns_topic",
    "aws_sns_topic_subscription",
    "aws_subnet",                          # CONFIG-DEPENDENT (a): G6 SEC-09 covers map_public_ip_on_launch
    "aws_vpc",
    "aws_vpc_endpoint",
    # Not cloud resources -- test-utility types with zero cloud footprint, used by this repo's
    # own test suite as create/delete/replace and end-to-end apply fixtures without needing
    # real credentials. Reviewed and added deliberately, same as any other entry, not a
    # real-world type judgment. A real gap found running this fix's own CI proof (not caught
    # locally, since this repo's test files were never run exhaustively against the fixed
    # classifier before pushing -- a real process gap, corrected by the repo-wide grep below,
    # not just these two additions): confirmed via `grep -rn 'resource "..."' tests/*.py` that
    # only these two non-cloud types are used as test fixtures anywhere in this repo's test
    # suite, so this is now a complete, not partial, exemption list.
    "random_id",             # hashicorp/random, tests/test_destructive_change_gate.py
    "terraform_data",        # built into Terraform core itself, tests/test_gate_e2e.py
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
    # plan_reader.py (G4 consolidation, docs/phase4_scope.md) -- shared with architecture_
    # model.py. G5's own policy (absent resource_changes is a fail-closed BLOCK, not "nothing to
    # check") is preserved via treat_absent_as_error=True; this is a deliberate difference from
    # G6's shadow-mode reader, which treats absent as "zero managed changes" -- see plan_reader's
    # module docstring. Not changed here, not in scope for this consolidation.
    raw_resource_changes, error = plan_reader.read_resource_changes(plan_json, treat_absent_as_error=True)
    if error:
        return _fail_closed(error)

    # Exclude mode == "data" only (not: require mode == "managed"). A data source's plan-time
    # "read" is not a resource being changed and must never be treated as a destructive action
    # -- same filter coverage_audit.py already applies, see its test_classify_ignores_data_
    # sources. An ALLOWLIST on "managed" would fail OPEN on a missing/unrecognized `mode` field
    # (the original bug this docstring refers to); a DENYLIST on "data" only excludes what
    # we're confident is a non-mutating read, so anything else stays in scope.
    resource_changes, malformed = plan_reader.managed_only(raw_resource_changes)
    findings = [{"address": None, "type": None, **m} for m in malformed]

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
        elif rtype in REVIEWED_UNSAFE_TYPES:
            # Distinct from unreviewed_resource_type below: this type WAS reviewed, and the
            # review's answer was "not safe to auto-ship" (docs/g5_autonomy_boundary_scope.md
            # section 3) -- a materially different fact for an audit-chain reader than "nobody
            # has looked at this yet."
            findings.append({"address": address, "type": rtype, "reason": "reviewed_unsafe_resource_type"})
        elif rtype.startswith(_DATABRICKS_PREFIX):
            # docs/g5_autonomy_boundary_scope.md was scoped to the real 41 AWS types only --
            # Databricks resource types were deliberately NOT reviewed into AUTO_SHIP_ELIGIBLE_
            # TYPES, matching G9's own AWS-only scope. Real bug found running this fix's own
            # 16-module regression proof: without this branch, a Databricks type absent from
            # STATEFUL_RESOURCE_TYPES (e.g. databricks_mws_credentials) fell through to
            # unreviewed_resource_type -- technically not wrong, but redundant and out of this
            # scope's stated boundary, since EVERY databricks_* type already, unconditionally,
            # never autonomous-eligible via `reduced_assurance` below regardless of this check.
            # Skip rather than double-flag; Databricks resource-type review is real, future,
            # separately-scoped work, not silently declared done by this AWS-only fix.
            pass
        elif rtype not in AUTO_SHIP_ELIGIBLE_TYPES:
            # The fix (docs/g5_autonomy_boundary_scope.md): a type that's neither known-
            # dangerous NOR reviewed-safe stages -- because it's genuinely new (generation-time
            # authoring's whole point) or because it's simply been overlooked, never because
            # "we don't recognize it" was silently read as "therefore fine."
            findings.append({"address": address, "type": rtype, "reason": "unreviewed_resource_type"})

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
