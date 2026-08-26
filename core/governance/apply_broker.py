"""
Release check for an apply: was this exact plan approved by someone other than its planner?

Runs in CI, after the human approval gate, against an approval record read from a store the
plan role cannot write. Refuses on hash mismatch, self-approval, an unattributed or stale
approval, and any record it cannot parse.

Not a token broker: an IAM trust policy matches claims in the token, and no CI provider
issues one carrying a plan digest.

Depends on: core/governance/audit_chain.py, core/governance/plan_gate.py
Shells out to: nothing. The S3 read is the caller's, passed in.
Used by: .github/workflows/deploy.yml, tests/test_apply_broker.py
"""
import datetime
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import audit_chain  # noqa: E402

# Approvals older than this are refused: the account state they were made against may have
# moved.
DEFAULT_MAX_AGE_SECONDS = 24 * 3600

RELEASED = "RELEASED"
REFUSED = "REFUSED"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _parse(timestamp):
    try:
        return datetime.datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _refuse(reason, detail, **extra):
    decision = {"released": False, "status": REFUSED, "reason": reason, "detail": detail}
    decision.update(extra)
    return decision


def verify(plan_hash, approval, planner=None, max_age_seconds=DEFAULT_MAX_AGE_SECONDS,
           now=None):
    """Return a decision dict for this plan hash. Fails closed on any ambiguity; never raises.

    `approval` is the record read from the store, or None if there was none. `planner` is who
    produced the plan, for the two-person check.
    """
    if not plan_hash:
        return _refuse("no_plan_hash", "the caller supplied no plan hash to verify")

    if approval is None:
        return _refuse("no_approval",
                       f"no approval record exists for plan {plan_hash[:16]}",
                       plan_hash=plan_hash)

    if not isinstance(approval, dict):
        return _refuse("malformed_approval",
                       "the approval record is not an object", plan_hash=plan_hash)

    recorded = approval.get("plan_hash")
    if recorded != plan_hash:
        return _refuse("hash_mismatch",
                       f"the approval is for plan {str(recorded)[:16]}, not {plan_hash[:16]}",
                       plan_hash=plan_hash, approved_hash=recorded)

    approver = (approval.get("approved_by") or approval.get("approver") or "").strip()
    if not approver:
        return _refuse("no_approver",
                       "the approval names nobody; an unattributable approval is not one",
                       plan_hash=plan_hash)

    if planner and approver and _same_principal(approver, planner):
        return _refuse("self_approval",
                       f"{approver!r} both planned and approved this. Two people, or the "
                       f"gate is one person agreeing with themselves",
                       plan_hash=plan_hash, approver=approver, planner=planner)

    approved_at = _parse(approval.get("approved_at"))
    if approved_at is None:
        return _refuse("no_approval_time",
                       "the approval carries no readable timestamp, so its age cannot be "
                       "checked", plan_hash=plan_hash)

    age = ((now or _now()) - approved_at).total_seconds()
    if age < 0:
        return _refuse("approval_in_the_future",
                       f"the approval is timestamped {abs(age):.0f}s in the future; a clock "
                       f"is wrong or the record was written by hand", plan_hash=plan_hash)
    if age > max_age_seconds:
        return _refuse("approval_stale",
                       f"approved {age / 3600:.1f}h ago, older than the "
                       f"{max_age_seconds / 3600:.0f}h limit. Re-approve against current "
                       f"account state", plan_hash=plan_hash, age_seconds=int(age))

    return {
        "released": True,
        "status": RELEASED,
        "reason": None,
        "detail": (f"plan {plan_hash[:16]} approved by {approver} "
                   f"{age / 60:.0f} minutes ago"),
        "plan_hash": plan_hash,
        "approver": approver,
        "planner": planner,
        "age_seconds": int(age),
    }


def _same_principal(left, right):
    """True if both strings name the same principal, compared on the last ARN segment."""
    def tail(value):
        return str(value).strip().rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower()
    return tail(left) == tail(right)


def record(decision, audit_path=None, operator=None):
    """Append the release decision, released or refused, to the tamper-evident chain."""
    path = audit_path or os.path.join(os.getcwd(), ".agents", "logs", "audit.jsonl")
    entry = {
        "timestamp": _now().isoformat(),
        "operator": operator or decision.get("approver") or "unknown",
        "action": "apply-release",
        "component": "apply_broker",
        "status": decision["status"],
        "reason": decision.get("reason"),
        "detail": decision.get("detail"),
        "plan_hash": decision.get("plan_hash"),
    }
    try:
        return audit_chain.append(path, entry)
    except OSError:
        # An unwritable log must not fail a release that already happened.
        return None


def load_approval(directory, plan_hash):
    """Read the approval for a hash from the local gate state, or None.

    Fallback, not the design: this reads the same disk the agent writes. In CI, read the
    approval from a store the plan role cannot write and pass it to verify() directly.
    """
    import plan_gate
    path = plan_gate._approved_path(directory, plan_hash)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return None


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Verify an apply is released by a human approval of this exact plan.")
    parser.add_argument("--dir", required=True, help="Terraform directory being applied.")
    parser.add_argument("--plan-hash", help="Hash to verify. Defaults to the current plan.")
    parser.add_argument("--approval-file",
                        help="Approval record read from an out-of-band store. Preferred in "
                             "CI over the local gate state.")
    parser.add_argument("--max-age-hours", type=float,
                        default=DEFAULT_MAX_AGE_SECONDS / 3600)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    sys.path.insert(0, _HERE)
    import plan_gate

    plan_hash = args.plan_hash
    planner = None
    if not plan_hash:
        plan_hash, error = plan_gate._plan_hash(args.dir)
        if not plan_hash:
            print(f"[broker] cannot hash the plan: {error}", file=sys.stderr)
            return 1

    pending_path = plan_gate._pending_path(args.dir)
    if os.path.exists(pending_path):
        try:
            planner = json.load(open(pending_path, encoding="utf-8")).get("planner")
        except (json.JSONDecodeError, OSError):
            planner = None

    if args.approval_file:
        try:
            approval = json.load(open(args.approval_file, encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            approval = None
            print(f"[broker] could not read {args.approval_file}: {exc}", file=sys.stderr)
    else:
        approval = load_approval(args.dir, plan_hash)

    decision = verify(plan_hash, approval, planner=planner,
                      max_age_seconds=args.max_age_hours * 3600)
    record(decision)

    if args.json:
        print(json.dumps(decision, indent=2))
    elif decision["released"]:
        print(f"[broker] RELEASED: {decision['detail']}")
    else:
        print(f"[broker] REFUSED ({decision['reason']}): {decision['detail']}",
              file=sys.stderr)
    return 0 if decision["released"] else 1


if __name__ == "__main__":
    sys.exit(main())
