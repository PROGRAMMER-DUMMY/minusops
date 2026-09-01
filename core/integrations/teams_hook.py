"""
Microsoft Teams Adaptive Card dispatcher for data-quality and quarantine alerts.

Teams rejects a bare {"text": ...} on a Workflows/Power Automate endpoint, so the card is
wrapped in the `attachments` envelope with contentType
`application/vnd.microsoft.card.adaptive` — sending the card object on its own returns 202 and
displays nothing, which reads as a delivered alert that nobody ever saw.

The webhook URL is a bearer credential and is resolved from `TEAMS_WEBHOOK_URL` or a Secrets
Manager ARN rather than accepted as a parameter (deviation from the plan's
`send_teams_card(webhook_url, ...)` signature, same reason as the Slack hook).

Depends on: core/integrations/base_hook.py
Shells out to: TEAMS_WEBHOOK_URL (an HTTPS POST to Microsoft Teams)
Used by: tests/test_integrations.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base_hook  # noqa: E402

WEBHOOK_ENV = "TEAMS_WEBHOOK_URL"
_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"


def build_adaptive_card(title, facts_list, action_url=None, action_title="Open in MinusOps"):
    """
    Build the Teams `attachments` envelope for an Adaptive Card.

    `facts_list` is a sequence of (name, value) pairs or {"title"/"name", "value"} dicts;
    values are stringified because a FactSet renders a non-string value as blank.
    """
    facts = []
    for item in facts_list or []:
        if isinstance(item, dict):
            name = item.get("title") or item.get("name") or ""
            value = item.get("value", "")
        else:
            name, value = item
        facts.append({"title": str(name), "value": str(value)})

    body = [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium",
             "wrap": True}]
    if facts:
        body.append({"type": "FactSet", "facts": facts})
    card = {"type": "AdaptiveCard", "$schema": _SCHEMA, "version": "1.4", "body": body}
    if action_url:
        card["actions"] = [{"type": "Action.OpenUrl", "title": action_title, "url": action_url}]
    return {"type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive",
                             "contentUrl": None, "content": card}]}


def send_teams_card(title, facts_list, action_url=None, approval_mode="gatekeeper",
                    action="send-teams-card", details=None, secret_arn=None,
                    timeout=base_hook.DEFAULT_TIMEOUT):
    """
    Post an Adaptive Card to the configured Teams webhook.

    Returns a result dict; `sent` is False when approval was denied or when
    TEAMS_WEBHOOK_URL is unconfigured.
    """
    payload = build_adaptive_card(title, facts_list, action_url=action_url)

    def _send():
        webhook = base_hook.resolve_secret(WEBHOOK_ENV, secret_arn)
        if not webhook:
            return base_hook.not_configured(WEBHOOK_ENV)
        return base_hook.post_json(webhook, payload, timeout=timeout)

    return base_hook.gated(action, details or title, approval_mode, _send)
