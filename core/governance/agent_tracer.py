"""
Multi-agent execution trace, read out of the audit chain.

WHAT THIS IS NOT: a picture of the lifecycle. FR-04.2 names eight relay stages, and the
audit chain currently records six actions -- `synthesize`, `plan`, `verify`, `approve`,
`apply`, `export`. Stages like `reflection` and `proving` have no audit action behind them
yet, and several agents named in the PRD (grill-me, diagrammer) leave their evidence as a
file on disk rather than an audit entry.

So every stage reports one of two things: RECORDED, with the audit entry hash that proves
it, or NOT_RUN. There is no third state and no inference. This is the lesson the proving
harness learned the expensive way -- a timeline showing eight green nodes for a run that
only ever synthesised is not an optimistic UI, it is a false record, and this one renders
next to a cryptographic audit link which makes it look authoritative.

FAIL-OPEN, like `cloud_drift`: a missing or corrupt audit file yields a trace of NOT_RUN
stages and `evidence_available: False`. The console must still render for a fresh run.

Depends on: nothing (standard library only -- PRD v13 invariant 4)
Shells out to: nothing
Used by: app/console_app.py (View 3), tests/test_agent_tracer.py
"""
import datetime
import hashlib
import json
import os

NOT_RUN = "NOT_RUN"
RECORDED = "RECORDED"

DEFAULT_AUDIT_PATH = os.path.join(".agents", "logs", "audit.jsonl")
DECISION_FILE = "architecture_decision.json"

# Three states, never one boolean. VERIFIED and BROKEN are the obvious two;
# ABSENT exists because a log that was never hash-chained is neither. Folding it into BROKEN
# sends an auditor hunting a tampering incident that did not happen, and folding it into
# VERIFIED presents an unprotected file as evidence.
CHAIN_VERIFIED = "VERIFIED"
CHAIN_BROKEN = "BROKEN"
CHAIN_ABSENT = "ABSENT"

# What the first record links back to. Must match audit_chain.GENESIS.
GENESIS = "0" * 64

# The relay, in lifecycle order. `actions` are the audit actions that evidence a stage --
# empty means nothing in the chain records it yet, so the stage can only ever be NOT_RUN
# until an action is added. That is deliberate: an empty tuple is a visible statement that
# this stage has no evidence source, rather than a stage quietly missing from the catalog.
STAGES = (
    {"key": "requirements", "agent": "grill-me-agent", "actions": (),
     "produces": os.path.join("requirements.json"),
     "summary": "Captured business goals and quantified non-functional requirements."},
    {"key": "architecture", "agent": "architect-agent", "actions": ("author_resource",),
     "produces": os.path.join("architecture_decision.json"),
     "summary": "Researched services and selected the module composition."},
    {"key": "synthesis", "agent": "synthesizer-engine", "actions": ("synthesize",),
     "produces": os.path.join("terraform", "main.tf"),
     "summary": "Generated governed Terraform HCL from the decision."},
    {"key": "diagram", "agent": "diagrammer-agent", "actions": (),
     "produces": os.path.join("reports", "architecture.drawio"),
     "summary": "Compiled the Draw.io blueprint and its 1-click editor URL."},
    {"key": "reflection", "agent": "reflector-agent", "actions": ("verify",),
     "produces": os.path.join("reports", "reflector_verdict.json"),
     "summary": "Evaluated the independent pre-plan gates."},
    {"key": "plan", "agent": "orchestrator", "actions": ("plan",),
     "produces": os.path.join("reports", "plan.json"),
     "summary": "Generated the plan and computed its binding SHA-256 hash."},
    {"key": "approval", "agent": "deploy-gate", "actions": ("approve",),
     "produces": os.path.join("reports", "approval.json"),
     "summary": "Recorded a human approval bound to that exact plan hash."},
    {"key": "proving", "agent": "proving-agent", "actions": (),
     "produces": os.path.join("reports", "proving_report.json"),
     "summary": "Executed the synthetic end-to-end data proof."},
    {"key": "notify", "agent": "slack-agent / teams-agent",
     "actions": ("send-slack-alert", "send-teams-alert", "trigger-pagerduty-incident"),
     "summary": "Dispatched the approval card to the on-call channel.",
     "produces": os.path.join("reports", "notification.json")},
)

# Persona and model tier per stage. Kept beside the catalog rather than
# inside it so the v13 STAGES literal and everything reading it stay exactly as they were.
#
# `model_tier` is what actually executes the stage in THIS repo, not what the PRD's example
# ledger shows. Only `requirements` and `architecture` are driven by a model -- they are
# Claude skills under `.agents/skills/`. Everything else, the notifier included, is
# deterministic Python, so its tier is `stdlib` and its inference cost is genuinely zero.
# A stage missing from this map yields persona/model_tier of None rather than a plausible
# default: an invented persona on an audit surface is still an invention.
AGENT_ROLES = {
    "requirements": ("Requirements interviewer", "pro"),
    "architecture": ("Principal cloud architect", "pro"),
    "synthesis": ("Deterministic HCL generator", "stdlib"),
    "diagram": ("Blueprint compiler", "stdlib"),
    "reflection": ("Independent pre-plan reviewer", "stdlib"),
    "plan": ("Plan gate operator", "stdlib"),
    "approval": ("Human-in-the-loop approver", "stdlib"),
    "proving": ("End-to-end proving harness", "stdlib"),
    "notify": ("Transport dispatcher", "stdlib"),
}


def _read_entries(audit_path):
    """Audit records, newest last. A malformed line is skipped, not fatal.

    One bad line must not discard the chain around it: this file is appended to by several
    processes, and a torn write in the middle would otherwise blank the whole timeline.
    """
    if not audit_path or not os.path.exists(audit_path):
        return []
    entries = []
    try:
        with open(audit_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("action"):
                    entries.append(record)
    except OSError:
        return []
    return entries


def _latest_by_action(entries):
    """The most recent record per action. A run planned three times shows the plan that
    stands, not the first attempt."""
    latest = {}
    for record in entries:
        action = record.get("action")
        stamp = str(record.get("timestamp") or "")
        current = latest.get(action)
        if current is None or stamp >= str(current.get("timestamp") or ""):
            latest[action] = record
    return latest


def _canonical(record):
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def _entry_hash(prev_hash, record_without_hash):
    """The chain format `audit_chain.append` writes, re-derived here.

    Not imported from `audit_chain`: this module is asserted to import only the standard
    library, and a cross-module import
    would break that. The two must stay byte-identical -- if `audit_chain._entry_hash`
    changes, this changes with it or every verification here reports a false BROKEN.

    Note the PRD's FR-06 wording, `SHA256(timestamp + operator + action + details +
    prev_hash)`, describes a WEAKER chain than the one on disk: it covers five named fields,
    while the real one covers the whole record. Verifying against the PRD's version would
    let a tamperer edit any unnamed field (status, decision, dir) undetected, so the real
    format wins.
    """
    return hashlib.sha256(
        (prev_hash + _canonical(record_without_hash)).encode("utf-8")).hexdigest()


def verify_chain(audit_path=None):
    """Tamper-check `.agents/logs/audit.jsonl`.

    Returns {"state", "broken_at", "checked", "errors", "audit_path"} where state is one of
    CHAIN_VERIFIED, CHAIN_BROKEN or CHAIN_ABSENT, and `broken_at` is the 1-based index of
    the first record that failed (counting non-empty lines, the way a reader counts them).

    FAILS CLOSED, unlike everything else in this module. A trace that guesses low is merely
    unhelpful; an integrity indicator that guesses high is a false all-clear on the one
    control an auditor relies on. So an unparseable line, a record with no `prev_hash`, an
    unreadable file -- every one of them is BROKEN, never VERIFIED. The cost of that choice
    is a false alarm on a corrupt-but-untampered file, which is the right way round.

    Deliberately stricter than `audit_chain.chain_status`, which tolerates a legacy prefix of
    un-chained records written before hashing existed. Tolerating that here would mean a
    forged record with its hashes stripped off reads as legacy rather than as an attack.
    """
    audit_path = audit_path or DEFAULT_AUDIT_PATH
    result = {"state": CHAIN_ABSENT, "broken_at": None, "checked": 0, "errors": [],
              "audit_path": audit_path}
    if not audit_path or not os.path.exists(audit_path):
        result["errors"].append("no audit log at this path; nothing has been chained yet")
        return result

    prev = GENESIS
    index = 0
    unchained = 0

    def _break(at, message):
        result["state"] = CHAIN_BROKEN
        result["broken_at"] = at
        result["errors"].append(f"record {at}: {message}")

    try:
        with open(audit_path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                index += 1
                try:
                    record = json.loads(line)
                except ValueError as exc:
                    _break(index, f"unparseable JSON ({exc}); chain cannot be verified past here")
                    break
                if not isinstance(record, dict):
                    _break(index, "not a JSON object; chain cannot be verified past here")
                    break
                if "entry_hash" not in record:
                    # Only tolerated while NOTHING has been chained yet -- a plain log is
                    # ABSENT, decided after the loop. Once one record is chained, a record
                    # without a hash is a hole in the chain.
                    if result["checked"]:
                        _break(index, "record carries no entry_hash after chaining began "
                                      "(possible insertion)")
                        break
                    unchained += 1
                    continue
                if unchained:
                    _break(index, "chaining begins after un-chained records; the prefix is "
                                  "unverifiable and may have been rewritten")
                    break
                if record.get("prev_hash") != prev:
                    _break(index, "prev_hash does not link to the previous record "
                                  "(chain broken, reordered, or a record was removed)")
                    break
                without = {k: v for k, v in record.items() if k != "entry_hash"}
                if record.get("entry_hash") != _entry_hash(prev, without):
                    _break(index, "entry_hash does not match the record (record was modified)")
                    break
                prev = record["entry_hash"]
                result["checked"] += 1
    except OSError as exc:
        # Unreadable is not the same as untampered. Fail closed.
        _break(index + 1, f"could not read the audit log ({exc})")
        return result

    if result["state"] == CHAIN_BROKEN:
        return result
    if not result["checked"]:
        result["errors"].append(
            f"{unchained} record(s) present, none of them hash-chained; there is no chain to verify")
        return result
    result["state"] = CHAIN_VERIFIED
    return result


def _parse_time(value):
    try:
        return datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _apply_latency(stages):
    """Seconds between one recorded stage landing and the next, in place.

    This is the gap between audit entries, which is NOT the same as how long the step took
    to execute: nothing records when a step started. The first recorded stage therefore gets
    None rather than 0.0 -- zero would render as an instantaneous step, None renders as what
    it is, unmeasurable from this evidence. Same reason mixed naive/aware timestamps give up
    instead of guessing a timezone.
    """
    previous = None
    for stage in stages:
        moment = _parse_time(stage.get("at")) if stage.get("status") == RECORDED else None
        if moment is not None and previous is not None:
            try:
                stage["latency_seconds"] = (moment - previous).total_seconds()
            except TypeError:
                stage["latency_seconds"] = None
        if moment is not None:
            previous = moment


def decision_branches(run_root=None):
    """The architecture decision behind a run: what was chosen, why, and what was rejected
.

    Returns present=False when the record is missing or unreadable. Every list is empty
    rather than filled with a placeholder when the record does not carry that field -- the
    decision files this repo has actually written carry no `alternatives` at all, and an
    invented tradeoff in a decision inspector is worse than a blank one.
    """
    result = {"present": False, "path": None, "architecture": None, "justification": None,
              "chosen_modules": [], "rejected_alternatives": [], "failure_modes_addressed": []}
    if not run_root:
        return result
    path = os.path.join(run_root, DECISION_FILE)
    result["path"] = path
    if not os.path.exists(path):
        return result
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return result
    if not isinstance(record, dict):
        return result

    result["present"] = True
    # Two spellings in the wild: `selected_architecture` from architecture_decision.template()
    # and `architecture` from the accelerator-written records under runs/.
    result["architecture"] = (record.get("selected_architecture")
                              or record.get("architecture") or None)
    result["justification"] = record.get("decision_summary") or None
    result["chosen_modules"] = [str(m) for m in (record.get("selected_modules") or [])
                                if str(m).strip()]
    for item in (record.get("alternatives") or []):
        if isinstance(item, dict) and "reject" in str(item.get("decision", "")).lower():
            result["rejected_alternatives"].append({
                "name": item.get("name"), "reason": item.get("reason"),
                "decision": item.get("decision"),
            })
    result["failure_modes_addressed"] = [str(f) for f in (record.get("failure_modes") or [])]
    return result


def trace(run_root=None, audit_path=None):
    """The relay timeline for a run.

    Returns {"stages": [...], "evidence_available": bool, "recorded": int}. Recorded stages
    are sorted chronologically; NOT_RUN stages follow in catalog order, because they have no
    time to sort by and interleaving them by lifecycle position would imply a sequence that
    did not happen.
    """
    audit_path = audit_path or DEFAULT_AUDIT_PATH
    entries = _read_entries(audit_path)
    latest = _latest_by_action(entries)

    ran, pending = [], []
    for spec in STAGES:
        record = next((latest[a] for a in spec["actions"] if a in latest), None)
        artifact = spec["produces"]
        present = bool(run_root and artifact
                       and os.path.exists(os.path.join(run_root, artifact)))
        persona, tier = AGENT_ROLES.get(spec["key"], (None, None))
        stage = {
            "key": spec["key"], "agent": spec["agent"], "summary": spec["summary"],
            "artifact": artifact, "artifact_present": present,
            "status": NOT_RUN, "at": None, "operator": None,
            "audit_hash": None, "details": None,
            # The audit record's own verdict fields, carried as scalars so a consumer can
            # tell a gate that blocked from one that passed without handling the raw record
            # (which would put unredacted payloads on a UI surface -- FR-07).
            "outcome": None, "gate_decision": None,
            "persona": persona, "model_tier": tier, "latency_seconds": None,
        }
        if record:
            stage.update(
                status=RECORDED,
                at=record.get("timestamp"),
                operator=record.get("operator"),
                # The cryptographic link back into the chain. A stage claiming it
                # ran without one is unfalsifiable, so `trace` never emits that.
                audit_hash=record.get("entry_hash"),
                details=record.get("details"),
                outcome=record.get("status"),
                gate_decision=record.get("decision"),
            )
            if stage["audit_hash"]:
                ran.append(stage)
                continue
            stage["status"] = NOT_RUN
            stage["details"] = "audit entry carries no hash; treating as unproven"
        pending.append(stage)

    ran.sort(key=lambda s: str(s["at"] or ""))
    _apply_latency(ran)
    return {
        "stages": ran + pending,
        "evidence_available": bool(entries),
        "recorded": len(ran),
        "audit_path": audit_path,
    }


def active_agents(run_root=None):
    """Currently-running subagents.

    Always empty today, and deliberately so. Nothing in this repo supervises subagent
    processes, so there is no source of truth to read. Returning a plausible-looking
    "architect-agent, 4.2s, model pro" would be fabrication on the one surface whose entire
    job is to report what is actually happening. When a supervisor exists, read it here.
    """
    return []


def format_trace(result):
    """The timeline as text, for the terminal and for pasting into a ticket."""
    lines = ["MULTI-AGENT EXECUTION TRACE", "=" * 60]
    if not result.get("evidence_available"):
        lines.append("No audit evidence found; every stage is reported as NOT_RUN.")
    for stage in result.get("stages", []):
        mark = "[OK]" if stage["status"] == RECORDED else "[  ]"
        when = stage["at"] or "-"
        lines.append(f"{mark} {stage['key']:<14} {stage['agent']:<26} {when}")
        if stage["status"] == RECORDED:
            lines.append(f"       audit {stage['audit_hash'][:16]}  {stage['summary']}")
        else:
            lines.append(f"       {stage['summary']}")
    return "\n".join(lines)
