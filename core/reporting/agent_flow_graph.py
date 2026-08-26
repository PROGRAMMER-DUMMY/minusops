"""
Agent execution lineage graph.

Compiles what `agent_tracer.trace()` recorded into a directed acyclic graph the console can
draw: one node per pipeline stage, one edge per handoff, and a per-node status drawn from a
fixed vocabulary.

THIS MODULE EMITS DATA, NEVER MARKUP. WP-03 compiles a structure and the console picks the
renderer; a module that returned HTML would have made that choice on the console's behalf,
and would also have to be trusted to escape what it interpolates.

Two properties are load-bearing and should survive edits:

**A status is never inferred upward.** RUNNING is set only when a caller passes `active`,
because nothing in an audit log can tell you a stage is in flight -- an absent record means
"no evidence", which is exactly what a stage that has not started looks like. Guessing
RUNNING would turn silence into a claim of progress.

**Chain integrity is never assumed.** `build_flow` renders beside a "verify audit trail"
indicator. A caller that did not run `agent_tracer.verify_chain()` gets CHAIN_NOT_CHECKED,
never CHAIN_VERIFIED. The two are different facts, and lighting the indicator green for a
caller who never looked is the one failure mode this whole section exists to prevent.

Depends on: core/governance/agent_tracer.py (for the stage records it compiles; imported by
    the caller, not here -- this module takes the trace as an argument)
Shells out to: nothing. Standard library only.
Used by: app/console_app.py (FLOW -> AGENT FLOW), tests/test_agent_flow.py
"""

# --- FR-04 status vocabulary --------------------------------------------------------------
COMPLETED = "completed"
WAITING_ON_HUMAN = "waiting-on-human"
BLOCKED = "blocked"
RUNNING = "running"
NOT_RUN = "not-run"

STATUSES = (COMPLETED, WAITING_ON_HUMAN, BLOCKED, RUNNING, NOT_RUN)

# Not one of agent_tracer's three chain states on purpose: VERIFIED, BROKEN and ABSENT are
# all answers, and "nobody asked" is not an answer.
CHAIN_NOT_CHECKED = "NOT_CHECKED"

# An audit record's own verdict fields that mean the stage did not simply pass. Matched as
# prefixes because the gate writes qualified reasons (`DENIED_NOT_AUTHORIZED`), and a reader
# that only knew the bare token would render a denial as a success.
_BLOCKED_PREFIXES = ("DENIED", "REJECTED", "REFUSED", "FAILED", "BLOCKED")
_WAITING_PREFIXES = ("PENDING", "AWAITING", "WAITING")

_RECORDED = "RECORDED"


def _verdict(value):
    return str(value or "").strip().upper()


def status_of(stage, active=()):
    """The FR-04 status for one traced stage.

    Order matters. A supervisor's word that a stage is live outranks the log, because the log
    is written on completion; after that, a refusal outranks the fact that a record exists at
    all, or a denied approval would render as "completed" purely because the gate wrote the
    denial down.
    """
    if stage.get("key") in set(active or ()):
        return RUNNING

    for value in (stage.get("gate_decision"), stage.get("outcome")):
        verdict = _verdict(value)
        if verdict.startswith(_BLOCKED_PREFIXES):
            return BLOCKED
        if verdict.startswith(_WAITING_PREFIXES):
            return WAITING_ON_HUMAN

    if stage.get("status") == _RECORDED and stage.get("audit_hash"):
        return COMPLETED
    return NOT_RUN


def build_flow(traced, chain=None, active=(), decision=None, order=None):
    """Compile a traced run into {"nodes", "edges", "chain"}.

    `decision` is attached to the architecture node alone. It is that agent's output, and
    hanging it on every node would imply each one weighed the same alternatives.

    `order` is the declared pipeline order (agent_tracer.STAGES keys). It is a PARAMETER
    rather than an import because this module compiles data and must not depend on the
    governance package to describe itself -- and because a hidden import of the very module
    whose output it consumes is a cycle waiting to happen. Given none, stage order is taken
    as received; trace() sorts recorded stages ahead of pending ones so the timeline reads
    chronologically, which is not the same as the pipeline order a DAG needs.
    """
    stages = list((traced or {}).get("stages") or [])
    if order:
        rank = {key: index for index, key in enumerate(order)}
        stages.sort(key=lambda s: rank.get(s.get("key"), len(rank)))

    nodes = []
    for index, stage in enumerate(stages):
        artifact = stage.get("artifact")
        previous = stages[index - 1].get("artifact") if index else None
        nodes.append({
            "id": stage.get("key"),
            "label": stage.get("agent"),
            "summary": stage.get("summary"),
            "status": status_of(stage, active),
            "persona": stage.get("persona") or stage.get("agent"),
            "model_tier": stage.get("model_tier") or "stdlib",
            "artifact": artifact,
            "artifact_present": stage.get("artifact_present", False),
            "inputs": [previous] if previous else [],
            "outputs": [artifact] if artifact else [],
            "audit_hash": stage.get("audit_hash"),
            "operator": stage.get("operator"),
            "at": stage.get("at"),
            "latency_seconds": stage.get("latency_seconds"),
            "decision": decision if stage.get("key") == "architecture" else None,
        })

    edges = [{"from": nodes[i]["id"], "to": nodes[i + 1]["id"], "label": "handoff"}
             for i in range(len(nodes) - 1)]

    return {"nodes": nodes, "edges": edges, "chain": _chain_state(chain)}


def _chain_state(chain):
    if not isinstance(chain, dict) or not chain.get("state"):
        return {"state": CHAIN_NOT_CHECKED, "broken_at": None, "checked": 0}
    return {"state": chain.get("state"), "broken_at": chain.get("broken_at"),
            "checked": chain.get("checked", 0)}


def find_node(graph, node_id):
    for node in (graph or {}).get("nodes") or []:
        if node.get("id") == node_id:
            return node
    return None
