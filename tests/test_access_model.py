"""
Tests for the plan-derived access model that backs the console's Access view.

The property under test throughout is that nothing is invented: a role, a grant or a
cross-account trust appears only when the plan states it, and an unreadable policy
document is reported as an explicit "not determinable" marker that a caller cannot
mistake for "no permissions".
"""
import json

import access_model as am


# --- fixture builders (real `terraform show -json` shapes) ------------------
def _rc(rtype, address, after=None, after_unknown=None, module_address=None,
        actions=None, mode="managed", name=None):
    change = {"actions": actions or ["create"], "after": after,
              "after_unknown": after_unknown or {}}
    rc = {"address": address, "mode": mode, "type": rtype,
          "name": name if name is not None else address.split(".")[-1],
          "change": change}
    if module_address is not None:
        rc["module_address"] = module_address
    return rc


def _plan(resource_changes=None, prior_state_resources=None):
    plan = {"format_version": "1.2"}
    if resource_changes is not None:
        plan["resource_changes"] = resource_changes
    if prior_state_resources is not None:
        plan["prior_state"] = {"values": {"root_module": {"resources": prior_state_resources}}}
    return plan


def _trust(*statements):
    """A jsonencode()'d trust policy resolves to a plain JSON string in after."""
    return json.dumps({"Version": "2012-10-17", "Statement": list(statements)})


SERVICE_TRUST = _trust({"Effect": "Allow", "Principal": {"Service": "glue.amazonaws.com"},
                        "Action": "sts:AssumeRole"})


# --- roles -----------------------------------------------------------------
def test_role_carries_name_module_and_parsed_service_principal():
    plan = _plan([_rc("aws_iam_role", "module.glue.aws_iam_role.this",
                      after={"name": "glue-etl-role", "assume_role_policy": SERVICE_TRUST},
                      module_address="module.glue")])
    roles = am.access_model(plan)["roles"]
    assert len(roles) == 1
    role = roles[0]
    assert role["address"] == "module.glue.aws_iam_role.this"
    assert role["name"] == "glue-etl-role"
    assert role["module"] == "module.glue"
    assert role["trust"]["resolved"] is True
    assert role["trusted_principals"] == [{
        "statement_index": 0, "effect": "Allow", "type": "Service",
        "identifier": "glue.amazonaws.com", "account_id": None,
        "is_wildcard": False, "has_external_id": False,
    }]


def test_module_is_derived_from_the_address_when_module_address_is_absent():
    plan = _plan([_rc("aws_iam_role", "module.a.module.b.aws_iam_role.this",
                      after={"name": "r", "assume_role_policy": SERVICE_TRUST})])
    assert am.access_model(plan)["roles"][0]["module"] == "module.a.module.b"


def test_root_module_role_has_empty_module():
    plan = _plan([_rc("aws_iam_role", "aws_iam_role.this",
                      after={"name": "r", "assume_role_policy": SERVICE_TRUST})])
    assert am.access_model(plan)["roles"][0]["module"] == ""


# --- the central invariant: unresolved is not empty ------------------------
def test_unknown_assume_role_policy_is_not_determinable_never_an_empty_list():
    """The most important property in this module: an access screen must never render
    'trusts nobody' for a trust policy that is simply not known until apply."""
    plan = _plan([_rc("aws_iam_role", "aws_iam_role.computed",
                      after={"name": "r"}, after_unknown={"assume_role_policy": True})])
    model = am.access_model(plan)
    role = model["roles"][0]
    assert role["trust"]["resolved"] is False
    assert role["trust"]["reason"] == am.UNKNOWN_UNTIL_APPLY
    assert role["trust"]["statements"] is None
    # Not [] -- an empty list would read as "trusted by nobody", which is a different fact.
    assert role["trusted_principals"] is None
    assert {"address": "aws_iam_role.computed", "field": "assume_role_policy",
            "reason": am.UNKNOWN_UNTIL_APPLY} in model["unresolved"]


def test_absent_assume_role_policy_is_distinguishable_from_unknown():
    plan = _plan([_rc("aws_iam_role", "aws_iam_role.bare", after={"name": "r"})])
    role = am.access_model(plan)["roles"][0]
    assert role["trust"]["resolved"] is False
    assert role["trust"]["reason"] == am.ABSENT
    assert role["trusted_principals"] is None


def test_unparseable_trust_policy_is_reported_as_invalid_json_not_dropped():
    plan = _plan([_rc("aws_iam_role", "aws_iam_role.broken",
                      after={"name": "r", "assume_role_policy": "{not json"})])
    model = am.access_model(plan)
    assert model["roles"][0]["trust"]["reason"] == am.INVALID_JSON
    assert model["roles"][0]["trusted_principals"] is None
    assert model["unresolved"][0]["reason"] == am.INVALID_JSON


def test_a_resolved_trust_policy_with_no_statements_yields_an_empty_principal_list():
    """The counterpart to the test above: genuinely-empty and unreadable must not collapse."""
    plan = _plan([_rc("aws_iam_role", "aws_iam_role.empty",
                      after={"name": "r", "assume_role_policy": _trust()})])
    role = am.access_model(plan)["roles"][0]
    assert role["trust"]["resolved"] is True
    assert role["trusted_principals"] == []


# --- policies and grants ---------------------------------------------------
def _doc(*statements):
    return json.dumps({"Version": "2012-10-17", "Statement": list(statements)})


READ_BRONZE = {"Sid": "ReadBronze", "Effect": "Allow",
               "Action": ["s3:GetObject", "s3:ListBucket"],
               "Resource": ["arn:aws:s3:::lake-bronze", "arn:aws:s3:::lake-bronze/*"]}


def test_inline_managed_and_attachment_policies_are_distinguished():
    plan = _plan([
        _rc("aws_iam_policy", "aws_iam_policy.standalone",
            after={"name": "standalone", "policy": _doc(READ_BRONZE)}),
        _rc("aws_iam_role_policy", "aws_iam_role_policy.inline",
            after={"name": "inline", "role": "glue-etl-role", "policy": _doc(READ_BRONZE)}),
        _rc("aws_iam_role_policy_attachment", "aws_iam_role_policy_attachment.attach",
            after={"role": "glue-etl-role",
                   "policy_arn": "arn:aws:iam::aws:policy/AmazonS3FullAccess"}),
    ])
    kinds = {p["address"]: p["attachment_kind"] for p in am.access_model(plan)["policies"]}
    assert kinds == {
        "aws_iam_policy.standalone": "managed",
        "aws_iam_role_policy.inline": "inline",
        "aws_iam_role_policy_attachment.attach": "attachment",
    }


def test_grants_are_reported_at_statement_granularity_with_actions_and_resource_arns():
    plan = _plan([_rc("aws_iam_role_policy", "aws_iam_role_policy.inline",
                      after={"name": "inline", "role": "glue-etl-role",
                             "policy": _doc(READ_BRONZE)})])
    policy = am.access_model(plan)["policies"][0]
    assert policy["grants"] == [{
        "index": 0, "sid": "ReadBronze", "effect": "Allow",
        "actions": ["s3:GetObject", "s3:ListBucket"], "not_actions": [],
        "resources": ["arn:aws:s3:::lake-bronze", "arn:aws:s3:::lake-bronze/*"],
        "not_resources": [], "negated": False, "conditions": [],
    }]


def test_a_deny_statement_is_preserved_but_is_not_a_grant():
    deny = {"Sid": "NoDelete", "Effect": "Deny", "Action": "s3:DeleteObject",
            "Resource": "arn:aws:s3:::lake-bronze/*"}
    plan = _plan([_rc("aws_iam_policy", "aws_iam_policy.mixed",
                      after={"name": "mixed", "policy": _doc(READ_BRONZE, deny)})])
    policy = am.access_model(plan)["policies"][0]
    assert [s["effect"] for s in policy["statements"]] == ["Allow", "Deny"]
    assert [g["sid"] for g in policy["grants"]] == ["ReadBronze"]


def test_a_notaction_statement_is_flagged_negated_rather_than_reported_as_no_actions():
    """NotAction allows everything EXCEPT the listed actions. Reporting actions=[] on its own
    would understate the grant by the whole action namespace."""
    plan = _plan([_rc("aws_iam_policy", "aws_iam_policy.broad",
                      after={"name": "broad", "policy": _doc(
                          {"Effect": "Allow", "NotAction": "iam:*", "Resource": "*"})})])
    grant = am.access_model(plan)["policies"][0]["grants"][0]
    assert grant["negated"] is True
    assert grant["actions"] == []
    assert grant["not_actions"] == ["iam:*"]
    assert grant["resources"] == ["*"]


def test_statement_conditions_are_reported():
    plan = _plan([_rc("aws_iam_policy", "aws_iam_policy.conditional",
                      after={"name": "conditional", "policy": _doc(
                          {"Effect": "Allow", "Action": "s3:GetObject", "Resource": "*",
                           "Condition": {"StringEquals": {"aws:PrincipalOrgID": "o-abc"}}})})])
    grants = am.access_model(plan)["policies"][0]["grants"]
    assert grants[0]["conditions"] == ["aws:PrincipalOrgID"]


def test_attachment_of_an_aws_managed_policy_is_not_determinable_from_this_plan():
    """The document behind an attached managed-policy ARN is not in the plan at all. Rendering
    it as zero grants would silently hide, for example, AmazonS3FullAccess."""
    plan = _plan([_rc("aws_iam_role_policy_attachment", "aws_iam_role_policy_attachment.attach",
                      after={"role": "glue-etl-role",
                             "policy_arn": "arn:aws:iam::aws:policy/AmazonS3FullAccess"})])
    model = am.access_model(plan)
    policy = model["policies"][0]
    assert policy["policy_arn"] == "arn:aws:iam::aws:policy/AmazonS3FullAccess"
    assert policy["document"]["resolved"] is False
    assert policy["document"]["reason"] == am.MANAGED_POLICY_NOT_IN_PLAN
    assert policy["grants"] is None
    assert policy["statements"] is None
    assert {"address": "aws_iam_role_policy_attachment.attach", "field": "policy_document",
            "reason": am.MANAGED_POLICY_NOT_IN_PLAN} in model["unresolved"]


def test_unknown_policy_document_is_not_determinable_never_an_empty_grant_list():
    plan = _plan([_rc("aws_iam_policy", "aws_iam_policy.computed",
                      after={"name": "computed"}, after_unknown={"policy": True})])
    policy = am.access_model(plan)["policies"][0]
    assert policy["document"]["reason"] == am.UNKNOWN_UNTIL_APPLY
    assert policy["grants"] is None


def test_policies_are_attached_to_the_role_they_name():
    plan = _plan([
        _rc("aws_iam_role", "aws_iam_role.glue",
            after={"name": "glue-etl-role", "assume_role_policy": SERVICE_TRUST}),
        _rc("aws_iam_role_policy", "aws_iam_role_policy.inline",
            after={"name": "inline", "role": "glue-etl-role", "policy": _doc(READ_BRONZE)}),
        _rc("aws_iam_role_policy_attachment", "aws_iam_role_policy_attachment.attach",
            after={"role": "glue-etl-role", "policy_arn": "arn:aws:iam::aws:policy/S3"}),
        _rc("aws_iam_role_policy", "aws_iam_role_policy.other",
            after={"name": "other", "role": "some-other-role", "policy": _doc(READ_BRONZE)}),
    ])
    model = am.access_model(plan)
    role = [r for r in model["roles"] if r["name"] == "glue-etl-role"][0]
    assert role["attached_policies"] == ["aws_iam_role_policy.inline",
                                         "aws_iam_role_policy_attachment.attach"]


def test_a_policy_whose_role_is_unknown_is_not_attached_to_a_guess():
    plan = _plan([
        _rc("aws_iam_role", "aws_iam_role.glue",
            after={"name": "glue-etl-role", "assume_role_policy": SERVICE_TRUST}),
        _rc("aws_iam_role_policy", "aws_iam_role_policy.inline",
            after={"name": "inline", "policy": _doc(READ_BRONZE)},
            after_unknown={"role": True}),
    ])
    model = am.access_model(plan)
    assert model["roles"][0]["attached_policies"] == []
    assert model["policies"][0]["role"] is None
    assert {"address": "aws_iam_role_policy.inline", "field": "role",
            "reason": am.UNKNOWN_UNTIL_APPLY} in model["unresolved"]


# --- Cross-account trust and Lake Formation grants --------------------------------------

def _crossacct_trust(principal, condition=None):
    statement = {"Effect": "Allow", "Action": "sts:AssumeRole",
                 "Principal": {"AWS": principal}}
    if condition:
        statement["Condition"] = condition
    return json.dumps({"Version": "2012-10-17", "Statement": [statement]})


def _role_plan(principal, condition=None):
    return {"resource_changes": [{
        "address": "module.sec.aws_iam_role.partner", "type": "aws_iam_role",
        "mode": "managed", "name": "partner",
        "change": {"actions": ["create"],
                   "after": {"name": "partner",
                             "assume_role_policy": _crossacct_trust(principal, condition)}}}]}


def test_a_trust_from_another_account_is_reported_as_cross_account():
    model = am.access_model(
        _role_plan("arn:aws:iam::445566772201:root"), own_account_id="111122223333")

    grants = am.cross_account_grants(model)

    assert len(grants) == 1
    assert grants[0]["account_id"] == "445566772201"
    assert grants[0]["role"] == "partner"


def test_a_trust_from_our_own_account_is_not_cross_account():
    """Flagging same-account trust as cross-account trains a reviewer to ignore the column."""
    model = am.access_model(
        _role_plan("arn:aws:iam::111122223333:root"), own_account_id="111122223333")

    assert am.cross_account_grants(model) == []


def test_a_cross_account_trust_without_an_external_id_is_flagged():
    """SEC-05. Without sts:ExternalId the role is exposed to the confused-deputy problem:
    anyone who can persuade the trusted account to assume it inherits the access."""
    model = am.access_model(
        _role_plan("arn:aws:iam::445566772201:root"), own_account_id="111122223333")

    grant = am.cross_account_grants(model)[0]

    assert grant["has_external_id"] is False


def test_an_external_id_condition_is_recognised():
    model = am.access_model(
        _role_plan("arn:aws:iam::445566772201:root",
                   {"StringEquals": {"sts:ExternalId": "shared-secret"}}),
        own_account_id="111122223333")

    assert am.cross_account_grants(model)[0]["has_external_id"] is True


def test_a_wildcard_principal_is_reported_even_with_no_account_to_name():
    """`Principal: "*"` names no account, so an account-based filter alone would drop the
    single most dangerous trust policy there is."""
    model = am.access_model(_role_plan("*"), own_account_id="111122223333")

    grants = am.cross_account_grants(model)

    assert len(grants) == 1 and grants[0]["is_wildcard"] is True


def test_an_unresolved_trust_policy_is_reported_not_treated_as_no_trust():
    model = am.access_model({"resource_changes": [{
        "address": "module.sec.aws_iam_role.late", "type": "aws_iam_role", "mode": "managed",
        "name": "late", "change": {"actions": ["create"], "after": {"name": "late"},
                                   "after_unknown": {"assume_role_policy": True}}}]},
        own_account_id="111122223333")

    grants = am.cross_account_grants(model)

    assert len(grants) == 1
    assert grants[0]["determinable"] is False


def test_lake_formation_grants_are_extracted_with_principal_and_permissions():
    plan = {"resource_changes": [{
        "address": "module.gov.aws_lakeformation_permissions.analysts",
        "type": "aws_lakeformation_permissions", "mode": "managed", "name": "analysts",
        "change": {"actions": ["create"], "after": {
            "principal": "arn:aws:iam::111122223333:role/analyst",
            "permissions": ["SELECT", "DESCRIBE"],
            "table": [{"database_name": "marketing", "name": "events"}]}}}]}

    grants = am.lake_formation_grants(plan)

    assert len(grants) == 1
    assert grants[0]["permissions"] == ["SELECT", "DESCRIBE"]
    assert grants[0]["database"] == "marketing"
    assert grants[0]["table"] == "events"


def test_a_lake_formation_grant_whose_principal_is_unknown_says_so():
    plan = {"resource_changes": [{
        "address": "module.gov.aws_lakeformation_permissions.late",
        "type": "aws_lakeformation_permissions", "mode": "managed", "name": "late",
        "change": {"actions": ["create"], "after": {"permissions": ["ALL"]},
                   "after_unknown": {"principal": True}}}]}

    grant = am.lake_formation_grants(plan)[0]

    assert grant["principal"] is None
    assert grant["determinable"] is False


# --- Dataset reachability: which role can reach which bucket ----------------------------

def _bucket(address, name=None, unknown=False):
    change = {"actions": ["create"], "after": {"bucket": name} if name else {}}
    if unknown:
        change["after_unknown"] = {"bucket": True}
    return {"address": address, "type": "aws_s3_bucket", "mode": "managed",
            "name": address.split(".")[-1], "change": change}


def _inline_policy(role, statements):
    return {"address": "module.sec.aws_iam_role_policy.p", "type": "aws_iam_role_policy",
            "mode": "managed", "name": "p",
            "change": {"actions": ["create"], "after": {
                "role": role,
                "policy": json.dumps({"Version": "2012-10-17", "Statement": statements})}}}


def _reach_plan(statements, buckets=None):
    roles = [{"address": "module.sec.aws_iam_role.etl", "type": "aws_iam_role",
              "mode": "managed", "name": "etl",
              "change": {"actions": ["create"], "after": {"name": "etl"}}}]
    return {"resource_changes": roles + [_inline_policy("etl", statements)]
            + (buckets or [_bucket("module.storage.aws_s3_bucket.bronze", "acme-bronze")])}


def test_a_role_reaches_the_bucket_its_policy_names():
    plan = _reach_plan([{"Effect": "Allow", "Action": ["s3:GetObject"],
                         "Resource": ["arn:aws:s3:::acme-bronze/*"]}])
    model = am.access_model(plan)

    reach = am.dataset_reachability(model, plan)

    assert reach[0]["role"] == "etl"
    assert reach[0]["datasets"][0]["address"] == "module.storage.aws_s3_bucket.bronze"
    assert "s3:GetObject" in reach[0]["datasets"][0]["actions"]


def test_a_deny_statement_is_not_reach():
    """A Deny is a real fact about access, but it is not a grant. Counting it as reach
    would report permissions the role does not have."""
    plan = _reach_plan([{"Effect": "Deny", "Action": ["s3:GetObject"],
                         "Resource": ["arn:aws:s3:::acme-bronze/*"]}])
    model = am.access_model(plan)

    assert am.dataset_reachability(model, plan)[0]["datasets"] == []


def test_a_wildcard_resource_reaches_everything_and_says_so():
    """`Resource: "*"` matches no bucket name, so a name-matching join would report the
    broadest possible grant as reaching nothing."""
    plan = _reach_plan([{"Effect": "Allow", "Action": ["s3:*"], "Resource": ["*"]}])
    model = am.access_model(plan)

    entry = am.dataset_reachability(model, plan)[0]

    assert entry["reaches_everything"] is True


def test_a_negated_statement_is_not_reported_as_a_narrow_grant():
    """NotResource allows the whole namespace EXCEPT what it lists. Reading its `resources`
    as the grant understates it enormously."""
    plan = _reach_plan([{"Effect": "Allow", "Action": ["s3:*"],
                         "NotResource": ["arn:aws:s3:::secret/*"]}])
    model = am.access_model(plan)

    entry = am.dataset_reachability(model, plan)[0]

    assert entry["reaches_everything"] is True


def test_an_unresolved_policy_makes_reach_undeterminable_not_empty():
    plan = {"resource_changes": [
        {"address": "module.sec.aws_iam_role.etl", "type": "aws_iam_role", "mode": "managed",
         "name": "etl", "change": {"actions": ["create"], "after": {"name": "etl"}}},
        {"address": "module.sec.aws_iam_role_policy.p", "type": "aws_iam_role_policy",
         "mode": "managed", "name": "p",
         "change": {"actions": ["create"], "after": {"role": "etl"},
                    "after_unknown": {"policy": True}}},
        _bucket("module.storage.aws_s3_bucket.bronze", "acme-bronze")]}
    model = am.access_model(plan)

    entry = am.dataset_reachability(model, plan)[0]

    assert entry["determinable"] is False
    assert entry["datasets"] == []


def test_a_bucket_named_only_at_apply_time_cannot_be_matched_and_is_reported():
    """The grant is real; the join is impossible. Silently reporting no reach hides it."""
    plan = _reach_plan([{"Effect": "Allow", "Action": ["s3:GetObject"],
                         "Resource": ["arn:aws:s3:::acme-bronze/*"]}],
                       buckets=[_bucket("module.storage.aws_s3_bucket.bronze", unknown=True)])
    model = am.access_model(plan)

    entry = am.dataset_reachability(model, plan)[0]

    assert entry["unmatched_resources"], "an ARN matching no known bucket must be reported"
