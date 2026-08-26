"""
Governed visual-to-code reconciliation (PRD v13 FR-05).

Everywhere else the console reads. Here a drag on a canvas rewrites `terraform/main.tf`, so
this file is the one place in the console that can undo the deploy gate. The gate's whole
premise is that infrastructure changes are reviewed HCL bound to an approved plan hash; a
mouse gesture that edits that HCL quietly defeats it completely.

The design is therefore two calls, and the split is the safety property:

  propose()  is INERT. It reads the file, computes what would change, and returns a diff.
             It writes nothing -- not the HCL, not the decision, not the audit chain. A
             proposal is not an event.
  confirm()  writes, and only when `confirmed is True`. Not truthy -- True. `confirmed="no"`
             is a non-empty string and would pass a truthiness check, which on this surface
             would turn a dismissed modal into an applied edit.

PLAN INVALIDATION USES THE MECHANISM THAT ALREADY EXISTS. Confirming deletes the standing
approval records, exactly as the one manual `approval-revoked` entry in this repo's audit
chain did, so `plan_gate.gate_status()` reports `approved: False`. A separate STALE_PLAN
flag stored somewhere else would be a second answer to "is this approved", and the gate --
not this module -- is what decides whether apply runs. Two answers eventually disagree, and
the wrong one would be the permissive one.

Belt and braces: writing main.tf also trips the gate's own source-drift check
(`_reject_if_source_stale`), which refuses apply when the source changed after the plan.
This module does not reimplement that; it just makes the invalidation visible immediately
rather than at the next apply attempt.

Depends on: core/governance/audit_chain.py (stdlib-only, so PRD v13 invariant 4 holds)
Shells out to: nothing
Used by: app/console_app.py (canvas edit interception), tests/test_reconciler.py
"""
import datetime
import difflib
import getpass
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "governance"))
import audit_chain  # noqa: E402

AUDIT_ACTION = "ARCH_VISUAL_RECONCILIATION"
STALE_PLAN = "STALE_PLAN (NEEDS_REPLAN)"
NEXT_COMMAND = "minusctl gate plan"

_TF_FILE = os.path.join("terraform", "main.tf")
_DECISION_FILE = "architecture_decision.json"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def propose(run_root, change, author=None, audit_path=None):
    """Compute what a canvas edit WOULD do. Writes nothing.

    `change` is {kind, target, attribute, from, to}. Returns the payload the Architecture
    Change Review Modal renders: author, timestamp, plain-English summary, safety warnings,
    and a unified diff of the HCL.

    `audit_path` is accepted and deliberately unused -- it keeps the signature symmetrical
    with `confirm()` so a caller cannot wire the two up differently by accident.
    """
    tf_path = os.path.join(run_root, _TF_FILE)
    original = _read(tf_path)
    old_value = str(change.get("from") or "")
    new_value = str(change.get("to") or "")
    target = str(change.get("target") or "unknown resource")
    attribute = str(change.get("attribute") or "")

    proposal = {
        "run_root": run_root,
        "change": dict(change),
        "author": author or getpass.getuser(),
        "at": _now(),
        "tf_path": tf_path,
        "applicable": False,
        "reason": "",
        "summary": "",
        "warnings": [],
        "diff": "",
        "updated_hcl": "",
    }

    if not original:
        proposal["reason"] = f"terraform source not found at {tf_path}"
        return proposal

    if not old_value or old_value not in original:
        # The canvas is describing HCL that is not there. The two have already diverged,
        # and writing anything now would be guessing which one is right.
        proposal["reason"] = (
            f"reference {old_value!r} not found in {_TF_FILE}; the canvas and the code have "
            "diverged, so no edit can be derived safely")
        return proposal

    updated = original.replace(old_value, new_value)
    proposal.update(
        applicable=True,
        updated_hcl=updated,
        summary=(f"Re-routed {target} {attribute}: {old_value} -> {new_value}".strip()),
        warnings=_warnings(target, old_value, new_value),
        diff="\n".join(difflib.unified_diff(
            original.splitlines(), updated.splitlines(),
            fromfile=f"a/{_TF_FILE}", tofile=f"b/{_TF_FILE}", lineterm="")),
    )
    return proposal


def _warnings(target, old_value, new_value):
    """The safety and lineage notice (FR-05.2 item 3)."""
    notes = [
        "This edits generated Terraform in Git. Git remains the authoritative state; the "
        "canvas only proposes.",
        f"Any standing approval is revoked on confirmation and the run becomes {STALE_PLAN}.",
        f"Re-run `{NEXT_COMMAND}` before any apply.",
    ]
    if "bronze" in new_value and "gold" in old_value:
        notes.insert(0, "Data lineage change: this moves a consumer upstream from curated "
                        "Gold to raw Bronze. Downstream contracts and quality gates that "
                        "assume conformed data may no longer hold.")
    elif "gold" in new_value and "bronze" in old_value:
        notes.insert(0, "Data lineage change: this moves a consumer from raw Bronze to "
                        "curated Gold. Verify the transformation is still reached.")
    return notes


def _approval_records(state_dir):
    """Approval files for this run's gate state. Split out so a test can point it at a
    temporary directory without reaching into plan_gate's on-disk layout."""
    if not state_dir or not os.path.isdir(state_dir):
        return []
    return sorted(glob.glob(os.path.join(state_dir, "*.json")))


def _gate_approval_dir(run_root):
    """Where plan_gate keeps approvals for this run's terraform directory.

    Derived rather than imported: this module is standard-library-only by invariant, and
    importing plan_gate would pull in the provider stack. The path shape is asserted by
    plan_gate's own tests; if it moves, that is a coordinated change.
    """
    tf_dir = os.path.abspath(os.path.join(run_root, "terraform"))
    key = tf_dir.replace(os.sep, "_").replace(":", "")
    return os.path.join(os.getcwd(), ".agents", "logs", "plan_gate", key, "approvals")


def confirm(proposal, confirmed=False, audit_path=None, operator=None):
    """Apply a reviewed proposal. Writes ONLY when `confirmed is True`.

    Identity check, not truthiness: `confirmed="no"` is a non-empty string, and treating it
    as consent would turn a dismissed modal into an infrastructure edit.
    """
    result = {
        "applied": False, "status": None, "approvals_revoked": 0,
        "next_command": NEXT_COMMAND, "reason": "", "files": [],
    }

    if confirmed is not True:
        result["reason"] = "not confirmed; nothing was written"
        return result
    if not proposal or not proposal.get("applicable"):
        result["reason"] = proposal.get("reason") if proposal else "no proposal"
        return result

    run_root = proposal["run_root"]
    tf_path = proposal["tf_path"]
    author = operator or proposal.get("author") or getpass.getuser()

    with open(tf_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(proposal["updated_hcl"])
    result["files"].append(tf_path)

    decision_path = os.path.join(run_root, _DECISION_FILE)
    if os.path.exists(decision_path):
        try:
            decision = json.loads(_read(decision_path)) or {}
        except ValueError:
            decision = {}
        decision.setdefault("reconciliations", []).append({
            "at": proposal["at"], "author": author,
            "summary": proposal["summary"], "change": proposal["change"],
        })
        decision["status"] = STALE_PLAN
        with open(decision_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(decision, handle, indent=2)
            handle.write("\n")
        result["files"].append(decision_path)

    # Revoke by deleting the approval record -- see the module docstring on why this rather
    # than a parallel staleness flag.
    for record in _approval_records(_gate_approval_dir(run_root)):
        try:
            os.remove(record)
            result["approvals_revoked"] += 1
        except OSError:
            pass

    result["status"] = STALE_PLAN
    result["applied"] = True
    _audit(audit_path, author, proposal, result)
    return result


def _audit(audit_path, author, proposal, result):
    """Append to the tamper-evident chain, THROUGH audit_chain.append.

    Not a bare `open(path, "a")`. That lands the record in the same file and still omits
    `prev_hash`/`entry_hash`, which breaks every link after it -- this module wrote 85 such
    entries into this repo's own audit.jsonl before the bug was found, and `audit verify`
    exited 1 with 218 errors. A log that reads correctly and fails verification is worse
    than no log: the failure surfaces as tampering rather than as a defect.

    Never raises: a confirmed edit that already touched the filesystem must not be reported
    as failed because the log was unwritable.
    """
    path = audit_path or os.path.join(os.getcwd(), ".agents", "logs", "audit.jsonl")
    record = {
        "timestamp": _now(),
        "operator": author,
        "action": AUDIT_ACTION,
        "details": (f"{proposal['summary']} | files: "
                    f"{', '.join(os.path.basename(f) for f in result['files'])} | "
                    f"approvals revoked: {result['approvals_revoked']} | status: {STALE_PLAN}"),
        "status": "RECORDED",
    }
    try:
        audit_chain.append(path, record)
    except OSError:
        pass


def format_proposal(proposal):
    """The review modal as text, for the terminal path and for tests to read."""
    if not proposal.get("applicable"):
        return f"ARCHITECTURE CHANGE REFUSED\n{proposal.get('reason', '')}"
    lines = [
        "ARCHITECTURE CHANGE REVIEW",
        "=" * 60,
        f"Author    : {proposal['author']}",
        f"Timestamp : {proposal['at']}",
        "",
        f"Change    : {proposal['summary']}",
        "",
        "Warnings:",
    ]
    lines.extend(f"  - {w}" for w in proposal["warnings"])
    lines += ["", "Proposed diff:", proposal["diff"], "",
              f"Confirm to apply. Nothing has been written yet."]
    return "\n".join(lines)
