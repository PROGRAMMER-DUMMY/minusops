"""
MCP server: MinusOps reachable from any MCP client, over stdio.

Exposes the gate's verdicts, not its controls. Every tool is read-only -- `gate_status`,
`plan_summary`, `guardrail_check`, `pillar_next`, `pillar_derive`. Nothing mutates, nothing
approves, and `gate apply` is deliberately absent: a mutating tool would put an apply behind
a tool call any client could make.

JSON-RPC 2.0 over stdio: `initialize`, `tools/list`, `tools/call`. Standard library only.

Depends on: core/governance/plan_gate.py, core/governance/agent_guardrails.py,
    core/architecture/pillars.py, core/reporting/runs.py
Shells out to: nothing
Used by: any MCP client (claude_desktop_config.json, .cursor/mcp.json, ...),
    tests/test_mcp_server.py
"""
import json
import os
import sys

_CORE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("governance", "architecture", "reporting", "generation"):
    sys.path.insert(0, os.path.join(_CORE, _sub))

import agent_guardrails  # noqa: E402
import pillars  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER = {"name": "minusops", "version": "0.1.0"}

TOOLS = [
    {
        "name": "gate_status",
        "description": (
            "The deploy gate's verdict for a Terraform directory: whether it was planned, "
            "the plan hash, whether a human approved that exact hash, detected cloud drift, "
            "and the next safe command. Read-only."),
        "inputSchema": {
            "type": "object",
            "properties": {"dir": {"type": "string",
                                   "description": "Terraform directory to report on."}},
            "required": ["dir"],
        },
    },
    {
        "name": "plan_summary",
        "description": (
            "What a recorded plan would change: resource counts by action and by type, the "
            "destructive-change classification, and the author's impact statement if the "
            "plan is staged. Reads the recorded plan; never runs terraform."),
        "inputSchema": {
            "type": "object",
            "properties": {"dir": {"type": "string"}},
            "required": ["dir"],
        },
    },
    {
        "name": "guardrail_check",
        "description": (
            "Would MinusOps refuse this shell command? Returns allowed, the rule id, and the "
            "reason. For clients with no pre-execution hook of their own. Checks only -- it "
            "never runs the command."),
        "inputSchema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "pillar_next",
        "description": (
            "The next requirements question to ask, with its options, the modules each maps "
            "to, and whatever the answers so far already determine -- a Glue worker plan, an "
            "S3 object-size verdict, a Kinesis shard count."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "answered": {"type": "array", "items": {"type": "string"},
                             "description": "Pillar keys already covered."},
                "facts": {"type": "object",
                          "description": "Stated numbers: daily_gb, partitions_per_day, ..."},
            },
        },
    },
    {
        "name": "pillar_derive",
        "description": (
            "What the stated facts already decide. Each answer either carries its arithmetic "
            "and the source it rests on, or says which fact is missing -- it never fills a "
            "gap with a default."),
        "inputSchema": {
            "type": "object",
            "properties": {"facts": {"type": "object"}},
            "required": ["facts"],
        },
    },
]


def _gate_status(arguments):
    import plan_gate
    return plan_gate.gate_status(arguments["dir"])


def _plan_summary(arguments):
    import collections
    import plan_gate

    directory = arguments["dir"]
    pending_path = plan_gate._pending_path(directory)
    if not os.path.exists(pending_path):
        return {"planned": False,
                "reason": "no recorded plan for this directory",
                "next": "minusctl gate verify, then minusctl gate plan"}
    with open(pending_path, encoding="utf-8") as handle:
        pending = json.load(handle)

    summary = {
        "planned": True,
        "plan_hash": pending.get("plan_hash"),
        "planner": pending.get("planner"),
        "destroy": pending.get("destroy", False),
        "impact": pending.get("impact"),
    }
    try:
        classification = plan_gate._classify_plan(directory)
        summary["autonomous_eligible"] = classification.get("autonomous_eligible")
        summary["findings"] = classification.get("findings")
        plan_json, _err = plan_gate._plan_json(directory)
        if plan_json:
            counts = collections.Counter()
            for change in plan_json.get("resource_changes", []):
                for action in change["change"]["actions"]:
                    counts[f"{action}:{change['type']}"] += 1
            summary["changes"] = dict(counts)
    except Exception as exc:                                   # noqa: BLE001
        # Advisory only: a missing terraform binary must not fail the status read.
        summary["classification_unavailable"] = str(exc)
    return summary


def _guardrail_check(arguments):
    decision = agent_guardrails.evaluate(arguments["command"])
    return {
        "allowed": decision["allowed"],
        "rule": decision["rule"],
        "reason": decision["reason"],
        "requires_human": decision["requires_human"],
        "note": ("This is a guardrail against a mistake, not a sandbox. It refuses the "
                 "shapes commands arrive in; an interpreter running a file is not one of "
                 "them. The IAM credential is the boundary."),
    }


def _pillar_next(arguments):
    rendered = pillars.next_pillar(arguments.get("answered") or [],
                                   arguments.get("facts") or {})
    return rendered if rendered is not None else {"complete": True,
                                                  "message": "All 18 pillars are answered."}


def _pillar_derive(arguments):
    facts = arguments.get("facts") or {}
    return {"facts": facts,
            "derived": pillars.derive(facts),
            "missing_facts": pillars.missing_facts(facts)}


HANDLERS = {
    "gate_status": _gate_status,
    "plan_summary": _plan_summary,
    "guardrail_check": _guardrail_check,
    "pillar_next": _pillar_next,
    "pillar_derive": _pillar_derive,
}


def handle(request):
    """One JSON-RPC request in, one response out. None for a notification."""
    method = request.get("method")
    request_id = request.get("id")

    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER,
        })

    if method in ("notifications/initialized", "initialized"):
        return None                                    # a notification takes no reply

    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return _error(request_id, -32601, f"no such tool: {name!r}")
        try:
            payload = handler(params.get("arguments") or {})
        except KeyError as exc:
            return _error(request_id, -32602, f"missing required argument: {exc}")
        except Exception as exc:                       # noqa: BLE001
            # A tool result with isError, not a protocol error: the call was well-formed.
            return _result(request_id, {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            })
        return _result(request_id, {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}],
            "isError": False,
        })

    return _error(request_id, -32601, f"unknown method: {method!r}")


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(stdin=None, stdout=None):
    """Read line-delimited JSON-RPC from stdin, write responses to stdout.

    A malformed line is answered with a parse error; the loop continues.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, f"parse error: {exc}")
        else:
            response = handle(request)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--list-tools" in argv:
        print(json.dumps([{"name": t["name"], "description": t["description"]}
                          for t in TOOLS], indent=2))
        return 0
    return serve()


if __name__ == "__main__":
    sys.exit(main())
