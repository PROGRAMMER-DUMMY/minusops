"""
Comprehensive test suite for Phase 1 MCP Gateway Control Plane.
Tests:
1. PII / PHI Redaction Engine.
2. OPA Rego Policy Evaluation.
3. Human-in-the-Loop (HITL) Step-Up Guard.
4. Session Checkpointing & Memory.
5. End-to-End MCP Gateway Proxy JSON-RPC Pipeline.
"""
import pytest

from core.mcp_gateway.pii_redactor import redact_string, redact_payload, contains_pii
from core.mcp_gateway.opa_evaluator import evaluate_policy_in_memory
import io

from core.mcp_gateway import step_up_guard
from core.mcp_gateway.step_up_guard import StepUpGuard
from core.mcp_gateway.session_checkpointer import SessionCheckpointer
from core.mcp_gateway.gateway_proxy import MCPGatewayProxy


# ==============================================================================
# 1. PII / PHI Redaction Tests
# ==============================================================================

def test_a_bare_run_of_digits_is_not_an_ssn():
    r"""A nine-digit run matched the SSN pattern. `807698055` is a real aws_lakeformation_data_lake_
    settings id out of this repository's own state file, and the redactor replaced it with
    [REDACTED_SSN_...] -- destroying an infrastructure identifier in the reports and audit
    entries this gateway is meant to be safe to log."""
    assert redact_string("807698055")[0] == "807698055"
    assert redact_string("id=807698055")[0] == "id=807698055"


def test_a_bare_run_of_digits_is_not_a_phone_number():
    """Ten consecutive digits matched the phone pattern, so an epoch timestamp was redacted."""
    assert redact_string("1756598400")[0] == "1756598400"


def test_a_real_ssn_is_still_redacted():
    """The fix must not blind the redactor. A written SSN carries its separators."""
    redacted, found, _ = redact_string("patient ssn 123-45-6789")

    assert "123-45-6789" not in redacted
    assert any(f["type"] == "SSN" for f in found)


def test_a_real_phone_number_is_still_redacted():
    for written in ("555-123-4567", "(555) 123-4567", "+1 555-123-4567"):
        redacted, found, _ = redact_string(f"call {written} now")
        assert written not in redacted, written
        assert any(f["type"] == "PHONE" for f in found), written


def test_the_signing_key_is_not_a_literal_in_the_source(monkeypatch):
    """A hardcoded HMAC key in a public repository is a published key: anyone holding it can
    forge an approval token for a high-risk tool call, which is the exact control this guard
    exists to be."""
    monkeypatch.delenv("MINUS_STEP_UP_SECRET", raising=False)
    monkeypatch.setenv("MINUS_POLICY_MODE", "dev")
    source = io.open(step_up_guard.__file__, encoding="utf-8").read()

    assert "minusops-hitl-step-up-secret-key" not in source


def test_production_refuses_to_start_without_a_configured_secret(monkeypatch):
    """Falling back to a default in production would sign real approvals with a key the
    operator never chose. Refusing is the only honest option."""
    monkeypatch.delenv("MINUS_STEP_UP_SECRET", raising=False)
    monkeypatch.setenv("MINUS_POLICY_MODE", "production")

    with pytest.raises(step_up_guard.StepUpSecretMissing):
        step_up_guard.StepUpGuard()


def test_dev_gets_a_key_that_is_random_per_process(monkeypatch):
    """A fixed development key in a public repository is still a published key. Random per
    process means tokens do not survive a restart, which is correct for dev and forces
    production to configure one."""
    monkeypatch.delenv("MINUS_STEP_UP_SECRET", raising=False)
    monkeypatch.setenv("MINUS_POLICY_MODE", "dev")

    first = step_up_guard.StepUpGuard().secret_key
    second = step_up_guard.StepUpGuard().secret_key

    assert first and second and first != second


def test_a_configured_secret_is_used_verbatim(monkeypatch):
    monkeypatch.setenv("MINUS_STEP_UP_SECRET", "an-operator-chosen-key")
    monkeypatch.setenv("MINUS_POLICY_MODE", "production")

    assert step_up_guard.StepUpGuard().secret_key == b"an-operator-chosen-key"


def test_pii_redactor_masks_ssn_and_email():
    text = "Patient SSN is 123-45-6789 and doctor email is dr.smith@hospital.org"
    redacted, findings, vault = redact_string(text)

    assert "123-45-6789" not in redacted
    assert "dr.smith@hospital.org" not in redacted
    assert "[REDACTED_SSN_" in redacted
    assert "[REDACTED_EMAIL_" in redacted
    assert len(findings) == 2
    assert any(v == "123-45-6789" for v in vault.values())


def test_pii_redactor_handles_nested_payloads():
    payload = {
        "patient_name": "John Doe",
        "ssn": "987-65-4321",
        "contact": {
            "phone": "555-123-4567",
            "email": "john@domain.com"
        },
        "query": "SELECT * FROM omop_person WHERE ssn = '987-65-4321'"
    }
    redacted, findings, vault = redact_payload(payload)

    assert "987-65-4321" not in str(redacted)
    assert "555-123-4567" not in str(redacted)
    assert "john@domain.com" not in str(redacted)
    assert len(findings) >= 3
    assert contains_pii(payload) is True
    assert contains_pii(redacted) is False


# ==============================================================================
# 2. OPA Policy Evaluation Tests
# ==============================================================================

def test_opa_allows_read_only_tool_for_analyst_agent():
    context = {
        "caller_spiffe_id": "spiffe://enterprise.local/agents/analyst/01",
        "tool_name": "query_domain_ard",
        "arguments": {"table": "person", "limit": 100},
        "scopes": ["data:read"]
    }
    verdict = evaluate_policy_in_memory(context)
    assert verdict["decision"] == "allow"
    assert "allow_data_read" in verdict["policy"]


def test_opa_requires_step_up_for_mutating_tools():
    context = {
        "caller_spiffe_id": "spiffe://enterprise.local/agents/operator/01",
        "tool_name": "terraform_apply",
        "arguments": {"plan_hash": "fc5a84b8397a"},
        "step_up_verified": False
    }
    verdict = evaluate_policy_in_memory(context)
    assert verdict["decision"] == "step_up_required"
    assert verdict["risk_tier"] == "HIGH"


def test_opa_denies_untrusted_spiffe_id():
    context = {
        "caller_spiffe_id": "spiffe://rogue.untrusted.com/agent/x",
        "tool_name": "query_domain_ard",
        "arguments": {}
    }
    verdict = evaluate_policy_in_memory(context)
    assert verdict["decision"] == "deny"


# ==============================================================================
# 3. HITL Step-Up Guard Tests
# ==============================================================================

def test_step_up_guard_ticket_lifecycle():
    guard = StepUpGuard(ttl_seconds=300)
    ticket = guard.create_step_up_request(
        tool_name="modify_patient_record",
        arguments={"patient_id": "P-100", "status": "ACTIVE"},
        caller_spiffe_id="spiffe://enterprise.local/agents/operator/01",
        thread_id="th-999"
    )

    ticket_id = ticket["ticket_id"]
    assert ticket["status"] == "PENDING_APPROVAL"
    assert ticket["payload_hash"] is not None

    # Sign approval token
    approver = "ciso@enterprise.local"
    token = guard.generate_approval_token(ticket_id, approver)
    assert token.startswith(f"tok_{ticket_id}_")

    # Verify and approve
    ok, msg = guard.verify_and_approve(ticket_id, token, approver)
    assert ok is True
    assert guard.get_ticket(ticket_id)["status"] == "APPROVED"


def test_step_up_guard_rejects_tampered_token():
    guard = StepUpGuard(ttl_seconds=300)
    ticket = guard.create_step_up_request(
        tool_name="terraform_destroy",
        arguments={},
        caller_spiffe_id="spiffe://enterprise.local/agents/operator/01",
        thread_id="th-123"
    )
    ok, msg = guard.verify_and_approve(ticket["ticket_id"], "tok_invalid_fake_token", "admin")
    assert ok is False
    assert "Invalid cryptographic" in msg


# ==============================================================================
# 4. Session Checkpointing Tests
# ==============================================================================

def test_session_checkpointer_thread_and_long_term():
    store = SessionCheckpointer(default_ttl_seconds=60)
    store.save_checkpoint("thread-10", 1, {"query": "OMOP CDM check"})
    store.save_checkpoint("thread-10", 2, {"result": "500 records found"})

    latest = store.load_latest_checkpoint("thread-10")
    assert latest["step_index"] == 2
    assert latest["state"]["result"] == "500 records found"

    # Long-term memory
    store.store_long_term_fact("healthcare_domain", "cdm_version", "OMOP v5.4")
    val = store.retrieve_long_term_fact("healthcare_domain", "cdm_version")
    assert val == "OMOP v5.4"


# ==============================================================================
# 5. End-to-End MCP Gateway Proxy JSON-RPC Pipeline Tests
# ==============================================================================

def test_gateway_proxy_read_query_with_pii_sanitization():
    proxy = MCPGatewayProxy()
    
    # Mock tool handler
    def handle_ard_query(args):
        return {"rows": 10, "received_args": args}

    proxy.register_tool("query_domain_ard", handle_ard_query)

    req = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "tools/call",
        "params": {
            "name": "query_domain_ard",
            "arguments": {
                "filter_ssn": "111-22-3333",
                "table": "person"
            }
        }
    }

    resp = proxy.handle_jsonrpc_request(
        request=req,
        caller_spiffe_id="spiffe://enterprise.local/agents/analyst/01"
    )

    assert "result" in resp
    assert resp["result"]["pii_redacted"] is True
    # The handler received sanitized token, not raw SSN
    assert "111-22-3333" not in str(resp["result"]["output"]["received_args"])
    assert "[REDACTED_SSN_" in str(resp["result"]["output"]["received_args"]["filter_ssn"])
    assert len(proxy.get_audit_logs()) == 1


def test_gateway_proxy_mutating_tool_step_up_flow():
    proxy = MCPGatewayProxy()

    def handle_tf_apply(args):
        return "Terraform apply completed successfully"

    proxy.register_tool("terraform_apply", handle_tf_apply)

    req = {
        "jsonrpc": "2.0",
        "id": "req-2",
        "method": "tools/call",
        "params": {
            "name": "terraform_apply",
            "arguments": {"plan_hash": "fc5a84b8397a"}
        }
    }

    # First attempt -> Paused with STEP_UP_REQUIRED
    resp1 = proxy.handle_jsonrpc_request(
        request=req,
        caller_spiffe_id="spiffe://enterprise.local/agents/operator/01"
    )
    assert resp1["result"]["status"] == "STEP_UP_REQUIRED"
    ticket_id = resp1["result"]["ticket_id"]

    # Generate approval token
    approver = "lead_architect@enterprise.local"
    approval_token = proxy.step_up_guard.generate_approval_token(ticket_id, approver)

    # Second attempt with token -> Executed
    req["params"]["stepup_ticket_id"] = ticket_id
    req["params"]["approver_identity"] = approver

    resp2 = proxy.handle_jsonrpc_request(
        request=req,
        caller_spiffe_id="spiffe://enterprise.local/agents/operator/01",
        approval_token=approval_token
    )
    assert "result" in resp2
    assert resp2["result"]["output"] == "Terraform apply completed successfully"
