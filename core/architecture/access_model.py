"""
Plan-derived IAM / access model -- who can reach what, and where the plan stops knowing.

This backs the console's Access view. Its one non-negotiable property: it never infers an
access relationship the plan does not state. A trust policy that is unknown until apply, or a
policy document that will not parse, is reported as an explicit "not determinable" marker
(resolved=False plus a reason, statements=None) -- never as an empty list. An access screen
that quietly under-reports permissions is worse than no screen, and "[]" versus "unreadable"
is exactly the distinction that gets lost when a parser fails soft into a default.

Design rules (deliberate):
  * Facts only. Actions, resources and principals come from the policy JSON the plan carries.
    Cross-account is decided by the account id written in the principal, not by inference.
  * Same-account-vs-cross-account is NOT resolved by calling STS. policy/g6/rules.rego already
    established (verified live) that data.aws_caller_identity is a real API call that fails
    under dummy credentials, so this module takes the plan's own account id only when a caller
    passes one in or the plan carries a resolved aws_caller_identity read. When it stays
    unknown, an account principal is reported with external_to_plan_account=None -- undecided,
    never assumed same-account.
  * Findings are NOT recomputed here. Every entry is keyed by its Terraform `address`, which is
    what policy/g6/rules.rego emits as a finding's `resource` field, so SEC-01..SEC-07 findings
    join straight onto the role or policy they concern.
  * Fail-soft, but counted: a malformed entry lands in `malformed` and never crashes the read.

Depends on: core/governance/plan_reader.py (fail-soft plan access),
    core/architecture/architecture_model.py (shared role/layer classification)
Shells out to: nothing. Everything is derived from a plan.json already on disk -- no AWS call.
Used by: app/console_app.py (Access view), tests/test_access_model.py
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# architecture_model's own import block puts core/governance on sys.path, which is what makes
# plan_reader importable when this file is run directly -- so the order here matters.
import architecture_model  # noqa: E402
import plan_reader  # noqa: E402

# Reasons a policy document could not be turned into statements. These are the caller's
# vocabulary for "not determinable from this plan"; each one is a different fact and they are
# deliberately not collapsed into a single boolean.
UNKNOWN_UNTIL_APPLY = "unknown_until_apply"
INVALID_JSON = "invalid_json"
NOT_A_STRING = "not_a_string"
ABSENT = "absent"
NO_AFTER_STATE = "no_after_state"
NO_STATEMENT_KEY = "no_statement_key"
# An aws_iam_role_policy_attachment names a policy by ARN. When that ARN is an AWS-managed
# policy (or any policy this plan does not itself create), its statements are simply not in
# the document -- reporting zero grants there would hide, say, AmazonS3FullAccess entirely.
MANAGED_POLICY_NOT_IN_PLAN = "managed_policy_document_not_in_plan"

# Inline vs managed vs attachment is a real access distinction, not cosmetic: an inline policy
# dies with its role, a managed policy outlives it, and an attachment points at a document that
# may live outside the plan.
POLICY_KINDS = {
    "aws_iam_policy": "managed",
    "aws_iam_role_policy": "inline",
    "aws_iam_role_policy_attachment": "attachment",
}

_ACCOUNT_ARN = re.compile(r"^arn:aws[a-z-]*:iam::(\d{12}):")
_ACCOUNT_ID = re.compile(r"^\d{12}$")
_MODULE_PREFIX = re.compile(r"^((?:module\.[^.]+\.)+)")


def _as_list(value):
    """AWS policy JSON writes a single-element field as a bare scalar, not a list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _module_of(rc):
    """Owning module: Terraform's own module_address when present, else the address prefix."""
    module_address = rc.get("module_address")
    if isinstance(module_address, str) and module_address:
        return module_address
    m = _MODULE_PREFIX.match(rc.get("address") or "")
    return m.group(1).rstrip(".") if m else ""


def _unresolved(reason):
    return {"resolved": False, "reason": reason, "statements": None}


def parse_policy_document(after, after_unknown, field):
    """Read one raw-JSON policy attribute into {resolved, reason, statements}.

    `statements` is a real list only when resolved is True and None otherwise, so a caller
    physically cannot render an unreadable document as "grants nothing". after_unknown is
    sparse -- a key is present only when the value is genuinely unknown -- so it is consulted
    first and never read as "== False means known".
    """
    if isinstance(after_unknown, dict) and after_unknown.get(field) is True:
        return _unresolved(UNKNOWN_UNTIL_APPLY)
    if not isinstance(after, dict):
        return _unresolved(NO_AFTER_STATE)
    raw = after.get(field)
    if raw is None:
        return _unresolved(ABSENT)
    if isinstance(raw, dict):
        parsed = raw  # already-decoded document (some providers hand one back as an object)
    elif isinstance(raw, str):
        if not raw.strip():
            return _unresolved(ABSENT)
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return _unresolved(INVALID_JSON)
    else:
        return _unresolved(NOT_A_STRING)
    if not isinstance(parsed, dict) or "Statement" not in parsed:
        return _unresolved(NO_STATEMENT_KEY)
    statements = [s for s in _as_list(parsed.get("Statement")) if isinstance(s, dict)]
    return {"resolved": True, "reason": None, "statements": statements}


def _condition_keys(statement):
    """Every condition key in a statement, e.g. ["sts:ExternalId"]."""
    condition = statement.get("Condition")
    if not isinstance(condition, dict):
        return []
    keys = []
    for operators in condition.values():
        if isinstance(operators, dict):
            keys.extend(str(k) for k in operators)
    return keys


def _has_external_id(statement):
    return any(k.lower() == "sts:externalid" for k in _condition_keys(statement))


def _account_of(identifier):
    """The AWS account id a principal names, or None. A service principal never matches."""
    if not isinstance(identifier, str):
        return None
    m = _ACCOUNT_ARN.match(identifier)
    if m:
        return m.group(1)
    return identifier if _ACCOUNT_ID.match(identifier) else None


def _principals_of(statement, index):
    """Flatten a statement's Principal block into one entry per identifier.

    Effect is carried through rather than filtered out here: a Deny in a trust policy is a real
    fact about who cannot assume the role, and dropping it would hide it from the view.
    """
    principal = statement.get("Principal")
    if principal is None and "NotPrincipal" in statement:
        principal = statement.get("NotPrincipal")
    pairs = []
    if isinstance(principal, str):
        pairs.append(("*" if principal == "*" else "AWS", principal))
    elif isinstance(principal, dict):
        for ptype, identifiers in principal.items():
            for identifier in _as_list(identifiers):
                pairs.append((str(ptype), identifier))
    return [{
        "statement_index": index,
        "effect": statement.get("Effect", ""),
        "type": ptype,
        "identifier": identifier,
        "account_id": _account_of(identifier),
        "is_wildcard": identifier == "*",
        "has_external_id": _has_external_id(statement),
    } for ptype, identifier in pairs]


def _trusted_principals(document):
    if not document["resolved"]:
        return None
    out = []
    for index, statement in enumerate(document["statements"]):
        out.extend(_principals_of(statement, index))
    return out


def _statement(statement, index):
    """One policy statement, flattened. NotAction/NotResource are kept in their own fields and
    flagged `negated` -- a NotAction statement allows the whole action namespace EXCEPT what it
    lists, so reporting it as `actions: []` would understate the grant enormously."""
    not_actions = [str(a) for a in _as_list(statement.get("NotAction"))]
    not_resources = [str(r) for r in _as_list(statement.get("NotResource"))]
    return {
        "index": index,
        "sid": statement.get("Sid", ""),
        "effect": statement.get("Effect", ""),
        "actions": [str(a) for a in _as_list(statement.get("Action"))],
        "not_actions": not_actions,
        "resources": [str(r) for r in _as_list(statement.get("Resource"))],
        "not_resources": not_resources,
        "negated": bool(not_actions or not_resources),
        "conditions": _condition_keys(statement),
    }


def _statements_of(document):
    if not document["resolved"]:
        return None
    return [_statement(s, i) for i, s in enumerate(document["statements"])]


def _known(after, after_unknown, field):
    """(value, reason) for a scalar attribute -- reason is set only when it is not knowable."""
    if isinstance(after_unknown, dict) and after_unknown.get(field) is True:
        return None, UNKNOWN_UNTIL_APPLY
    if not isinstance(after, dict):
        return None, NO_AFTER_STATE
    value = after.get(field)
    return (value, None) if value is not None else (None, ABSENT)


def _role_entry(rc, change, after, unresolved):
    document = parse_policy_document(after, change.get("after_unknown"), "assume_role_policy")
    if not document["resolved"]:
        unresolved.append({"address": rc.get("address", ""),
                           "field": "assume_role_policy", "reason": document["reason"]})
    return {
        "address": rc.get("address", ""),
        "name": after.get("name") if isinstance(after, dict) else None,
        "module": _module_of(rc),
        "layer": architecture_model.layer_of(architecture_model.classify_role("aws_iam_role")),
        "change_actions": change.get("actions") or [],
        "trust": document,
        "trusted_principals": _trusted_principals(document),
        "attached_policies": [],
    }


def _policy_entry(rc, change, after, kind, unresolved):
    address = rc.get("address", "")
    after_unknown = change.get("after_unknown")
    if kind == "attachment":
        # No document of its own: the statements live behind policy_arn, outside this plan.
        document = _unresolved(MANAGED_POLICY_NOT_IN_PLAN)
        unresolved.append({"address": address, "field": "policy_document",
                           "reason": MANAGED_POLICY_NOT_IN_PLAN})
    else:
        document = parse_policy_document(after, after_unknown, "policy")
        if not document["resolved"]:
            unresolved.append({"address": address, "field": "policy",
                               "reason": document["reason"]})

    role, role_reason = _known(after, after_unknown, "role")
    # A standalone aws_iam_policy is attached to nothing by itself, so its missing `role` is
    # expected rather than a gap; only report an attachment whose role we genuinely cannot read.
    if role_reason == UNKNOWN_UNTIL_APPLY:
        unresolved.append({"address": address, "field": "role", "reason": role_reason})

    statements = _statements_of(document)
    return {
        "address": address,
        "name": after.get("name") if isinstance(after, dict) else None,
        "module": _module_of(rc),
        "type": rc.get("type", ""),
        "attachment_kind": kind,
        "role": role,
        "policy_arn": (after or {}).get("policy_arn") if isinstance(after, dict) else None,
        "change_actions": change.get("actions") or [],
        "document": document,
        "statements": statements,
        # A Deny is not a grant. Both views are exposed so a caller can show the whole policy
        # without ever mistaking a prohibition for a permission.
        "grants": None if statements is None else [s for s in statements if s["effect"] == "Allow"],
    }


def access_model(plan, own_account_id=None):
    """Derive the full access model from a `terraform show -json` plan."""
    raw_changes, _error = plan_reader.read_resource_changes(plan, treat_absent_as_error=False)
    managed, malformed = plan_reader.managed_only(raw_changes or [])

    roles = []
    policies = []
    unresolved = []
    for rc in managed:
        rtype = rc.get("type")
        change = rc.get("change") if isinstance(rc.get("change"), dict) else {}
        after = change.get("after")
        if rtype == "aws_iam_role":
            roles.append(_role_entry(rc, change, after, unresolved))
        elif rtype in POLICY_KINDS:
            policies.append(_policy_entry(rc, change, after, POLICY_KINDS[rtype], unresolved))

    roles.sort(key=lambda r: r["address"])
    policies.sort(key=lambda p: p["address"])

    # Attachment by the role NAME the policy actually names. A policy whose role is unknown
    # until apply is left unattached rather than matched to the only role in the plan.
    for role in roles:
        if role["name"]:
            role["attached_policies"] = [p["address"] for p in policies
                                         if p["role"] == role["name"]]

    return {
        "own_account_id": own_account_id,
        "roles": roles,
        "policies": policies,
        "unresolved": unresolved,
        "malformed": malformed,
    }

def cross_account_grants(model):
    """Every trust this plan extends outside its own account, plus the ones it cannot read.

    Three things are deliberately in scope that an account-id filter alone would drop:

    - A WILDCARD principal names no account, and is the single most dangerous trust policy
      there is. Filtering on account id would silently omit it.
    - An UNRESOLVED trust policy is reported with determinable=False. A policy computed at
      apply time may well trust another account; treating it as "no trust" is the same
      under-report this whole module exists to avoid.
    - Same-account trust is excluded, because flagging it teaches a reviewer to ignore the
      column, and a column people ignore protects nobody.
    """
    own = str(model.get("own_account_id") or "")
    grants = []
    for role in model.get("roles") or []:
        principals = role.get("trusted_principals")
        if principals is None:
            grants.append({
                "role": role.get("name") or role.get("address"),
                "address": role.get("address"), "account_id": None, "principal": None,
                "is_wildcard": False, "has_external_id": False, "effect": None,
                "determinable": False,
                "reason": (role.get("trust") or {}).get("reason")
                          or "not determinable from this plan",
            })
            continue
        for principal in principals:
            account = principal.get("account_id")
            if not principal.get("is_wildcard") and (not account or account == own):
                continue
            grants.append({
                "role": role.get("name") or role.get("address"),
                "address": role.get("address"),
                "account_id": account,
                "principal": principal.get("identifier"),
                "is_wildcard": bool(principal.get("is_wildcard")),
                "has_external_id": bool(principal.get("has_external_id")),
                "effect": principal.get("effect"),
                "determinable": True, "reason": None,
            })
    return grants


def _first_table_block(after):
    """`table` and `table_with_columns` arrive as a one-element list in plan JSON."""
    for key in ("table", "table_with_columns", "database"):
        block = after.get(key)
        if isinstance(block, list) and block and isinstance(block[0], dict):
            return key, block[0]
        if isinstance(block, dict):
            return key, block
    return None, {}


def lake_formation_grants(plan):
    """Lake Formation permissions this plan declares.

    A grant whose principal is not known until apply is returned with principal=None and
    determinable=False rather than omitted: the grant is real, and a governance view that
    silently drops it reports less access than exists.
    """
    raw_changes, _error = plan_reader.read_resource_changes(plan, treat_absent_as_error=False)
    managed, _malformed = plan_reader.managed_only(raw_changes or [])

    grants = []
    for rc in managed:
        if rc.get("type") != "aws_lakeformation_permissions":
            continue
        change = rc.get("change") if isinstance(rc.get("change"), dict) else {}
        after = change.get("after") if isinstance(change.get("after"), dict) else {}
        unknown = change.get("after_unknown") if isinstance(
            change.get("after_unknown"), dict) else {}

        principal, principal_reason = _known(after, unknown, "principal")
        key, block = _first_table_block(after)
        grants.append({
            "address": rc.get("address", ""),
            "principal": principal,
            "permissions": _as_list(after.get("permissions")),
            "database": block.get("database_name") or (
                block.get("name") if key == "database" else None),
            "table": block.get("name") if key != "database" else None,
            "determinable": principal is not None,
            "reason": principal_reason,
        })
    return grants

# An S3 ARN names a bucket between the fixed prefix and the first slash:
#   arn:aws:s3:::acme-bronze/raw/*  ->  acme-bronze
_S3_BUCKET_ARN = re.compile(r"^arn:aws[\w-]*:s3:::([^/]+)")


def _bucket_names(plan):
    """{bucket name: address} for every S3 bucket this plan names.

    A bucket whose name is computed at apply time has no key here, which is why an ARN that
    matches nothing is REPORTED rather than dropped -- the grant is real, only the join is
    impossible.
    """
    raw_changes, _error = plan_reader.read_resource_changes(plan, treat_absent_as_error=False)
    managed, _malformed = plan_reader.managed_only(raw_changes or [])
    names = {}
    for rc in managed:
        if rc.get("type") != "aws_s3_bucket":
            continue
        change = rc.get("change") if isinstance(rc.get("change"), dict) else {}
        after = change.get("after") if isinstance(change.get("after"), dict) else {}
        name = after.get("bucket")
        if name:
            names[str(name)] = rc.get("address", "")
    return names


def dataset_reachability(model, plan):
    """Which datasets each role can reach, from its policies' own resource ARNs.

    Four outcomes are kept distinct, because collapsing any of them under-reports access:

    - A DENY is not reach. It is a real fact about permissions and it is not a grant.
    - A WILDCARD or NEGATED statement reaches everything. `Resource: "*"` matches no bucket
      name, and `NotResource` allows the whole namespace EXCEPT what it lists, so reading
      either one's `resources` as the grant would report the broadest possible access as
      reaching nothing.
    - An UNRESOLVED policy makes reach undeterminable, never empty.
    - An ARN naming a bucket this plan does not name is reported as unmatched. The grant
      exists; only the join failed.
    """
    buckets = _bucket_names(plan)
    policies_by_role = {}
    for policy in model.get("policies") or []:
        policies_by_role.setdefault(policy.get("role"), []).append(policy)

    entries = []
    for role in model.get("roles") or []:
        name = role.get("name")
        attached = policies_by_role.get(name, [])
        reached, unmatched = {}, []
        determinable, everything = True, False

        for policy in attached:
            statements = _statements_of(policy.get("document") or {})
            if statements is None:
                determinable = False
                continue
            for statement in statements:
                if str(statement.get("effect", "")).lower() != "allow":
                    continue
                if statement.get("negated"):
                    everything = True
                    continue
                for resource in statement.get("resources") or []:
                    if resource == "*":
                        everything = True
                        continue
                    match = _S3_BUCKET_ARN.match(resource)
                    bucket = match.group(1) if match else None
                    if bucket == "*":
                        everything = True
                        continue
                    address = buckets.get(bucket) if bucket else None
                    if not address:
                        unmatched.append(resource)
                        continue
                    reached.setdefault(address, set()).update(statement.get("actions") or [])

        entries.append({
            "role": name or role.get("address"),
            "address": role.get("address"),
            "determinable": determinable,
            "reaches_everything": everything,
            "datasets": [{"address": address, "actions": sorted(actions)}
                         for address, actions in sorted(reached.items())],
            "unmatched_resources": sorted(set(unmatched)),
        })
    return entries

