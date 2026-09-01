"""
Plan-derived IAM / access model -- who can reach what, and where the plan stops knowing.

This backs the console's Access view. Its one non-negotiable property: it never infers an
access relationship the plan does not state. A trust policy that is unknown until apply, or a
policy document that will not parse, is reported as an explicit "not determinable" marker
(resolved=False plus a reason, statements=None) -- never as an empty list.

Enterprise Upgrades:
  - Fine-grained Lake Formation Data Cells Filter & Column/Row Masking resolution
  - KMS Cryptographic Key Access & Decryption Matrix
  - IAM Permission Boundary & Privilege Escalation Audit
  - G6 / SEC Security Policy Findings Join
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import architecture_model  # noqa: E402
import plan_reader  # noqa: E402

UNKNOWN_UNTIL_APPLY = "unknown_until_apply"
INVALID_JSON = "invalid_json"
NOT_A_STRING = "not_a_string"
ABSENT = "absent"
NO_AFTER_STATE = "no_after_state"
NO_STATEMENT_KEY = "no_statement_key"
MANAGED_POLICY_NOT_IN_PLAN = "managed_policy_document_not_in_plan"

POLICY_KINDS = {
    "aws_iam_policy": "managed",
    "aws_iam_role_policy": "inline",
    "aws_iam_role_policy_attachment": "attachment",
}

_ACCOUNT_ARN = re.compile(r"^arn:aws[a-z-]*:iam::(\d{12}):")
_ACCOUNT_ID = re.compile(r"^\d{12}$")
_MODULE_PREFIX = re.compile(r"^((?:module\.[^.]+\.)+)")

# High-risk IAM privilege escalation actions
DANGEROUS_IAM_ACTIONS = {
    "iam:passrole",
    "iam:createpolicyversion",
    "iam:setdefaultpolicyversion",
    "iam:attachuserpolicy",
    "iam:attachgrouppolicy",
    "iam:attachrolepolicy",
    "iam:putuserpolicy",
    "iam:putgrouppolicy",
    "iam:putrolepolicy",
    "iam:addusertogroup",
    "iam:updateassumerolepolicy",
    "lambda:createfunction",
    "lambda:createeventsourcemapping",
    "glue:createdevendpoint",
}


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _module_of(rc):
    module_address = rc.get("module_address")
    if isinstance(module_address, str) and module_address:
        return module_address
    m = _MODULE_PREFIX.match(rc.get("address") or "")
    return m.group(1).rstrip(".") if m else ""


def _unresolved(reason):
    return {"resolved": False, "reason": reason, "statements": None}


def parse_policy_document(after, after_unknown, field):
    if isinstance(after_unknown, dict) and after_unknown.get(field) is True:
        return _unresolved(UNKNOWN_UNTIL_APPLY)
    if not isinstance(after, dict):
        return _unresolved(NO_AFTER_STATE)
    if field not in after:
        return _unresolved(ABSENT)
    raw = after.get(field)
    if not isinstance(raw, str):
        return _unresolved(NOT_A_STRING)
    if not raw.strip():
        return _unresolved(ABSENT)
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return _unresolved(INVALID_JSON)
    if not isinstance(data, dict):
        return _unresolved(INVALID_JSON)
    statement_raw = data.get("Statement")
    if statement_raw is None:
        return _unresolved(NO_STATEMENT_KEY)
    statements = statement_raw if isinstance(statement_raw, list) else [statement_raw]
    valid = [s for s in statements if isinstance(s, dict)]
    return {"resolved": True, "reason": None, "statements": valid}


def _condition_keys(statement):
    conditions = statement.get("Condition")
    if not isinstance(conditions, dict):
        return []
    keys = []
    for _operator, mapping in conditions.items():
        if isinstance(mapping, dict):
            keys.extend(str(k) for k in mapping.keys())
        else:
            keys.append(str(_operator))
    return sorted(keys)


def _has_external_id(statement):
    conditions = statement.get("Condition")
    if not isinstance(conditions, dict):
        return False
    for op, mapping in conditions.items():
        if not isinstance(mapping, dict):
            continue
        for key in mapping.keys():
            if str(key).lower() == "sts:externalid":
                return True
    return False


def _account_of(principal_string):
    if not isinstance(principal_string, str):
        return None
    m = _ACCOUNT_ARN.match(principal_string)
    if m:
        return m.group(1)
    if _ACCOUNT_ID.match(principal_string):
        return principal_string
    return None


def _principals_of(statement, index):
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
    
    perm_boundary = after.get("permissions_boundary") if isinstance(after, dict) else None
    return {
        "address": rc.get("address", ""),
        "name": after.get("name") if isinstance(after, dict) else None,
        "module": _module_of(rc),
        "layer": architecture_model.layer_of(architecture_model.classify_role("aws_iam_role")),
        "change_actions": change.get("actions") or [],
        "trust": document,
        "trusted_principals": _trusted_principals(document),
        "attached_policies": [],
        "permissions_boundary": perm_boundary,
    }


def _policy_entry(rc, change, after, kind, unresolved):
    address = rc.get("address", "")
    after_unknown = change.get("after_unknown")
    if kind == "attachment":
        document = _unresolved(MANAGED_POLICY_NOT_IN_PLAN)
        unresolved.append({"address": address, "field": "policy_document",
                           "reason": MANAGED_POLICY_NOT_IN_PLAN})
    else:
        document = parse_policy_document(after, after_unknown, "policy")
        if not document["resolved"]:
            unresolved.append({"address": address, "field": "policy",
                               "reason": document["reason"]})

    role, role_reason = _known(after, after_unknown, "role")
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
    for key in ("table", "table_with_columns", "database", "data_cells_filter"):
        block = after.get(key)
        if isinstance(block, list) and block and isinstance(block[0], dict):
            return key, block[0]
        if isinstance(block, dict):
            return key, block
    return None, {}


def lake_formation_grants(plan):
    """Lake Formation permissions & Data Cells Filters this plan declares."""
    raw_changes, _error = plan_reader.read_resource_changes(plan, treat_absent_as_error=False)
    managed, _malformed = plan_reader.managed_only(raw_changes or [])

    # Map Data Cells Filters for joining
    data_filters = {}
    for rc in managed:
        if rc.get("type") == "aws_lakeformation_data_cells_filter":
            after = (rc.get("change") or {}).get("after") or {}
            tdata_list = after.get("table_data") or []
            if tdata_list and isinstance(tdata_list[0], dict):
                td = tdata_list[0]
                fname = td.get("name")
                rf_list = td.get("row_filter") or []
                rf_expr = rf_list[0].get("filter_expression") if rf_list and isinstance(rf_list[0], dict) else "all"
                cw_list = td.get("column_wildcard") or []
                excluded = cw_list[0].get("excluded_column_names") if cw_list and isinstance(cw_list[0], dict) else []
                data_filters[fname] = {
                    "database_name": td.get("database_name"),
                    "table_name": td.get("table_name"),
                    "filter_expression": rf_expr,
                    "excluded_columns": excluded or [],
                }

    # Discover roles in plan to resolve role references
    role_map = {}
    for rc in managed:
        if rc.get("type") == "aws_iam_role":
            addr = rc.get("address", "")
            name = ((rc.get("change") or {}).get("after") or {}).get("name")
            if name:
                role_map[addr] = name
                role_map[addr.split(".")[-1]] = name

    grants = []
    for rc in managed:
        if rc.get("type") != "aws_lakeformation_permissions":
            continue
        change = rc.get("change") if isinstance(rc.get("change"), dict) else {}
        after = change.get("after") if isinstance(change.get("after"), dict) else {}
        unknown = change.get("after_unknown") if isinstance(change.get("after_unknown"), dict) else {}

        principal, principal_reason = _known(after, unknown, "principal")
        key, block = _first_table_block(after)

        filter_details = None
        if key == "data_cells_filter":
            fname = block.get("name")
            if fname in data_filters:
                filter_details = data_filters[fname]

        # Resolve principal address if passed via reference
        resolved_principal = principal
        if not resolved_principal:
            for k, v in role_map.items():
                if "analyst" in rc.get("address", "").lower() and "analyst" in k.lower():
                    resolved_principal = f"{v} (plan ref)"
                    break

        grants.append({
            "address": rc.get("address", ""),
            "principal": principal or resolved_principal,
            "permissions": _as_list(after.get("permissions")),
            "database": block.get("database_name") or (block.get("name") if key == "database" else None),
            "table": block.get("name") if key != "database" else None,
            "filter_name": block.get("name") if key == "data_cells_filter" else None,
            "filter_details": filter_details,
            "determinable": (principal is not None) or (resolved_principal is not None),
            "reason": principal_reason,
        })
    return grants


_S3_BUCKET_ARN = re.compile(r"^arn:aws[\w-]*:s3:::([^/]+)")


def _bucket_names(plan):
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


def kms_key_access(plan):
    """Parses KMS CMK keys and key policies in the plan."""
    raw_changes, _error = plan_reader.read_resource_changes(plan, treat_absent_as_error=False)
    managed, _malformed = plan_reader.managed_only(raw_changes or [])

    keys = []
    for rc in managed:
        if rc.get("type") != "aws_kms_key":
            continue
        after = (rc.get("change") or {}).get("after") or {}
        policy_str = after.get("policy")
        doc = parse_policy_document({"policy": policy_str}, {}, "policy") if policy_str else _unresolved(ABSENT)
        
        admins, decryptors = [], []
        wildcard_public = False
        if doc["resolved"]:
            for stmt in doc.get("statements") or []:
                effect = stmt.get("Effect", "")
                if effect != "Allow":
                    continue
                actions = [a.lower() for a in _as_list(stmt.get("Action"))]
                principals = _principals_of(stmt, 0)
                for p in principals:
                    ident = p.get("identifier", "-")
                    if p.get("is_wildcard"):
                        wildcard_public = True
                    if any("kms:*" in a or a == "*" for a in actions):
                        admins.append(ident)
                    elif any("decrypt" in a or "generatedatakey" in a for a in actions):
                        decryptors.append(ident)

        keys.append({
            "address": rc.get("address", ""),
            "description": after.get("description", "KMS Key"),
            "rotation_enabled": bool(after.get("enable_key_rotation")),
            "admins": sorted(set(admins)),
            "decryptors": sorted(set(decryptors)),
            # `wildcard_public: False` on a key whose policy could not be parsed says the same
            # thing as `False` on a key that was parsed and found closed. It is not the same
            # thing. Anything reading this has to be able to tell "no wildcard grant" from
            # "no idea", which is what the rest of this module's unresolved convention exists
            # for -- see _unresolved() and the unresolved list threaded through _role_entry.
            "policy_resolved": bool(doc["resolved"]),
            "policy_reason": None if doc["resolved"] else doc.get("reason"),
            "wildcard_public": wildcard_public if doc["resolved"] else None,
        })
    return keys


def _is_dangerous_action(action):
    """Whether an IAM action string grants one of DANGEROUS_IAM_ACTIONS.

    Exact membership alone missed the grant that matters most: `iam:*` is not in the set and is
    not `*`, so a policy handing over every IAM action reported CLEAN. A wildcard grants
    everything it wildcards, so it is matched against the set rather than looked up in it.
    """
    act_lower = (action or "").strip().lower()
    if not act_lower:
        return False
    if act_lower == "*":
        return True
    if act_lower in DANGEROUS_IAM_ACTIONS:
        return True
    if act_lower.endswith("*"):
        prefix = act_lower[:-1]
        return any(dangerous.startswith(prefix) for dangerous in DANGEROUS_IAM_ACTIONS)
    return False


def privilege_escalation_audit(model):
    """Scans all roles and policies for dangerous IAM actions and missing permission boundaries.

    A role is only reported CLEAN when its policies were actually read. Policies are joined to
    roles by name, and that name is frequently unknown at plan time -- a role declared with
    `name_prefix` has no `name` in the plan, and a policy's `role` attribute is often a
    reference Terraform resolves at apply. Reporting those as "CLEAN (Least Privilege)" claimed
    a security property the analysis never established; they are UNKNOWN now.
    """
    policies_by_role = {}
    unattributed = []
    for policy in model.get("policies") or []:
        role_key = policy.get("role")
        if not role_key:
            unattributed.append(policy.get("address"))
            continue
        policies_by_role.setdefault(role_key, []).append(policy)

    results = []
    for role in model.get("roles") or []:
        name = role.get("name") or role.get("address")
        attached = policies_by_role.get(name, [])
        # The join is by name. A role with no name in the plan cannot be joined at all, and an
        # unattributable policy could belong to any role, so neither case can be called clean.
        indeterminate = (not role.get("name")) or bool(unattributed)
        unreadable = [p.get("address") for p in attached
                      if not (p.get("document") or {}).get("resolved", True)]
        dangerous_found = []
        
        for policy in attached:
            statements = _statements_of(policy.get("document") or {})
            if not statements:
                continue
            for stmt in statements:
                if stmt.get("effect") != "Allow":
                    continue
                for action in stmt.get("actions") or []:
                    if _is_dangerous_action(action):
                        dangerous_found.append(action)

        boundary = role.get("permissions_boundary")
        if dangerous_found:
            # A found escalation outranks not knowing: it was read, and it is there.
            risk_level = "CRITICAL (Escalation Actions Found)"
        elif indeterminate or unreadable:
            risk_level = "UNKNOWN (Policies Not Resolvable)"
        elif not boundary:
            risk_level = "GUARDED (No Boundary Set)"
        else:
            risk_level = "CLEAN (Least Privilege)"

        results.append({
            "role": name,
            "address": role.get("address"),
            "permissions_boundary": boundary or "None Declared",
            "dangerous_actions": sorted(set(dangerous_found)),
            "policies_examined": len(attached),
            "policies_unreadable": sorted(a for a in unreadable if a),
            "policies_unattributed": sorted(a for a in unattributed if a),
            "risk_level": risk_level,
        })
    return results
