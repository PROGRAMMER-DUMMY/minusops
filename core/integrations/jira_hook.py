"""
Jira change-ticket creator: submits to Jira Cloud when wired, writes the payload to disk when not.

This is the single implementation of the ticket path that used to live inline in
`core/reporting/finops_agent.py::cmd_notify_jira`; that command now calls in here. The
prepare-to-disk fallback is preserved deliberately, not as a stub: an unconfigured Jira must
leave evidence that the ticket was authorised and what it said, so the operator can file it by
hand without re-running the analysis.

Jira Cloud REST v3 rejects a plain string description — it wants Atlassian Document Format —
so `_adf()` wraps the text. The v2 shape silently 400s.

Credentials come from JIRA_USER / JIRA_TOKEN (or a Secrets Manager ARN).

Depends on: core/integrations/base_hook.py
Shells out to: POST {JIRA_BASE_URL}/rest/api/3/issue when JIRA_BASE_URL and credentials are
    set; otherwise writes a JSON payload under the caller's log directory
Used by: core/reporting/finops_agent.py, tests/test_integrations.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base_hook  # noqa: E402

BASE_URL_ENV = "JIRA_BASE_URL"
USER_ENV = "JIRA_USER"
TOKEN_ENV = "JIRA_TOKEN"
LOG_DIR = os.path.join(os.getcwd(), ".agents", "logs")


def _adf(text):
    """Wrap plain text in Atlassian Document Format (required by Jira Cloud REST v3)."""
    return {"type": "doc", "version": 1,
            "content": [{"type": "paragraph",
                         "content": [{"type": "text", "text": text}]}]}


def build_ticket(project_key, summary, description, plan_hash=None, priority="High"):
    """Build the flat, human-readable ticket record that is written to disk when Jira is unwired."""
    ticket = {"project_key": project_key, "summary": summary, "description": description,
              "priority": priority}
    if plan_hash:
        ticket["plan_hash"] = plan_hash
    return ticket


def create_change_ticket(project_key, summary, description, plan_hash=None, priority="High",
                         out_dir=None, filename=None, approval_mode="gatekeeper",
                         action="create-jira-ticket", details=None, secret_arn=None,
                         timeout=base_hook.DEFAULT_TIMEOUT):
    """
    Open a change ticket. Returns a result dict.

    Submitted: {"ok": True, "sent": True, "issue_key": ...}. Not wired up: {"ok": True,
    "sent": False, "reason": "not_configured", "path": <written payload>}. Denied:
    {"ok": False, "status": 403, "reason": "not_authorized"} and nothing is written.
    """
    ticket = build_ticket(project_key, summary, description, plan_hash, priority)

    def _send():
        base = (os.environ.get(BASE_URL_ENV) or "").strip().rstrip("/")
        auth = base_hook.basic_auth_header(USER_ENV, TOKEN_ENV, secret_arn)
        if base and auth:
            payload = {"fields": {
                "project": {"key": project_key},
                "summary": summary,
                "description": _adf(description),
                "issuetype": {"name": os.environ.get("JIRA_ISSUE_TYPE", "Task")},
            }}
            res = base_hook.request_json(f"{base}/rest/api/3/issue", payload=payload,
                                         headers=dict(auth), method="POST", timeout=timeout)
            if res["ok"]:
                try:
                    res["issue_key"] = json.loads(res.get("body") or "{}").get("key")
                except json.JSONDecodeError:
                    res["issue_key"] = None
            return res

        target_dir = out_dir or LOG_DIR
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, filename or f"jira_ticket_{project_key}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(ticket, f, indent=2)
        except OSError as e:
            return {"ok": False, "status": 500, "error": f"could not write ticket payload: {e}"}
        result = base_hook.not_configured(f"{BASE_URL_ENV}/{USER_ENV}/{TOKEN_ENV}")
        result["path"] = path
        result["ticket"] = ticket
        return result

    return base_hook.gated(action, details or summary, approval_mode, _send)
