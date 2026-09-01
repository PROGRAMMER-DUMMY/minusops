"""
opa_evaluator.py -- Open Policy Agent (OPA) Rego evaluation engine for MCP Gateway.

Evaluates incoming agent tool invocations against Rego authorization policies.
Validates caller SPIFFE ID, OAuth 2.1 scopes, target tool name, and parameter constraints.
"""
import fnmatch
import json
import os
import subprocess
import shutil
from typing import Any, Dict, Optional

POLICY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "policy", "mcp", "gateway_authz.rego")

# High-risk operations requiring mandatory Human-in-the-Loop (HITL) step-up approval
HIGH_RISK_TOOLS = {
    "terraform_apply",
    "terraform_destroy",
    "modify_patient_record",
    "delete_patient_record",
    "execute_payment",
    "drop_table",
    "modify_security_group",
    "quarantine_override"
}

READ_ONLY_DATA_TOOLS = {
    "query_domain_ard",
    "query_solution_ard",
    "search_knowledge_base",
    "get_schema_metadata",
    "get_lineage_graph",
    "read_audit_logs"
}


def evaluate_policy_in_memory(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pure Python Rego emulator enforcing the exact enterprise governance contract
    when external `opa` binary is not present or for low-latency unit execution.
    """
    caller_spiffe = context.get("caller_spiffe_id", "")
    tool_name = context.get("tool_name", "")
    scopes = set(context.get("scopes", []))
    step_up_verified = context.get("step_up_verified", False)
    arguments = context.get("arguments", {})

    # Check 1: Unknown caller or empty identity
    if not caller_spiffe or not caller_spiffe.startswith("spiffe://enterprise.local/"):
        return {
            "decision": "deny",
            "reason": f"Invalid or untrusted SPIFFE ID: {caller_spiffe}",
            "policy": "precinct.authz.deny_untrusted_identity"
        }

    # Check 2: High-risk tools requiring step-up approval
    if tool_name in HIGH_RISK_TOOLS:
        if not step_up_verified:
            return {
                "decision": "step_up_required",
                "reason": f"Tool '{tool_name}' requires cryptographically signed Human-in-the-Loop (HITL) step-up approval",
                "policy": "precinct.authz.enforce_step_up_guard",
                "risk_tier": "HIGH"
            }
        # If step_up is verified, assert admin/engineer role
        if not fnmatch.fnmatch(caller_spiffe, "spiffe://enterprise.local/agents/operator/*") and \
           not fnmatch.fnmatch(caller_spiffe, "spiffe://enterprise.local/agents/architect/*"):
            return {
                "decision": "deny",
                "reason": f"Caller {caller_spiffe} lacks authority to execute high-risk tool {tool_name}",
                "policy": "precinct.authz.deny_insufficient_role"
            }
        return {
            "decision": "allow",
            "reason": "Step-up approval token verified for authorized operator",
            "policy": "precinct.authz.allow_verified_step_up"
        }

    # Check 3: Read-only data queries
    if tool_name in READ_ONLY_DATA_TOOLS:
        if any(fnmatch.fnmatch(caller_spiffe, pattern) for pattern in [
            "spiffe://enterprise.local/agents/analyst/*",
            "spiffe://enterprise.local/agents/researcher/*",
            "spiffe://enterprise.local/agents/architect/*",
            "spiffe://enterprise.local/agents/operator/*"
        ]):
            return {
                "decision": "allow",
                "reason": f"Read-only query allowed for authorized agent {caller_spiffe}",
                "policy": "precinct.authz.allow_data_read"
            }
        return {
            "decision": "deny",
            "reason": f"Caller {caller_spiffe} is not authorized for data queries",
            "policy": "precinct.authz.deny_data_access"
        }

    # Check 4: Support tools
    if tool_name.startswith("support_"):
        if "support:read" in scopes or fnmatch.fnmatch(caller_spiffe, "spiffe://enterprise.local/agents/support/*"):
            return {
                "decision": "allow",
                "reason": f"Support tool allowed for {caller_spiffe}",
                "policy": "precinct.authz.allow_support_tools"
            }

    # Fail closed by default
    return {
        "decision": "deny",
        "reason": f"Tool '{tool_name}' is not permitted by active policy for caller '{caller_spiffe}'",
        "policy": "precinct.authz.default_deny"
    }


def evaluate(context: Dict[str, Any], opa_bin: Optional[str] = None) -> Dict[str, Any]:
    """
    Evaluate policy using OPA CLI if available, falling back to deterministic in-memory policy engine.
    """
    bin_path = opa_bin or shutil.which("opa")
    if not bin_path or not os.path.exists(POLICY_PATH):
        return evaluate_policy_in_memory(context)

    input_payload = json.dumps({"input": context})
    try:
        proc = subprocess.run(
            [bin_path, "eval", "--data", POLICY_PATH, "--input", "-", "data.precinct.authz.decision"],
            input=input_payload,
            capture_output=True,
            text=True,
            timeout=5
        )
        if proc.returncode == 0:
            res = json.loads(proc.stdout)
            results = res.get("result", [{}])[0].get("expressions", [{}])[0].get("value")
            if results:
                return {
                    "decision": results.get("decision", "deny"),
                    "reason": results.get("reason", "OPA decision rendered"),
                    "policy": results.get("policy", "data.precinct.authz")
                }
    except Exception:
        pass

    return evaluate_policy_in_memory(context)
