"""
Slack incoming-webhook dispatcher and Block Kit formatter.

This is the single implementation of the Slack send that used to live inline in
`core/reporting/finops_agent.py::cmd_notify_slack`; that command now calls in here. Behaviour
is preserved exactly: approval first, then read `SLACK_WEBHOOK_URL`, and when the webhook is
unset treat the run as a success that sent nothing (the payload was prepared, the operator
approved, nothing was misconfigured on our side).

The webhook URL is itself a bearer credential — anyone holding it can post as the app — so it
is resolved from the environment or a Secrets Manager ARN rather than accepted as a parameter.
That is the one deliberate deviation from the plan's `send_slack_notification(webhook_url, ...)`
signature.

Depends on: core/integrations/base_hook.py
Shells out to: SLACK_WEBHOOK_URL (an HTTPS POST to Slack)
Used by: core/reporting/finops_agent.py, tests/test_integrations.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base_hook  # noqa: E402

WEBHOOK_ENV = "SLACK_WEBHOOK_URL"


def build_blocks(text, plan_hash=None, approve_url=None, reject_url=None):
    """
    Build a Block Kit payload: a markdown section, an optional plan-hash context line, and
    Approve/Reject actions.

    The buttons carry `value=<plan_hash>` so an interaction handler can verify that the plan
    being approved is the plan that was shown. An approval that cannot name its plan hash is
    the trap described in the friction ledger (section 3.1) — hence the context line is only
    omitted when there is no hash at all.
    """
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    if plan_hash:
        blocks.append({"type": "context",
                       "elements": [{"type": "mrkdwn", "text": f"*Plan hash:* `{plan_hash}`"}]})
    actions = [
        {"type": "button", "text": {"type": "plain_text", "text": "Approve"},
         "style": "primary", "action_id": "minusops_approve", "value": plan_hash or ""},
        {"type": "button", "text": {"type": "plain_text", "text": "Reject"},
         "style": "danger", "action_id": "minusops_reject", "value": plan_hash or ""},
    ]
    if approve_url:
        actions[0]["url"] = approve_url
    if reject_url:
        actions[1]["url"] = reject_url
    blocks.append({"type": "actions", "elements": actions})
    return blocks


def send_slack_notification(payload, interactive=False, plan_hash=None,
                            approval_mode="gatekeeper", action="send-slack-alert",
                            details=None, secret_arn=None, timeout=base_hook.DEFAULT_TIMEOUT):
    """
    Post `payload` (a Slack message dict, minimally {"text": ...}) to the configured webhook.

    With interactive=True the text is also rendered as Block Kit with Approve/Reject buttons;
    `text` stays in the payload as the notification fallback Slack requires.

    Returns a result dict; `sent` is False both when approval was denied (reason
    "not_authorized") and when no webhook is configured (reason "not_configured").
    """
    payload = dict(payload or {})
    text = payload.get("text", "")
    if interactive:
        payload.setdefault("blocks", build_blocks(text, plan_hash=plan_hash))

    def _send():
        webhook = base_hook.resolve_secret(WEBHOOK_ENV, secret_arn)
        if not webhook:
            return base_hook.not_configured(WEBHOOK_ENV)
        return base_hook.post_json(webhook, payload, timeout=timeout)

    return base_hook.gated(action, details or text, approval_mode, _send)
