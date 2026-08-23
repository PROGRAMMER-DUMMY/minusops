"""
Multi-agent execution trace, read out of the audit chain (PRD v13 FR-04).

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
import json
import os

NOT_RUN = "NOT_RUN"
RECORDED = "RECORDED"

DEFAULT_AUDIT_PATH = os.path.join(".agents", "logs", "audit.jsonl")

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
        stage = {
            "key": spec["key"], "agent": spec["agent"], "summary": spec["summary"],
            "artifact": artifact, "artifact_present": present,
            "status": NOT_RUN, "at": None, "operator": None,
            "audit_hash": None, "details": None,
        }
        if record:
            stage.update(
                status=RECORDED,
                at=record.get("timestamp"),
                operator=record.get("operator"),
                # FR-04.3: the cryptographic link back into the chain. A stage claiming it
                # ran without one is unfalsifiable, so `trace` never emits that.
                audit_hash=record.get("entry_hash"),
                details=record.get("details"),
            )
            if stage["audit_hash"]:
                ran.append(stage)
                continue
            stage["status"] = NOT_RUN
            stage["details"] = "audit entry carries no hash; treating as unproven"
        pending.append(stage)

    ran.sort(key=lambda s: str(s["at"] or ""))
    return {
        "stages": ran + pending,
        "evidence_available": bool(entries),
        "recorded": len(ran),
        "audit_path": audit_path,
    }


def active_agents(run_root=None):
    """Currently-running subagents (FR-04.1).

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
