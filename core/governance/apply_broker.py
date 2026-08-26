"""
Release an apply: verify, independently of the agent, that this exact plan was approved.

THE GAP THIS CLOSES. plan_gate already binds apply to a plan hash, and that binding is real.
But every part of it -- the plan record, the approval record, the check -- lives on the
machine the agent is working on, and the agent can write all three. The binding proves the
apply matches the plan; it does not prove a human ever saw either.

WHY THIS IS NOT AN OIDC TOKEN BROKER. The obvious design is a service that mints a credential
carrying the plan digest as a claim, so the trust policy itself refuses a mismatched apply.
It cannot be built out of AWS primitives: an IAM trust policy matches claims in the token,
and no CI provider issues a token carrying a digest you choose. You would have to run an
identity provider -- and then that service is the thing worth attacking, holds the authority
it was invented to constrain, and needs its own answer to every question asked here.

So the release check runs where the agent is not: inside the CI job, after the human approval
gate, against an approval store the agent's role cannot write. That is a weaker claim than a
cryptographic one, and it is honest about which. What it gives you:

    the runner re-derives the hash from the plan file it is about to apply
    it reads the approval from a store the PLAN role has no PutObject on
    it refuses on mismatch, on a stale approval, and on self-approval

The runner could in principle skip the check. The runner is not the agent, does not hold the
agent's credentials, and runs a workflow file that lives under branch protection -- so
skipping it is a reviewable change to a protected file rather than one more tool call.

TWO-PERSON IS ENFORCED HERE, NOT ASSUMED. plan_gate records `planner` and `approver`. This
refuses when they are the same principal, which authz's "open" mode does not: an account with
no approver allowlist configured lets one person plan and approve, and the audit record then
shows two fields with one name in both.

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

# An approval older than this is refused. Not because approval expires as a matter of
# principle, but because the world moves: an approval made against last week's account state
# was made against facts that may no longer hold, and re-approving is cheap.
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
    """Should this apply be released? Returns a decision dict; never raises.

    `approval` is the record as read from the store -- a dict, or None when the store had
    nothing for this hash. `planner` is who produced the plan, for the two-person check.

    Fails closed on every ambiguity. A record that cannot be parsed, a timestamp that cannot
    be read, an approver that cannot be identified: all refusals. The one thing this must
    never do is release because it could not tell.
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
        # The case the whole module exists for: an approval for a DIFFERENT plan.
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
    """Two names for the same person. Compares the last ARN segment, so
    `arn:aws:sts::1:assumed-role/deploy/alice` and `alice` are recognised as one."""
    def tail(value):
        return str(value).strip().rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower()
    return tail(left) == tail(right)


def record(decision, audit_path=None, operator=None):
    """Write the release decision to the tamper-evident chain.

    Both outcomes are recorded. A refused release is the more interesting entry: it says
    someone tried to apply something that was not approved, and that is exactly the event an
    auditor is looking for.
    """
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
        # A release that already happened must not be reported as failed because the log was
        # unwritable. The same rule reconciler follows.
        return None


def load_approval(directory, plan_hash):
    """Read the approval for a hash from the local gate state.

    The LOCAL store is the fallback, not the design. In CI the approval should be read from a
    location the plan role cannot write -- an S3 prefix its policy denies PutObject on -- and
    passed to verify() directly. Reading it from the same disk the agent works on proves
    only that the file is there.
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
