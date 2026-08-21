"""
Destructive-change gate — the plan-JSON classifier that decides whether a plan is eligible for
autonomous ship-on-green, or must route to the staged/guarded path.

Why it exists: validate/test/plan passing is a syntax-and-plan-shape check, never a safety
check, so it is necessary but not sufficient proof of safety for stateful or destructive
changes. The supporting evidence is TerraProbe's 71.4% deceptive-fix rate (arXiv 2606.26590,
peer-reviewed, 288 real repairs across three models): a fix that passes every automated check
while leaving the vulnerability in place. A secondary, unverified preprint ("Hallucinated
Resources, Brittle Oracles, Decoupled Security") reports no detected correctness-to-security
transfer at n=55 (Pearson r=0.20, p=0.14, 95% CI crossing zero). That is a non-significant
NULL result, not a weak positive correlation -- do not soften it to "weakly correlated" if it
is ever restated. It corroborates rather than carries the argument: the boundary below stands
on its own design logic even if either citation were retracted, and must never be presented
as depending on the preprint. The autonomy boundary this module enforces:

  - Ship-on-green is allowed ONLY for net-new, non-destructive, non-stateful creates OF A
    REVIEWED-SAFE RESOURCE TYPE (see AUTO_SHIP_ELIGIBLE_TYPES below).
  - Any plan showing delete/replace actions, touching a stateful/data resource type, touching
    IAM, touching a resource type nobody has reviewed as safe yet, or touching any Databricks
    resource (see below) routes to the staged/guarded path.
  - This is a hard, non-overridable gate: `classify()` never asks an LLM or a human for an
    opinion, it only reads `terraform show -json` plan facts.

Action-shape ground truth, verified against real `terraform show -json` output (Terraform
1.15.7) rather than assumed from documentation:

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

FAIL-CLOSED ON UNKNOWN RESOURCE TYPE (docs/g5_autonomy_boundary_scope.md). The gate is the
allowlist AUTO_SHIP_ELIGIBLE_TYPES, not the denylists. Gating on STATEFUL_RESOURCE_TYPES/
IAM_RESOURCE_TYPES membership alone is an allowlist-of-DANGER, fail-OPEN by construction: a
type in neither set produces no finding however stateful it is, so `aws_dynamodb_table`
(create-only, genuinely stateful, in neither set) classified `autonomous_eligible=True` under
that design. Generation-time authoring exists precisely to emit types no fixed catalog has
seen. The denylists stay because they give the most specific staged-reason for a known-
dangerous type, but they are not the gate. A type absent from AUTO_SHIP_ELIGIBLE_TYPES --
dangerous, or merely unreviewed -- stages, tagged `unreviewed_resource_type` and so
distinguishable in the audit trail from a known-dangerous finding. Same fail-closed shape as
ephemeral_apply.py's RESOURCE_TYPE_ALLOWLIST (G9). Membership is a reviewed fact, never a
guess in either direction.

Do not replace the allowlist with a heuristic over type NAME or SCHEMA SHAPE. It is
structurally blind to a real case in this repo's own catalog: `aws_s3_bucket_policy` holds no
data and has no stateful-looking schema (one opaque JSON string), yet its CONTENT is what G6's
SEC-07 rule catches (a bare `Principal: "*"` grants public access). Explicit review handles it
without inference -- nobody reviewed it safe, so it is simply not on the list.

Depends on: plan_reader
Shells out to: nothing -- it reads already-parsed plan JSON
Used by: core/governance/plan_gate.py, core/governance/address_churn.py
    (STATEFUL_RESOURCE_TYPES)
"""
import json
import sys

import plan_reader

_SAFE_ACTIONS = ("create",)

# Scoped deliberately to what this repo's own modules can produce today (see modules/*/main.tf)
# -- not a general-purpose cloud-resource classifier. Extend it when a new module introduces a
# data-bearing or catastrophic-blast-radius type; do not pre-empt types nothing here provisions.
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
    # Same bar as the entries above: what the TYPE represents, not what one plan does to it.
    "aws_sqs_queue",                       # holds in-flight events; a replace drops the backlog
    "aws_secretsmanager_secret",           # the container IS the secret's identity; recreating
                                           # it orphans every consumer's ARN reference
    # aws_dynamodb_table: this is the type test_destructive_change_gate.py's own regression-lock
    # comment names as the confirmed historical fail-open gap (see that file). It is reviewed IN
    # here, not left unreviewed, now that modules/metadata-control-table declares one: it holds
    # live pipeline-config rows every DAG queries at parse time, so a replace/recreate silently
    # blanks every pipeline's schedule and cluster sizing until the table is repopulated -- the
    # same "holds data" bar as aws_s3_bucket and aws_kinesis_stream above.
    "aws_dynamodb_table",
})

# IAM is a separate dimension from "holds data" -- a privilege-escalation-risk category the
# architecture spec calls out explicitly ("high IAM changes"). Routed to staged regardless of
# action, including a first-time create, same reasoning as STATEFUL_RESOURCE_TYPES: the risk is
# in what the resource TYPE represents, not only in what this specific plan does to it.
IAM_RESOURCE_TYPES = frozenset({
    "aws_iam_role",
    "aws_iam_role_policy",
    # Privilege-granting in exactly the sense this set exists for: an instance profile hands a
    # role to every EC2 node in a cluster, and a policy attachment binds an AWS-MANAGED policy
    # whose contents this repo does not author and cannot scan (compute-emr-ec2-spot attaches
    # AmazonEMRServicePolicy_v2). A wildcard inside a managed policy is invisible to SEC-02.
    "aws_iam_instance_profile",
    "aws_iam_role_policy_attachment",
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
    # string) but its CONTENT can grant public access -- the case G6's SEC-07 rule exists for.
    # G6 is shadow-only, so this classifier is the only thing that could actually stage it.
    "aws_s3_bucket_policy",
    # aws_default_security_group: excluded on a decision, not by default. This repo's own
    # correctly-configured usage (modules/networking-vpc/main.tf) sets
    # `egress { cidr_blocks = ["0.0.0.0/0"] }`, so an unrestricted CIDR block is present in the
    # type's intended configuration here, not only in a misconfiguration. A security group's
    # content (direction, CIDR) is the network-layer equivalent of an IAM/KMS/S3 policy's
    # Principal/Action content, and this classifier reads only resource type and action, never
    # rule content -- it cannot tell the standard self-referencing-ingress pattern from one that
    # just opened ingress to the world. Asymmetric downside decides it: staging a genuine
    # hardening change costs one human glance; auto-shipping the bad one is the failure mode
    # this gate exists to prevent.
    "aws_default_security_group",

    # Every type below was considered for AUTO_SHIP_ELIGIBLE_TYPES and rejected on its own
    # reason, against that same asymmetric-downside test.

    # Internet-facing surfaces. A partner SFTP endpoint and a webhook receiver are, by design,
    # reachable from outside the account -- the network-layer equivalent of the public-exposure
    # content risk that put aws_s3_bucket_policy and aws_default_security_group here. This
    # classifier reads type and action only; it cannot tell a correctly-scoped endpoint from one
    # that exposes the wrong prefix.
    "aws_transfer_server",
    "aws_transfer_user",                   # grants an external party a credentialed path in
    "aws_transfer_ssh_key",                # the credential binding itself
    "aws_apigatewayv2_api",
    "aws_apigatewayv2_route",
    "aws_apigatewayv2_stage",
    "aws_apigatewayv2_integration",        # carries credentials_arn: the API's write path into SQS

    # Continuously-running, materially-priced compute. Creating one of these is not a
    # configuration change that can be glanced past on a cost report a month later; an EMR
    # cluster at the tier this module targets is the largest single line item this repo can
    # produce. auto_termination_policy bounds a FORGOTTEN cluster, not an unintended one.
    "aws_emr_cluster",
    "aws_emr_instance_fleet",
    "aws_dms_replication_instance",

    # Data MOVEMENT. Unlike the storage types in STATEFUL_RESOURCE_TYPES, these do not hold
    # data -- they copy it, continuously, somewhere else. A replication task pointed at the
    # wrong schema, a flow pulling the wrong SaaS object, or a CRR rule targeting the wrong
    # destination bucket is an exfiltration path that plans as an ordinary create.
    "aws_dms_endpoint",
    "aws_dms_s3_endpoint",
    "aws_dms_replication_task",
    "aws_appflow_flow",
    "aws_s3_bucket_replication_configuration",

    # Audit and retention integrity. A CloudTrail with S3 data events bills per event and is
    # the record SecOps reads; object lock makes objects undeletable for the full window, by
    # anyone, which is a commitment rather than a setting. Both are usually hardening -- and
    # both are worth one human glance precisely because they are hard to walk back.
    "aws_cloudtrail",
    "aws_s3_bucket_object_lock_configuration",
})

# Reviewed allowlist of resource types confirmed safe to auto-ship (docs/
# g5_autonomy_boundary_scope.md section 3) -- the inverted gate. A type absent from this set
# stages, tagged `unreviewed_resource_type` (or `reviewed_unsafe_resource_type` if it's in
# REVIEWED_UNSAFE_TYPES above). Each entry was reviewed individually against the catalog in
# ephemeral_apply.py's RESOURCE_TYPE_ALLOWLIST, not migrated wholesale from "not currently
# flagged".
#
# CONFIG-DEPENDENT ENTRIES. Some entries are safe as a TYPE but carry configuration that can
# flip that. Each has an explicit disposition, marked inline below; do not remove an entry
# without reading the matching case here:
#
#   (a) COVERED BY A G6 RULE, stays eligible -- `aws_redshiftserverless_workgroup`,
#       `aws_subnet`, `aws_s3_object`. Each has a schema-verified boolean/string attribute that
#       flips public exposure without changing resource type (`publicly_accessible`,
#       `map_public_ip_on_launch`, `acl`). SEC-08/SEC-09/SEC-10 (policy/g6/rules.rego) check
#       exactly those attributes. G5 sees only the type and stays silent; G6 catches the
#       CONTENT. Deleting one of those Rego rules re-opens the hole here.
#   (b) NO G6 RULE POSSIBLE, stays eligible on reasoned exception -- `aws_glue_job`,
#       `aws_kinesisanalyticsv2_application`, `aws_sfn_state_machine`. Each carries an
#       arbitrary executable payload (a Spark script, a Flink/SQL app, a state-machine
#       definition) whose risk is in what it DOES at runtime, not in any single attribute a
#       plan-time Rego rule could pattern-match. The actual privilege boundary for all three is
#       the IAM role each assumes: a genuinely new role is itself a separate `aws_iam_role`/
#       `aws_iam_policy` resource, independently caught by SEC-02/SEC-05 (wildcard resource/
#       action, missing external ID). This is a disclosed boundary, not an oversight -- moving
#       all three to REVIEWED_UNSAFE_TYPES would stage every future occurrence for a risk this
#       gate can see no better staged than un-staged.
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
    "aws_route_table",
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
    # A replication subnet group is a named list of existing subnet ids -- it holds no data,
    # grants no permission, has no endpoint, and costs nothing.
    "aws_dms_replication_subnet_group",
    # Not cloud resources -- test-utility types with zero cloud footprint, used by this repo's
    # test suite as create/delete/replace and end-to-end apply fixtures without real
    # credentials. Deliberately reviewed in, not a real-world type judgment. `grep -rn
    # 'resource "..."' tests/*.py` confirms these are the only non-cloud fixture types in the
    # suite, so the exemption is complete rather than partial; re-run that grep before adding
    # a third.
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
    """Classify a parsed `terraform show -json` plan. Fail-closed on any malformed input.

    Every field read here is validated rather than assumed. "Real terraform show -json always
    sets X" is the reasoning that produced the original fail-open mode-field bug, so do not
    reintroduce it. Only a genuinely empty, well-typed `resource_changes: []` (a real no-op
    plan) classifies as autonomous-eligible -- never a missing or malformed one.
    """
    # treat_absent_as_error=True is G5's own policy: an absent resource_changes is a fail-closed
    # BLOCK, not "nothing to check". Deliberately different from G6's shadow-mode reader, which
    # treats absent as "zero managed changes" -- see plan_reader's module docstring.
    raw_resource_changes, error = plan_reader.read_resource_changes(plan_json, treat_absent_as_error=True)
    if error:
        return _fail_closed(error)

    # Exclude mode == "data" only; do NOT invert this to require mode == "managed". A data
    # source's plan-time "read" is not a resource being changed (the same filter
    # coverage_audit.py applies). An allowlist on "managed" fails OPEN on a missing or
    # unrecognized `mode` field; a denylist on "data" excludes only what is certainly a
    # non-mutating read, so everything else stays in scope.
    resource_changes, malformed = plan_reader.managed_only(raw_resource_changes)
    findings = [{"address": None, "type": None, **m} for m in malformed]

    for rc in resource_changes:
        address = rc.get("address")
        rtype = rc.get("type")
        change = rc.get("change")

        # A missing or non-string type would look up safely (returning False) against the type
        # sets -- but treating "don't know what this is" as "therefore not stateful/IAM" is the
        # fail-open shape this gate exists to close. Malformed type data blocks outright.
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
            # Distinct from unreviewed_resource_type below: this type WAS reviewed and the
            # answer was "not safe to auto-ship" -- a materially different fact for an
            # audit-chain reader than "nobody has looked at this yet."
            findings.append({"address": address, "type": rtype, "reason": "reviewed_unsafe_resource_type"})
        elif rtype.startswith(_DATABRICKS_PREFIX):
            # Deliberately no finding, not an oversight. Databricks types were never reviewed
            # into AUTO_SHIP_ELIGIBLE_TYPES (that review was AWS-only, matching G9's scope), so
            # without this branch a databricks_* type absent from STATEFUL_RESOURCE_TYPES falls
            # through to unreviewed_resource_type -- redundant, because `reduced_assurance`
            # below already makes EVERY databricks_* type unconditionally ineligible. Skip
            # rather than double-flag. Databricks type review is separate, still-open work.
            pass
        elif rtype not in AUTO_SHIP_ELIGIBLE_TYPES:
            # A type that is neither known-dangerous NOR reviewed-safe stages -- whether it is
            # genuinely new (generation-time authoring's whole point) or simply overlooked.
            # "We don't recognize it" must never be read as "therefore fine."
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
