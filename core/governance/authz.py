"""
Approver authorization (RBAC seam for the approval/deploy gates).

The control plane never invents identity — it reads the operator from the
environment your IdP/CI populates, and authorizes against a configured approver
allowlist. Wire `MINUS_OPERATOR` to your SSO/OIDC subject (or CI actor) and
`MINUS_APPROVERS` (or .minus/approvers.json) to the set of principals allowed to
approve a deploy. If no allowlist is configured the gate runs in "open" mode
(single-operator dev), which is reported explicitly so it can never be mistaken
for an enforced control.

2026-07-07: `operator()` itself is unchanged (still MINUS_OPERATOR / OS user) --
`MINUS_APPROVERS`/`.minus/approvers.json` are compared against whatever string
`operator()` returns, and an env var is trivially spoofable by anyone with shell
access. `verified_operator()` is the AWS-IAM-backed alternative: it derives the
principal from the ARN `sts get-caller-identity` returns for the credentials
ACTUALLY active right now, not a self-reported string. plan_gate.py's approve/apply
RBAC (where the two-person production rule needs to mean something) prefers this;
other callers (e.g. approval.py's Slack/Jira notify gate, which may run with no
cloud session at all) are unaffected, deliberately -- this is scoped to the deploy
gate's approver/planner identity, not a global replacement of operator().
"""
import getpass
import json
import os
import re

APPROVERS_ENV = "MINUS_APPROVERS"
OPERATOR_ENV = "MINUS_OPERATOR"
APPROVERS_FILE = os.path.join(".minus", "approvers.json")


def operator():
    """The acting principal. Prefer the IdP/CI-provided identity over the OS user."""
    return (os.environ.get(OPERATOR_ENV) or "").strip() or getpass.getuser()


def _principal_from_arn(arn):
    """Extract a stable, human-meaningful identity from an STS-verified ARN.
    Returns None for an ARN that carries no distinguishing principal (root, or one
    that doesn't match a recognized shape) -- never guesses."""
    if not arn:
        return None
    # Assumed role (SSO permission-set assumption, or an MFA-gated deploy role assumed
    # with --role-session-name) -- the session name is the real "who" for RBAC purposes.
    m = re.match(r"^arn:aws:sts::\d+:assumed-role/[^/]+/(.+)$", arn)
    if m:
        return m.group(1)
    # A direct IAM user (no SSO/role assumption in play).
    m = re.match(r"^arn:aws:iam::\d+:user/(.+)$", arn)
    if m:
        return m.group(1)
    return None


def verified_operator():
    """The AWS-STS-verified acting principal, or None if no cloud session is active or
    the ARN carries no recoverable principal (e.g. root). Unlike operator(), this
    cannot be spoofed by setting an environment variable -- it reflects whichever
    credentials are cryptographically live right now, the same `sts get-caller-identity`
    call the credential-posture check already makes."""
    try:
        from providers.base import get_provider
        posture = get_provider().credential_posture()
    except Exception:
        return None
    if not posture.get("connected"):
        return None
    return _principal_from_arn(posture.get("arn"))


def configured_approvers(workspace="."):
    """Union of MINUS_APPROVERS (comma-separated) and .minus/approvers.json approvers."""
    approvers = set()
    env_value = os.environ.get(APPROVERS_ENV, "")
    approvers.update(a.strip() for a in env_value.split(",") if a.strip())
    path = os.path.join(workspace, APPROVERS_FILE)
    if os.path.exists(path):
        try:
            data = json.loads(open(path, encoding="utf-8").read())
            approvers.update(a.strip() for a in data.get("approvers", []) if str(a).strip())
        except (OSError, json.JSONDecodeError):
            pass
    return approvers


def authorize(op=None, workspace="."):
    """
    Return (allowed, mode, reason).

    mode is "enforced" when an allowlist exists, "open" when none is configured.
    """
    op = op or operator()
    approvers = configured_approvers(workspace)
    if not approvers:
        return True, "open", "no approver allowlist configured (open mode)"
    if op in approvers:
        return True, "enforced", f"{op} is an authorized approver"
    return False, "enforced", f"{op} is not in the approver allowlist"
