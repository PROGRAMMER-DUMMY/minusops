# MCP Gateway Layer Context (`core/mcp_gateway`)

The `core/mcp_gateway` package implements the enterprise Model Context Protocol (MCP) Control Plane for autonomous agents, enforcing upstream PII/PHI sanitization, Open Policy Agent (OPA) Rego authorization, Human-in-the-Loop (HITL) step-up approval guards, and dual-tier stateful session checkpointing.

---

## Directory Overview & File Map

| File | Purpose | Key Responsibilities |
| :--- | :--- | :--- |
| [`__init__.py`](./__init__.py) | Package initialization | Package docstring defining MCP Gateway control plane scope |
| [`pii_redactor.py`](./pii_redactor.py) | PII / PHI Redaction Engine | Upstream inline regex inspection & cryptographic tokenization (SSN, MRN, Credit Card, Email, Phone) |
| [`opa_evaluator.py`](./opa_evaluator.py) | OPA Policy Evaluator | Rego authorization checking SPIFFE identity, OAuth 2.1 scopes, and tool risk tiers |
| [`step_up_guard.py`](./step_up_guard.py) | HITL Step-Up Guard | Intercepts mutating actions, generates SHA256-bound approval tickets, verifies cryptographic signatures |
| [`session_checkpointer.py`](./session_checkpointer.py) | Stateful Session Memory | Dual-tier persistence (Short-term thread snapshots with TTL + Long-term episodic vector facts) |
| [`gateway_proxy.py`](./gateway_proxy.py) | Unified Gateway Proxy | Handles JSON-RPC 2.0 requests, W3C trace generation, OPA checks, step-up routing, and audit logging |

---

## Exhaustive File Specifications

### 1. [`pii_redactor.py`](./pii_redactor.py)
* **Exact Purpose:** Inspects tool arguments and text payloads to detect and mask sensitive PII/PHI before execution.
* **Key Functions:** `redact_string(text)`, `redact_payload(payload)`, `contains_pii(payload)`.
* **Inputs / Outputs:** Takes raw strings/dicts/lists, returns sanitized structures with cryptographic replacement tokens (`[REDACTED_SSN_xxxx]`) and token vault mappings.
* **Failure Modes:** Fails closed; unparseable strings are preserved while logging inspection events.

### 2. [`opa_evaluator.py`](./opa_evaluator.py)
* **Exact Purpose:** Enforces declarative Rego policies over incoming tool invocations.
* **Key Functions:** `evaluate(context, opa_bin=None)`, `evaluate_policy_in_memory(context)`.
* **Inputs / Outputs:** Inputs: caller SPIFFE ID, target tool name, arguments, scopes. Outputs: `{"decision": "allow" | "deny" | "step_up_required", "reason": "...", "policy": "..."}`.
* **Failure Modes:** Rejects untrusted or empty SPIFFE IDs by default (`default allow := false`).

### 3. [`step_up_guard.py`](./step_up_guard.py)
* **Exact Purpose:** Human-in-the-Loop guard for high-risk and infrastructure-mutating tool requests.
* **Key Classes & Methods:** `StepUpGuard`: `create_step_up_request()`, `generate_approval_token()`, `verify_and_approve()`.
* **Inputs / Outputs:** Emits tickets with cryptographic payload hashes and HMAC-SHA256 tokens; verifies expiration and signature before allowing execution.

### 4. [`session_checkpointer.py`](./session_checkpointer.py)
* **Exact Purpose:** Thread-level and cross-session memory management for multi-agent workflows.
* **Key Classes & Methods:** `SessionCheckpointer`: `save_checkpoint()`, `load_latest_checkpoint()`, `store_long_term_fact()`, `retrieve_long_term_fact()`.
* **Inputs / Outputs:** State snapshot dictionaries indexed by `thread_id` and `step_index` with TTL expiration.

### 5. [`gateway_proxy.py`](./gateway_proxy.py)
* **Exact Purpose:** The unified reverse proxy handling JSON-RPC 2.0 requests across Data, Workflow, and Support MCP tools.
* **Key Classes & Methods:** `MCPGatewayProxy`: `register_tool()`, `handle_jsonrpc_request()`, `_log_audit()`, `get_audit_logs()`.
* **Inputs / Outputs:** JSON-RPC 2.0 requests $	o$ JSON-RPC 2.0 responses containing outputs, trace IDs, and step-up tickets.
