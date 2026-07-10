"""
Approval gate for side-effecting / mutating actions (notifications, ticket creation,
infrastructure changes, etc.).

Two selectable modes:
  - gatekeeper   : require explicit human approval before the action proceeds.
                   In a non-interactive session (no TTY) this safely DENIES.
  - auto-approve : proceed without prompting (still fully audited).

Authorization (RBAC): in BOTH modes, if an approver allowlist is configured
(MINUS_APPROVERS / .minus/approvers.json) the acting operator must be on it, or the
request is denied. With no allowlist the gate runs in "open" mode (recorded as such).

Every decision — approved, denied, auto-approved, or unauthorized — is appended to
the tamper-evident hash-chained audit trail (.agents/logs/audit.jsonl).

Usage as a library:
    from approval import request_approval
    if request_approval("send-slack-alert", "Notify #cost-alerts about anomaly", mode):
        ... perform the action ...

Usage from the CLI (for testing / scripting):
    python approval.py --action send-slack --details "..." --mode gatekeeper
    # exit code 0 = approved, 1 = denied
"""
import os
import sys
import getpass
import argparse
import datetime

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
import audit_chain  # noqa: E402
import authz  # noqa: E402

LOG_DIR = os.path.join(os.getcwd(), ".agents", "logs")
VALID_MODES = ("gatekeeper", "auto-approve")


def _audit(action, details, mode, decision, **extra):
    """Append the approval decision to the shared tamper-evident audit trail."""
    os.makedirs(LOG_DIR, exist_ok=True)
    event = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "operator": authz.operator(),
        "os_user": getpass.getuser(),
        "component": "approval",
        "action": action,
        "details": details,
        "approval_mode": mode,
        "decision": decision,
    }
    event.update(extra)
    try:
        audit_chain.append(os.path.join(LOG_DIR, "audit.jsonl"), event)
    except Exception as e:
        print(f"[APPROVAL] WARNING: could not write audit record: {e}", file=sys.stderr)


def request_approval(action, details, mode="gatekeeper"):
    """
    Return True if the action is authorised, False otherwise.
    Always writes an audit record.
    """
    mode = (mode or "gatekeeper").lower()
    if mode not in VALID_MODES:
        print(f"[APPROVAL] Unknown mode '{mode}', defaulting to 'gatekeeper'.", file=sys.stderr)
        mode = "gatekeeper"

    # RBAC: enforce the approver allowlist (if configured) before anything else.
    op = authz.operator()
    allowed, authz_mode, reason = authz.authorize(op)
    if not allowed:
        _audit(action, details, mode, "DENIED_NOT_AUTHORIZED", operator_checked=op, authz_mode=authz_mode, reason=reason)
        print(f"[APPROVAL] DENIED: {op} is not an authorized approver ({reason}).", file=sys.stderr)
        return False

    if mode == "auto-approve":
        _audit(action, details, mode, "AUTO_APPROVED", authz_mode=authz_mode)
        print(f"[APPROVAL] auto-approve mode -> proceeding: {action} (approver: {op}, {authz_mode})")
        return True

    # gatekeeper mode — require a human in the loop
    print("=" * 60)
    print("HUMAN-IN-THE-LOOP APPROVAL REQUIRED")
    print("=" * 60)
    print(f"  Action  : {action}")
    print(f"  Details : {details}")
    print(f"  Approver: {op} ({authz_mode})")
    print("-" * 60)

    if not sys.stdin or not sys.stdin.isatty():
        # No interactive terminal — fail closed.
        _audit(action, details, mode, "DENIED_NO_TTY", authz_mode=authz_mode)
        print("[APPROVAL] No interactive terminal available → DENIED (fail-closed).")
        return False

    try:
        answer = input("Approve this action? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"

    approved = answer in ("y", "yes")
    _audit(action, details, mode, "APPROVED" if approved else "DENIED", authz_mode=authz_mode)
    print(f"[APPROVAL] {'APPROVED' if approved else 'DENIED'}: {action}")
    return approved


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Approval gate (gatekeeper | auto-approve)")
    parser.add_argument("--action", required=True, help="Short action name being authorised")
    parser.add_argument("--details", required=True, help="Description of the action / state change")
    parser.add_argument("--mode", default="gatekeeper", choices=VALID_MODES, help="Approval mode")
    args = parser.parse_args()

    ok = request_approval(args.action, args.details, args.mode)
    sys.exit(0 if ok else 1)
