"""The published IAM policies, checked against the properties they exist to have.

These files are copied into a customer account. A wildcard in the wrong statement or a deny
that breaks an ordinary apply is not a documentation defect -- it is either an open
escalation path or a control that gets switched off on day one.

Two shapes are asserted throughout:

  the escalation caps are PRESENT      -- CreateRole conditioned on the boundary, PassRole
                                          scoped by PassedToService, the boundary itself
                                          protected from edit and detach
  the denies do not break Terraform    -- convergence destroys and recreates as normal
                                          operation, so a deny must be the irreversible
                                          subset, never delete in general

Depends on: examples/iam/*.json
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IAM = os.path.join(ROOT, "examples", "iam")

PLAN = "plan-role-policy.json"
APPLY = "apply-role-policy.json"
BOUNDARY = "permissions-boundary.json"
SCP = "organization-scp.json"


def _policy(name):
    with open(os.path.join(IAM, name), encoding="utf-8") as handle:
        return json.load(handle)


def _statements(name):
    return _policy(name)["Statement"]


def _by_sid(name, sid):
    return next(s for s in _statements(name) if s.get("Sid") == sid)


def _actions(statement):
    action = statement.get("Action") or statement.get("NotAction") or []
    return [action] if isinstance(action, str) else list(action)


ALL_POLICIES = [PLAN, APPLY, BOUNDARY, SCP]


# --- They are valid policies at all -------------------------------------------------------

@pytest.mark.parametrize("name", ALL_POLICIES)
def test_the_file_is_valid_json_and_a_policy_document(name):
    policy = _policy(name)
    assert policy["Version"] == "2012-10-17"
    assert policy["Statement"], "a policy with no statements grants and denies nothing"


@pytest.mark.parametrize("name", ALL_POLICIES)
def test_every_statement_has_an_effect_and_a_sid(name):
    """A Sid is what an operator quotes when asking why something was refused."""
    for statement in _statements(name):
        assert statement["Effect"] in ("Allow", "Deny")
        assert statement.get("Sid"), f"unnamed statement in {name}: {statement}"


@pytest.mark.parametrize("name", ALL_POLICIES)
def test_placeholders_are_obvious_rather_than_plausible(name):
    """A template that ships a real-looking account id gets deployed unedited."""
    text = open(os.path.join(IAM, name), encoding="utf-8").read()
    assert not re.search(r"\barn:aws:[a-z0-9-]*:[a-z0-9-]*:\d{12}:", text), (
        f"{name} carries a literal 12-digit account id; use <ACCOUNT_ID>")


# --- The plan role cannot mutate or escalate ----------------------------------------------

def test_the_plan_role_can_read_state_but_not_write_it():
    read = _by_sid(PLAN, "ReadTerraformState")
    assert "s3:GetObject" in _actions(read)
    assert "s3:PutObject" not in _actions(read)
    assert "s3:PutObject" in _actions(_by_sid(PLAN, "DenyMutation"))


def test_the_plan_role_can_hold_the_state_lock():
    """plan is not purely read-only: the S3 backend takes a DynamoDB lock. Without this the
    honest fix is -lock=false, and an unlocked plan against shared state races an apply."""
    lock = _by_sid(PLAN, "HoldTheStateLockWhilePlanning")
    assert set(_actions(lock)) >= {"dynamodb:PutItem", "dynamodb:DeleteItem"}
    assert "table/" in lock["Resource"], "the lock grant must be scoped to the lock table"


def test_the_plan_role_may_assume_the_cross_account_read_roles():
    """A hub-and-spoke lakehouse plans across accounts through aliased providers. A blanket
    deny on sts:AssumeRole makes an architecture this project offers unplannable."""
    allow = _by_sid(PLAN, "AssumeOnlyTheNamedCrossAccountReadRoles")
    assert allow["Effect"] == "Allow"
    assert all(":role/minusops-plan" in arn for arn in allow["Resource"])


def test_the_plan_role_may_assume_nothing_else():
    """The escalation this closes: an agent authors a provider block naming an admin role.
    It only works if the caller may assume it."""
    deny = _by_sid(PLAN, "DenyAssumingAnyOtherRole")
    assert deny["Effect"] == "Deny"
    assert "sts:AssumeRole" in _actions(deny)
    assert "NotResource" in deny, "a Resource deny would also deny the two allowed roles"
    assert "Resource" not in deny


def test_the_plan_role_denies_iam_outright():
    assert "iam:*" in _actions(_by_sid(PLAN, "DenyMutation"))


def test_the_plan_role_denies_are_unconditional():
    """An earlier draft made the mutation deny conditional on a principal tag, which is a
    deny that stops applying the moment someone changes a tag."""
    for statement in _statements(PLAN):
        if statement["Effect"] == "Deny":
            assert "Condition" not in statement, statement["Sid"]


# --- The apply role caps what it creates ---------------------------------------------------

def test_role_creation_is_conditioned_on_the_boundary():
    """Terraform must create roles to build a pipeline, and CreateRole plus AttachRolePolicy
    is admin. The answer is not to withhold it but to cap what gets created."""
    create = _by_sid(APPLY, "CreateRolesOnlyInsideTheBoundary")
    condition = create["Condition"]["StringEquals"]["iam:PermissionsBoundary"]
    assert condition.endswith("policy/MinusOpsAppPermissionsBoundary")
    assert "iam:CreateRole" in _actions(create)
    assert "iam:AttachRolePolicy" in _actions(create)


def test_role_writes_are_confined_to_the_managed_path():
    """So the apply role cannot rewrite the plan role, itself, or a platform role."""
    for sid in ("CreateRolesOnlyInsideTheBoundary", "ManageRolesOnTheManagedPathOnly"):
        resources = _by_sid(APPLY, sid)["Resource"]
        for arn in [resources] if isinstance(resources, str) else resources:
            assert "minusops-managed/" in arn, f"{sid} reaches outside the managed path"


def test_pass_role_is_scoped_by_service():
    """Unscoped PassRole is the escalation people miss: hand an admin role to a Lambda you
    control, invoke it, inherit its authority -- holding no destructive permission yourself."""
    passrole = _by_sid(APPLY, "PassRolesOnlyToTheServicesThatRunThem")
    services = passrole["Condition"]["StringEquals"]["iam:PassedToService"]
    assert services, "PassRole with no service condition is unscoped"
    assert all(s.endswith(".amazonaws.com") for s in services)
    assert "minusops-managed/" in passrole["Resource"]
    assert passrole["Resource"] != "*"


def test_the_boundary_cannot_be_detached_or_edited_by_the_role_it_bounds():
    """A boundary the bounded principal can rewrite is decoration."""
    detach = _actions(_by_sid(APPLY, "DenyBoundaryRemoval"))
    assert "iam:DeleteRolePermissionsBoundary" in detach

    tamper = _by_sid(APPLY, "DenyBoundaryPolicyTampering")
    assert "iam:CreatePolicyVersion" in _actions(tamper)
    assert tamper["Resource"].endswith("MinusOpsAppPermissionsBoundary"), (
        "scope it to the boundary ARN: a blanket DeletePolicyVersion deny breaks every "
        "managed-policy update once the 5-version quota is reached")


def test_the_apply_role_cannot_touch_the_platform_roles():
    protected = _by_sid(APPLY, "DenyTouchingThePlatformRoles")["Resource"]
    assert any("minusops-plan" in arn for arn in protected)
    assert any("minusops-apply" in arn for arn in protected)


def test_the_apply_role_grants_no_blanket_iam():
    """`iam:*` anywhere in an Allow makes every cap above decorative."""
    for statement in _statements(APPLY):
        if statement["Effect"] == "Allow":
            assert "iam:*" not in _actions(statement), statement["Sid"]


# --- The boundary is a ceiling, not a grant ------------------------------------------------

def test_the_boundary_forbids_identity_management():
    """This is what stops the escalation chain continuing one level down: a role created
    inside the boundary cannot create another one."""
    deny = _by_sid(BOUNDARY, "NoIdentityManagementEver")
    assert deny["Effect"] == "Deny"
    assert "iam:*" in _actions(deny)
    assert "sts:AssumeRole" in _actions(deny)


def test_the_boundary_lets_a_workload_use_a_key_but_not_manage_one():
    crypto = _actions(_by_sid(BOUNDARY, "WorkloadCryptoAndSecrets"))
    assert "kms:Decrypt" in crypto and "kms:GenerateDataKey" in crypto
    for managing in ("kms:CreateKey", "kms:PutKeyPolicy", "kms:ScheduleKeyDeletion"):
        assert managing not in crypto, f"a workload role can mint or destroy keys: {managing}"


def test_the_boundary_denies_the_irreversible_set():
    deny = _actions(_by_sid(BOUNDARY, "NoIrreversibleDestruction"))
    for action in ("kms:ScheduleKeyDeletion", "s3:DeleteObjectVersion",
                   "cloudtrail:StopLogging"):
        assert action in deny


# --- The SCP denies what cannot be undone, and nothing that breaks an apply -----------------

def test_the_scp_denies_the_irreversible_set():
    deny = _actions(_by_sid(SCP, "DenyIrreversibleDataDestruction"))
    for action in ("kms:ScheduleKeyDeletion", "s3:DeleteObjectVersion",
                   "cloudtrail:StopLogging", "organizations:LeaveOrganization"):
        assert action in deny


def test_the_scp_does_not_deny_put_bucket_versioning():
    """THE correction to the researched design, and the reason this test exists.

    Enabling and suspending versioning are the SAME API call, with no condition key
    separating them. modules/storage-medallion-s3/main.tf declares aws_s3_bucket_versioning,
    so denying it org-wide fails `terraform apply` on every new bucket the primary storage
    module creates. A published template that breaks bucket creation on day one is worse than
    no template; suspension is caught detectively by an AWS Config rule instead.
    """
    for statement in _statements(SCP):
        assert "s3:PutBucketVersioning" not in _actions(statement), (
            "this deny breaks creating every versioned bucket in the catalog")


def test_the_scp_does_not_deny_delete_in_general():
    """Terraform converges by destroying and recreating -- an immutable field change, a
    rename. A deny broader than the irreversible subset breaks ordinary applies."""
    for statement in _statements(SCP):
        if statement["Effect"] != "Deny":
            continue
        for action in _actions(statement):
            assert action not in ("s3:DeleteBucket", "iam:DeleteRole", "glue:DeleteJob",
                                  "dynamodb:DeleteTable", "s3:*", "*"), (
                f"{statement['Sid']} denies {action}, which Terraform does legitimately")


def test_the_scp_records_why_each_omission_is_deliberate():
    """An omission that looks like an oversight gets "fixed" by the next reader."""
    policy = _policy(SCP)
    omissions = policy["_not_denied_deliberately"]
    assert "s3:PutBucketVersioning" in omissions
    reason = " ".join(omissions["s3:PutBucketVersioning"])
    assert "SAME API CALL" in reason
    assert "storage-medallion-s3" in reason


def test_the_scp_requires_a_boundary_on_created_roles():
    """The identity policy conditions CreateRole on the boundary; the SCP repeats it so an
    account admin cannot create an unbounded role either."""
    require = _by_sid(SCP, "RequireABoundaryOnEveryRoleCreated")
    assert require["Effect"] == "Deny"
    assert require["Condition"]["StringNotEquals"]["iam:PermissionsBoundary"].endswith(
        "MinusOpsAppPermissionsBoundary")


# --- The set hangs together -----------------------------------------------------------------

def test_the_boundary_name_is_identical_everywhere():
    """Three files reference it by ARN. A typo in one is a cap that silently does not apply."""
    names = set()
    for name in (APPLY, SCP):
        text = open(os.path.join(IAM, name), encoding="utf-8").read()
        names.update(re.findall(r"policy/([A-Za-z0-9_-]*PermissionsBoundary)", text))
    assert names == {"MinusOpsAppPermissionsBoundary"}, names


def test_the_plan_role_and_the_apply_role_do_not_overlap_on_state_writes():
    """Only the apply role writes state. Whoever writes state decides what Terraform believes
    it owns, so denying DeleteBucket while allowing state writes achieves nothing."""
    plan_writes = {a for s in _statements(PLAN) if s["Effect"] == "Allow"
                   for a in _actions(s)}
    assert "s3:PutObject" not in plan_writes
    apply_writes = _actions(_by_sid(APPLY, "WriteTerraformState"))
    assert "s3:PutObject" in apply_writes


# --- The onboarding template ----------------------------------------------------------------

TEMPLATE = os.path.join(IAM, "onboarding-template.yaml")


def _template():
    """CloudFormation short tags are not YAML, so they are stubbed out. This checks the
    document's SHAPE, not that CloudFormation would accept every intrinsic."""
    yaml = pytest.importorskip("yaml")

    class Loader(yaml.SafeLoader):
        pass

    yaml.add_multi_constructor("!", lambda loader, suffix, node: None, Loader=Loader)
    with open(TEMPLATE, encoding="utf-8") as handle:
        return yaml.load(handle, Loader=Loader)


def test_the_template_creates_both_roles_and_the_boundary():
    """One Quick Create instead of five hand-assembled policy documents. Every manual step
    is a step someone gets subtly wrong, and IAM that is subtly wrong reads as working."""
    resources = _template()["Resources"]
    assert {"PlanRole", "ApplyRole", "AppPermissionsBoundary"} <= set(resources)
    assert {"StateBucket", "LockTable", "StateKey"} <= set(resources)


def test_mfa_is_off_by_default_and_says_why():
    """`aws:MultiFactorAuthPresent` is absent for SSO, SAML and OIDC sessions, so requiring
    it DENIES an SSO operator even after a hardware key. Defaulting it on would ship a
    template that locks out the workflow this repo's own docs recommend."""
    template = _template()
    assert template["Parameters"]["RequireMfaOnApply"]["Default"] == "false"
    note = template["Metadata"]["MfaParameter"]
    assert "Identity Center" in note and "DENIES" in note


def test_the_state_bucket_denies_writes_from_anything_but_the_apply_role():
    """Whoever writes state decides what Terraform believes it owns."""
    policy = _template()["Resources"]["StateBucketPolicy"]["Properties"]["PolicyDocument"]
    deny = next(s for s in policy["Statement"] if s["Sid"] == "OnlyTheApplyRoleMutatesState")
    assert deny["Effect"] == "Deny"
    assert "s3:PutObject" in deny["Action"]


def test_the_state_bucket_is_versioned_and_retained():
    bucket = _template()["Resources"]["StateBucket"]
    assert bucket["DeletionPolicy"] == "Retain"
    assert bucket["Properties"]["VersioningConfiguration"]["Status"] == "Enabled"


def test_the_plan_role_in_the_template_carries_read_only_access():
    """Without it the role can read state and nothing else, and plan fails on the first
    Describe call."""
    plan = _template()["Resources"]["PlanRole"]["Properties"]
    assert any("ReadOnlyAccess" in arn for arn in plan["ManagedPolicyArns"])


def test_the_template_and_the_json_policies_agree_on_the_sids():
    """Two copies of the same design drift. The JSON is the reviewable reference and the
    template is what gets deployed, so the statement names must match."""
    apply_role = _template()["Resources"]["ApplyRole"]["Properties"]["Policies"][0]
    template_sids = {s["Sid"] for s in apply_role["PolicyDocument"]["Statement"]}
    json_sids = {s["Sid"] for s in _statements(APPLY)}
    missing = json_sids - template_sids
    assert not missing, f"in apply-role-policy.json but not in the template: {sorted(missing)}"
