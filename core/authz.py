"""
Approver authorization (RBAC seam for the approval/deploy gates).

The control plane never invents identity — it reads the operator from the
environment your IdP/CI populates, and authorizes against a configured approver
allowlist. Wire `MINUS_OPERATOR` to your SSO/OIDC subject (or CI actor) and
`MINUS_APPROVERS` (or .minus/approvers.json) to the set of principals allowed to
approve a deploy. If no allowlist is configured the gate runs in "open" mode
(single-operator dev), which is reported explicitly so it can never be mistaken
for an enforced control.
"""
import getpass
import json
import os

APPROVERS_ENV = "MINUS_APPROVERS"
OPERATOR_ENV = "MINUS_OPERATOR"
APPROVERS_FILE = os.path.join(".minus", "approvers.json")


def operator():
    """The acting principal. Prefer the IdP/CI-provided identity over the OS user."""
    return (os.environ.get(OPERATOR_ENV) or "").strip() or getpass.getuser()


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
