"""
Enterprise Connector Configuration and Verification Engine.

Manages persistent connector configuration under .minus/connectors.json,
applies them to the live process environment, and executes live health
probes for Slack, Microsoft Teams, Jira Cloud, Confluence, and Outlook.
"""
import datetime
import json
import os
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)
for _sub in ("integrations", "architecture", "reporting", "governance"):
    _p = os.path.join(_CORE_DIR, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

WORKSPACE = os.getcwd()
CONFIG_PATH = os.path.join(WORKSPACE, ".minus", "connectors.json")
FEEDBACK_PATH = os.path.join(WORKSPACE, ".minus", "feedback.jsonl")


def load_connector_configs():
    """Load stored connector settings from .minus/connectors.json with env fallbacks."""
    stored = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                stored = json.load(f)
        except Exception:
            stored = {}

    configs = {
        "slack": {
            "name": "Slack",
            "channel": stored.get("slack", {}).get("channel") or "#data-platform-alerts",
            "endpoint_ref": stored.get("slack", {}).get("endpoint_ref") or os.environ.get("SLACK_WEBHOOK_URL", ""),
            "secret_arn": stored.get("slack", {}).get("secret_arn") or os.environ.get("SLACK_SECRET_ARN", ""),
            "configured": bool(stored.get("slack", {}).get("endpoint_ref") or os.environ.get("SLACK_WEBHOOK_URL") or os.environ.get("SLACK_SECRET_ARN")),
        },
        "teams": {
            "name": "Microsoft Teams",
            "channel": stored.get("teams", {}).get("channel") or "Data Engineering",
            "endpoint_ref": stored.get("teams", {}).get("endpoint_ref") or os.environ.get("TEAMS_WEBHOOK_URL", ""),
            "secret_arn": stored.get("teams", {}).get("secret_arn") or os.environ.get("TEAMS_SECRET_ARN", ""),
            "configured": bool(stored.get("teams", {}).get("endpoint_ref") or os.environ.get("TEAMS_WEBHOOK_URL") or os.environ.get("TEAMS_SECRET_ARN")),
        },
        "jira": {
            "name": "Jira Cloud",
            "base_url": stored.get("jira", {}).get("base_url") or os.environ.get("JIRA_BASE_URL", ""),
            "project_key": stored.get("jira", {}).get("project_key") or "DATA",
            "user_email": stored.get("jira", {}).get("user_email") or os.environ.get("JIRA_USER", ""),
            "token_ref": stored.get("jira", {}).get("token_ref") or ("********" if os.environ.get("JIRA_TOKEN") else ""),
            "secret_arn": stored.get("jira", {}).get("secret_arn") or os.environ.get("JIRA_SECRET_ARN", ""),
            "configured": bool((stored.get("jira", {}).get("base_url") or os.environ.get("JIRA_BASE_URL")) and (stored.get("jira", {}).get("token_ref") or os.environ.get("JIRA_TOKEN") or os.environ.get("JIRA_SECRET_ARN"))),
        },
        "confluence": {
            "name": "Confluence",
            "base_url": stored.get("confluence", {}).get("base_url") or os.environ.get("CONFLUENCE_BASE_URL", ""),
            "space_key": stored.get("confluence", {}).get("space_key") or "ARCH",
            "user_email": stored.get("confluence", {}).get("user_email") or os.environ.get("CONFLUENCE_USER", ""),
            "token_ref": stored.get("confluence", {}).get("token_ref") or ("********" if os.environ.get("CONFLUENCE_API_TOKEN") else ""),
            "secret_arn": stored.get("confluence", {}).get("secret_arn") or os.environ.get("CONFLUENCE_SECRET_ARN", ""),
            "configured": bool((stored.get("confluence", {}).get("base_url") or os.environ.get("CONFLUENCE_BASE_URL")) and (stored.get("confluence", {}).get("token_ref") or os.environ.get("CONFLUENCE_API_TOKEN") or os.environ.get("CONFLUENCE_SECRET_ARN"))),
        },
        "outlook": {
            "name": "Outlook / O365",
            "distribution_list": stored.get("outlook", {}).get("distribution_list") or os.environ.get("SMTP_FROM", "finops-reports@example.com"),
            "endpoint_ref": stored.get("outlook", {}).get("endpoint_ref") or os.environ.get("SMTP_HOST", ""),
            "secret_arn": stored.get("outlook", {}).get("secret_arn") or os.environ.get("SMTP_SECRET_ARN", ""),
            "configured": bool(stored.get("outlook", {}).get("endpoint_ref") or os.environ.get("SMTP_HOST") or os.environ.get("SMTP_SECRET_ARN")),
        },
    }
    return configs


def save_connector_config(connector_key, values):
    """Persist connector settings to .minus/connectors.json and apply to environment."""
    stored = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                stored = json.load(f)
        except Exception:
            stored = {}

    stored[connector_key] = values
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(stored, f, indent=2)

    # Apply to current process environment
    if connector_key == "slack" and values.get("endpoint_ref"):
        os.environ["SLACK_WEBHOOK_URL"] = values["endpoint_ref"]
    elif connector_key == "teams" and values.get("endpoint_ref"):
        os.environ["TEAMS_WEBHOOK_URL"] = values["endpoint_ref"]
    elif connector_key == "jira":
        if values.get("base_url"):
            os.environ["JIRA_BASE_URL"] = values["base_url"]
        if values.get("user_email"):
            os.environ["JIRA_USER"] = values["user_email"]
        if values.get("token_ref") and values["token_ref"] != "********":
            os.environ["JIRA_TOKEN"] = values["token_ref"]
    elif connector_key == "confluence":
        if values.get("base_url"):
            os.environ["CONFLUENCE_BASE_URL"] = values["base_url"]
        if values.get("user_email"):
            os.environ["CONFLUENCE_USER"] = values["user_email"]
        if values.get("token_ref") and values["token_ref"] != "********":
            os.environ["CONFLUENCE_API_TOKEN"] = values["token_ref"]
    elif connector_key == "outlook":
        if values.get("endpoint_ref"):
            os.environ["SMTP_HOST"] = values["endpoint_ref"]
        if values.get("distribution_list"):
            os.environ["SMTP_FROM"] = values["distribution_list"]

    return True


def _unsent(res, unconfigured_detail):
    """Why a send did not happen, told apart rather than collapsed.

    Every branch read `if res.get("sent")` and reported everything else as NOT_CONFIGURED, so
    an HTTP 401, 403 or 500 was shown to the operator as "endpoint is unconfigured" -- a
    false statement about a destination that is configured and rejected them. base_hook's own
    contract distinguishes the cases and this threw that away: `not_configured` carries
    ok=True with reason="not_configured", a delivery failure does not.
    """
    res = res or {}
    reason = res.get("reason")
    if reason == "not_configured":
        return {"ok": False, "status": "NOT_CONFIGURED", "detail": unconfigured_detail}
    if reason == "not_authorized":
        return {"ok": False, "status": "NOT_AUTHORIZED",
                "detail": "The send was not authorised by the approval gate."}
    status = res.get("status") or 0
    detail = res.get("error") or "the endpoint did not accept the message"
    if status in (401, 403):
        return {"ok": False, "status": "AUTH_FAILED",
                "detail": f"The endpoint rejected the credentials (HTTP {status}): {detail}"}
    return {"ok": False, "status": "CONNECTION_ERROR",
            "detail": f"Delivery failed (HTTP {status}): {detail}"}


def test_connector(connector_key):
    """Execute a governed test ping to verify connectivity."""
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S UTC")
    try:
        if connector_key == "slack":
            import slack_hook
            res = slack_hook.send_slack_notification(
                {"text": f"[MinusOps Verification] Live connection test from Governance Console at {now_str}."},
                approval_mode="auto-approve",
                action="test-slack-ping"
            )
            if res.get("sent"):
                return {"ok": True, "status": "CONNECTED", "detail": f"Message delivered to Slack (HTTP {res.get('status', 200)}) at {now_str}"}
            return _unsent(res, "Slack endpoint is unconfigured. Set the endpoint reference above.")

        elif connector_key == "teams":
            import teams_hook
            res = teams_hook.send_teams_card(
                "MinusOps Console Verification",
                [("Status", "Verified"), ("Timestamp", now_str), ("Source", "Governance Console")],
                approval_mode="auto-approve",
                action="test-teams-ping"
            )
            if res.get("sent"):
                return {"ok": True, "status": "CONNECTED", "detail": f"Adaptive Card delivered to Teams at {now_str}"}
            return _unsent(res, "Teams endpoint is unconfigured. Set the endpoint reference above.")

        elif connector_key == "jira":
            import jira_hook
            res = jira_hook.create_change_ticket(
                "DATA", "MinusOps Connection Test",
                f"Test change ticket from MinusOps Governance Console at {now_str}.",
                approval_mode="auto-approve",
                action="test-jira-ping"
            )
            if res.get("sent"):
                return {"ok": True, "status": "CONNECTED", "detail": f"Jira Issue created: {res.get('issue_key')} at {now_str}"}
            return _unsent(res, "Jira credentials unconfigured. Set Base URL, User and API token.")

        elif connector_key == "confluence":
            import confluence_hook
            res = confluence_hook.publish_confluence_page(
                "ARCH", "MinusOps Connection Test",
                f"### MinusOps Connection Test\n\nVerified at {now_str}.",
                approval_mode="auto-approve",
                action="test-confluence-ping"
            )
            if res.get("sent"):
                return {"ok": True, "status": "CONNECTED", "detail": f"Page created at {now_str}"}
            return _unsent(res, "Confluence credentials unconfigured.")

        elif connector_key == "outlook":
            import outlook_hook
            res = outlook_hook.send_executive_email(
                ["finops@example.com"], "MinusOps FinOps Connection Test",
                f"<h3>MinusOps FinOps Connection Test</h3><p>Verified at {now_str}.</p>",
                approval_mode="auto-approve",
                action="test-outlook-ping"
            )
            if res.get("sent"):
                return {"ok": True, "status": "CONNECTED", "detail": f"Email report delivered at {now_str}"}
            return _unsent(res, "Outlook SMTP unconfigured. Set SMTP_HOST and SMTP_FROM.")

        return {"ok": False, "status": "UNKNOWN", "detail": f"Unknown connector: {connector_key}"}

    except Exception as exc:
        return {"ok": False, "status": "ERROR", "detail": f"Connection test failed: {str(exc)}"}
