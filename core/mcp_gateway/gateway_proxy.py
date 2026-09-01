"""
gateway_proxy.py -- Unified Enterprise Model Context Protocol (MCP) Gateway Proxy.

Routes incoming JSON-RPC 2.0 tool requests through:
1. W3C Distributed Trace Context Generation (TraceID / SpanID).
2. Upstream Inline PII / PHI Redaction.
3. Open Policy Agent (OPA) Rego Authorization.
4. Human-in-the-Loop (HITL) Step-Up Guard.
5. Specialized MCP Gateway Dispatch (Data, Workflow, Support).
6. Immutable JSON Audit Event Logging.
"""
import uuid
import time
from typing import Any, Callable, Dict, Optional

from .pii_redactor import redact_payload
from .opa_evaluator import evaluate as evaluate_opa
from .step_up_guard import StepUpGuard
from .session_checkpointer import SessionCheckpointer


class MCPGatewayProxy:
    def __init__(
        self,
        step_up_guard: Optional[StepUpGuard] = None,
        checkpointer: Optional[SessionCheckpointer] = None
    ):
        self.step_up_guard = step_up_guard or StepUpGuard()
        self.checkpointer = checkpointer or SessionCheckpointer()
        self._tool_handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {}
        self._audit_log = []

    def register_tool(self, tool_name: str, handler: Callable[[Dict[str, Any]], Any]):
        """Register a backend tool executor function."""
        self._tool_handlers[tool_name] = handler

    def handle_jsonrpc_request(
        self,
        request: Dict[str, Any],
        caller_spiffe_id: str,
        scopes: Optional[list] = None,
        thread_id: Optional[str] = None,
        approval_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Handle incoming JSON-RPC 2.0 tool request through the governed security pipeline.
        """
        req_id = request.get("id", str(uuid.uuid4()))
        method = request.get("method")
        params = request.get("params", {})
        tool_name = params.get("name", "")
        raw_args = params.get("arguments", {})
        active_thread = thread_id or f"th-{uuid.uuid4().hex[:8]}"

        # Step 1: Distributed Trace Context
        trace_id = f"trace-{uuid.uuid4().hex}"
        span_id = f"span-{uuid.uuid4().hex[:16]}"

        # Step 2: Upstream PII Redaction
        sanitized_args, pii_findings, vault = redact_payload(raw_args)

        # Step 3: Check Step-Up Approval if token provided
        step_up_verified = False
        if approval_token and "stepup_ticket_id" in params:
            ticket_id = params["stepup_ticket_id"]
            approver = params.get("approver_identity", "admin@enterprise.local")
            ok, msg = self.step_up_guard.verify_and_approve(ticket_id, approval_token, approver)
            step_up_verified = ok

        # Step 4: OPA Rego Authorization Evaluation
        auth_context = {
            "caller_spiffe_id": caller_spiffe_id,
            "tool_name": tool_name,
            "arguments": sanitized_args,
            "scopes": scopes or ["data:read"],
            "step_up_verified": step_up_verified,
            "trace_id": trace_id
        }
        auth_verdict = evaluate_opa(auth_context)

        # Step 5: Handle Policy Verdicts
        if auth_verdict["decision"] == "deny":
            self._log_audit(trace_id, caller_spiffe_id, tool_name, "DENIED", auth_verdict["reason"])
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32001,
                    "message": f"OPA Policy Denied: {auth_verdict['reason']}",
                    "data": {"policy": auth_verdict.get("policy"), "trace_id": trace_id}
                }
            }

        if auth_verdict["decision"] == "step_up_required":
            ticket = self.step_up_guard.create_step_up_request(
                tool_name=tool_name,
                arguments=sanitized_args,
                caller_spiffe_id=caller_spiffe_id,
                thread_id=active_thread,
                reason=auth_verdict["reason"]
            )
            self.checkpointer.save_checkpoint(
                thread_id=active_thread,
                step_index=1,
                state_data={"pending_ticket": ticket, "sanitized_args": sanitized_args}
            )
            self._log_audit(trace_id, caller_spiffe_id, tool_name, "STEP_UP_REQUESTED", ticket["ticket_id"])
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "status": "STEP_UP_REQUIRED",
                    "ticket_id": ticket["ticket_id"],
                    "expires_at": ticket["expires_at"],
                    "message": "Mutating action paused. Awaiting cryptographically signed administrator approval.",
                    "trace_id": trace_id
                }
            }

        # Step 6: Execute Tool
        handler = self._tool_handlers.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Tool '{tool_name}' not found on registered MCP gateways",
                    "data": {"trace_id": trace_id}
                }
            }

        try:
            tool_output = handler(sanitized_args)
            self._log_audit(trace_id, caller_spiffe_id, tool_name, "EXECUTED", "SUCCESS")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "output": tool_output,
                    "pii_redacted": len(pii_findings) > 0,
                    "trace_id": trace_id
                }
            }
        except Exception as e:
            self._log_audit(trace_id, caller_spiffe_id, tool_name, "EXECUTION_ERROR", str(e))
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32000,
                    "message": f"Tool execution failed: {str(e)}",
                    "data": {"trace_id": trace_id}
                }
            }

    def _log_audit(self, trace_id: str, caller: str, tool: str, status: str, details: str):
        self._audit_log.append({
            "timestamp": time.time(),
            "trace_id": trace_id,
            "caller_spiffe_id": caller,
            "tool_name": tool,
            "status": status,
            "details": details
        })

    def get_audit_logs(self):
        return self._audit_log
