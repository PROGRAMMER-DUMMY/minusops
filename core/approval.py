"""
Approval gate for side-effecting / mutating actions (notifications, ticket creation,
infrastructure changes, etc.).

Two selectable modes:
  - gatekeeper   : require explicit human approval before the action proceeds.
                   In a non-interactive session (no TTY) this safely DENIES.
  - auto-approve : proceed without prompting (still fully audited).

Every decision — approved, denied, or auto-approved — is appended to
.agents/logs/audit.jsonl so there is always a record of who authorised what.

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
import json
import getpass
import argparse
import datetime

LOG_DIR = os.path.join(os.getcwd(), ".agents", "logs")
VALID_MODES = ("gatekeeper", "auto-approve")


def _audit(action, details, mode, decision):
    """Append the approval decision to the shared audit trail."""
    os.makedirs(LOG_DIR, exist_ok=True)
    event = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "operator": getpass.getuser(),
        "action": action,
        "details": details,
        "approval_mode": mode,
        "decision": decision,
    }
    try:
        with open(os.path.join(LOG_DIR, "audit.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
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

    if mode == "auto-approve":
        _audit(action, details, mode, "AUTO_APPROVED")
        print(f"[APPROVAL] auto-approve mode -> proceeding: {action}")
        return True

    # gatekeeper mode — require a human in the loop
    print("=" * 60)
    print("HUMAN-IN-THE-LOOP APPROVAL REQUIRED")
    print("=" * 60)
    print(f"  Action : {action}")
    print(f"  Details: {details}")
    print("-" * 60)

    if not sys.stdin or not sys.stdin.isatty():
        # No interactive terminal — fail closed.
        _audit(action, details, mode, "DENIED_NO_TTY")
        print("[APPROVAL] No interactive terminal available → DENIED (fail-closed).")
        return False

    try:
        answer = input("Approve this action? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"

    approved = answer in ("y", "yes")
    _audit(action, details, mode, "APPROVED" if approved else "DENIED")
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
