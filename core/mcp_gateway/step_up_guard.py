"""
step_up_guard.py -- Human-in-the-Loop (HITL) Step-Up Approval Guard.

Intercepts high-risk/mutating tool invocations, freezes execution state in RedisSaver,
issues cryptographically signed approval tokens, and dispatches approval notifications
via outbound integration hooks.
"""
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from typing import Any, Dict, Optional, Tuple

DEFAULT_TTL_SECONDS = 900  # 15 minutes


class StepUpSecretMissing(RuntimeError):
    """Raised when production mode has no configured signing key."""


def resolve_secret_key():
    """The HMAC key that signs approval tokens, from MINUS_STEP_UP_SECRET.

    This was a literal in the source. A hardcoded key in a public repository is a published
    key: anyone holding it can forge an approval token for exactly the high-risk tool calls
    this guard exists to gate.

    With no key configured, production REFUSES rather than falling back -- signing real
    approvals with a key the operator never chose is worse than not starting. Development
    gets a key generated per process, not a second literal: tokens then do not survive a
    restart, which is correct for dev and is what forces production to configure one.
    """
    configured = os.environ.get("MINUS_STEP_UP_SECRET")
    if configured:
        return configured.encode("utf-8")
    if (os.environ.get("MINUS_POLICY_MODE") or "dev").strip().lower() == "production":
        raise StepUpSecretMissing(
            "MINUS_STEP_UP_SECRET is not set and MINUS_POLICY_MODE=production. Approval "
            "tokens cannot be signed with a key the operator did not choose.")
    return secrets.token_bytes(32)


class StepUpGuard:
    def __init__(self, secret_key: Optional[bytes] = None,
                 ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.secret_key = secret_key or resolve_secret_key()
        self.ttl_seconds = ttl_seconds
        self._pending_tickets: Dict[str, Dict[str, Any]] = {}

    def create_step_up_request(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        caller_spiffe_id: str,
        thread_id: str,
        reason: str = "High-risk mutation"
    ) -> Dict[str, Any]:
        """
        Create a pending step-up approval ticket with a cryptographic payload hash.
        """
        ticket_id = f"stepup-{uuid.uuid4().hex[:12]}"
        now = time.time()
        expires_at = now + self.ttl_seconds

        payload_bytes = json.dumps({
            "tool_name": tool_name,
            "arguments": arguments,
            "caller_spiffe_id": caller_spiffe_id,
            "thread_id": thread_id
        }, sort_keys=True).encode("utf-8")

        payload_hash = hashlib.sha256(payload_bytes).hexdigest()

        ticket = {
            "ticket_id": ticket_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "caller_spiffe_id": caller_spiffe_id,
            "thread_id": thread_id,
            "payload_hash": payload_hash,
            "reason": reason,
            "created_at": now,
            "expires_at": expires_at,
            "status": "PENDING_APPROVAL",
            "approver": None,
            "approval_token": None
        }

        self._pending_tickets[ticket_id] = ticket
        return ticket

    def generate_approval_token(self, ticket_id: str, approver_identity: str) -> str:
        """
        Sign an approval token for a specific ticket and approver.
        """
        ticket = self._pending_tickets.get(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")

        sig_data = f"{ticket_id}:{ticket['payload_hash']}:{approver_identity}:{ticket['expires_at']}"
        signature = hmac.new(self.secret_key, sig_data.encode("utf-8"), hashlib.sha256).hexdigest()
        token = f"tok_{ticket_id}_{signature[:16]}"
        return token

    def verify_and_approve(
        self,
        ticket_id: str,
        approval_token: str,
        approver_identity: str
    ) -> Tuple[bool, str]:
        """
        Verify the approval token and mark the ticket as APPROVED.
        """
        ticket = self._pending_tickets.get(ticket_id)
        if not ticket:
            return False, "Ticket not found"

        if time.time() > ticket["expires_at"]:
            ticket["status"] = "EXPIRED"
            return False, "Approval ticket has expired"

        expected_token = self.generate_approval_token(ticket_id, approver_identity)
        if not hmac.compare_digest(approval_token, expected_token):
            return False, "Invalid cryptographic approval token"

        ticket["status"] = "APPROVED"
        ticket["approver"] = approver_identity
        ticket["approval_token"] = approval_token
        return True, "Ticket approved successfully"

    def get_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        return self._pending_tickets.get(ticket_id)
