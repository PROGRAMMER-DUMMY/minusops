"""Tests for the MCP server, driven over JSON-RPC rather than through its functions.

Depends on: core/integrations/mcp_server.py
Shells out to: the server itself, over stdio
Used by: nothing (pytest entry point)
"""
import json
import os
import subprocess
import sys


from core.integrations import mcp_server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _call(method, params=None, request_id=1):
    return mcp_server.handle({"jsonrpc": "2.0", "id": request_id,
                              "method": method, "params": params or {}})


def _tool(name, arguments):
    response = _call("tools/call", {"name": name, "arguments": arguments})
    return response["result"]


def _payload(result):
    return json.loads(result["content"][0]["text"])


# --- Protocol -------------------------------------------------------------------------------

def test_initialize_answers_with_a_protocol_version_and_tool_capability():
    result = _call("initialize")["result"]
    assert result["protocolVersion"] == mcp_server.PROTOCOL_VERSION
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "minusops"


def test_a_notification_gets_no_reply():
    """A reply to a notification is a protocol violation."""
    assert _call("notifications/initialized") is None


def test_tools_list_returns_every_tool_with_a_schema():
    tools = _call("tools/list")["result"]["tools"]
    assert tools
    for tool in tools:
        assert tool["name"] and tool["description"]
        assert tool["inputSchema"]["type"] == "object"


def test_an_unknown_method_is_a_protocol_error():
    assert _call("tools/nonsense")["error"]["code"] == -32601


def test_an_unknown_tool_is_a_protocol_error():
    response = _call("tools/call", {"name": "delete_everything", "arguments": {}})
    assert response["error"]["code"] == -32601


def test_a_missing_required_argument_is_reported_as_such():
    response = _call("tools/call", {"name": "gate_status", "arguments": {}})
    assert response["error"]["code"] == -32602


def test_a_failing_tool_is_a_tool_error_not_a_protocol_error():
    """A well-formed call whose work failed is a tool error, not a protocol error."""
    result = _tool("gate_status", {"dir": os.path.join(ROOT, "does-not-exist")})
    assert "isError" in result


# --- The surface is read-only ----------------------------------------------------------------

def test_no_tool_can_mutate_infrastructure():
    """No tool on the surface can mutate infrastructure."""
    names = {t["name"] for t in mcp_server.TOOLS}
    for forbidden in ("gate_apply", "apply", "gate_approve", "approve", "prove",
                      "seed", "destroy", "run"):
        assert forbidden not in names, f"{forbidden} is exposed over MCP"


def test_every_tool_has_a_handler_and_every_handler_a_tool():
    """Every advertised tool has a handler, and every handler is advertised."""
    assert {t["name"] for t in mcp_server.TOOLS} == set(mcp_server.HANDLERS)


# --- guardrail_check -- the tool that pays for the rest ---------------------------------------

def test_guardrail_check_refuses_a_destructive_command_and_names_the_rule():
    payload = _payload(_tool("guardrail_check", {"command": "terraform destroy"}))
    assert payload["allowed"] is False
    assert payload["rule"] == "TF-01"


def test_guardrail_check_allows_ordinary_work():
    assert _payload(_tool("guardrail_check", {"command": "git status"}))["allowed"] is True


def test_guardrail_check_marks_the_human_gated_commands():
    payload = _payload(_tool("guardrail_check", {"command": "minusctl gate apply --dir x"}))
    assert payload["allowed"] is False
    assert payload["requires_human"] is True


def test_guardrail_check_states_its_own_limit():
    """The payload states what the check is and is not."""
    payload = _payload(_tool("guardrail_check", {"command": "ls"}))
    assert "not a sandbox" in payload["note"]
    assert "IAM credential" in payload["note"]


def test_guardrail_check_does_not_run_the_command(tmp_path):
    """The check evaluates a command without running it."""
    canary = tmp_path / "canary.txt"
    canary.write_text("intact", encoding="utf-8")
    _tool("guardrail_check", {"command": f"rm {canary}"})
    assert canary.read_text(encoding="utf-8") == "intact"


# --- The requirements tools --------------------------------------------------------------------

def test_pillar_next_returns_the_first_question_with_its_options():
    payload = _payload(_tool("pillar_next", {}))
    assert payload["key"] == "policy_baseline"
    assert payload["options"] and payload["depth"]


def test_pillar_next_carries_what_the_facts_already_decide():
    payload = _payload(_tool("pillar_next", {
        "answered": [k for k in pillars_keys() if k != "worker_sizing"],
        "facts": {"daily_gb": 100, "shuffle": "wide"}}))
    assert payload["key"] == "worker_sizing"
    assert payload["derived"]["determinable"] is True


def pillars_keys():
    import pillars as p
    return list(p.PILLAR_KEYS)


def test_pillar_derive_catches_the_small_file_problem():
    payload = _payload(_tool("pillar_derive",
                             {"facts": {"daily_gb": 2, "partitions_per_day": 24}}))
    assert payload["derived"]["partitioning"]["verdict"] == "TOO_SMALL"


def test_pillar_derive_refuses_rather_than_defaulting():
    payload = _payload(_tool("pillar_derive", {"facts": {}}))
    assert payload["derived"]["worker_sizing"]["determinable"] is False
    assert "daily_gb" in payload["missing_facts"]


# --- End to end, over stdio ---------------------------------------------------------------------

def test_the_server_speaks_json_rpc_over_stdio():
    """End to end, through the real process, over stdio."""
    frames = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "guardrail_check", "arguments": {"command": "rm -rf /"}}},
    ]
    done = subprocess.run(
        [sys.executable, "-m", "core.integrations.mcp_server"],
        input="\n".join(json.dumps(f) for f in frames) + "\n",
        capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert done.returncode == 0, done.stderr

    responses = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
    assert len(responses) == 3, "the notification must not have been answered"
    assert responses[0]["result"]["serverInfo"]["name"] == "minusops"
    assert responses[1]["result"]["tools"]
    refusal = json.loads(responses[2]["result"]["content"][0]["text"])
    assert refusal["allowed"] is False


def test_a_malformed_frame_does_not_take_the_server_down():
    """A malformed frame is answered and the loop continues."""
    done = subprocess.run(
        [sys.executable, "-m", "core.integrations.mcp_server"],
        input='{not json\n{"jsonrpc":"2.0","id":9,"method":"tools/list","params":{}}\n',
        capture_output=True, text=True, cwd=ROOT, timeout=60)

    responses = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
    assert responses[0]["error"]["code"] == -32700
    assert responses[1]["result"]["tools"], "the server kept serving after the bad frame"


def test_list_tools_is_available_without_a_client():
    done = subprocess.run(
        [sys.executable, "-m", "core.integrations.mcp_server", "--list-tools"],
        capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert done.returncode == 0
    assert {t["name"] for t in json.loads(done.stdout)} == set(mcp_server.HANDLERS)
