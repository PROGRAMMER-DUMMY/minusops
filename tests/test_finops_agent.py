"""
Real-behavior tests for finops_agent.py (2026-07-06, Item 4 follow-up): this module sits
directly in the live operator path -- natural-language FinOps queries and the Slack/Jira
notify actions all go through it -- but had zero dedicated test coverage. These exercise the
actual read-path commands (cost/anomalies/correlate) against a fake CloudProvider double, and
the actual approval-gated notify actions, rather than just importing the module.
"""
import json
import os

import pytest

import finops_agent


class FakeProvider:
    """Minimal CloudProvider double exposing exactly what finops_agent calls."""
    name = "aws"

    def __init__(self, cost_result=None, anomalies_result=(None, None), owner_map=None):
        self._cost_result = cost_result
        self._anomalies_result = anomalies_result
        self._owner_map = owner_map or {}

    def cost_by_service(self, months_back=6):
        return self._cost_result

    def anomalies(self, days_back=60):
        return self._anomalies_result

    def owner(self, resource_hint):
        return self._owner_map.get(resource_hint)


def test_cmd_cost_prints_spend_and_month_over_month(monkeypatch, capsys):
    provider = FakeProvider(cost_result={
        "ok": True,
        "months": [
            {"month": "2026-05", "total": 100.0, "by_service": {"Amazon S3": 60.0, "AWS Glue": 40.0}},
            {"month": "2026-06", "total": 150.0, "by_service": {"Amazon S3": 90.0, "AWS Glue": 60.0}},
        ],
    })
    monkeypatch.setattr(finops_agent, "get_provider", lambda: provider)
    assert finops_agent.cmd_cost() is True
    out = capsys.readouterr().out
    assert "AWS SPEND BY SERVICE" in out
    assert "Amazon S3" in out and "$90.00" in out
    # Month-over-month: 150 - 100 = +$50.00, +50.0%
    assert "+$50.00" in out
    assert "+50.0%" in out


def test_cmd_cost_reports_error_and_returns_false(monkeypatch, capsys):
    provider = FakeProvider(cost_result={"ok": False, "error": "no credentials"})
    monkeypatch.setattr(finops_agent, "get_provider", lambda: provider)
    assert finops_agent.cmd_cost() is False
    assert "no credentials" in capsys.readouterr().err


def test_cmd_anomalies_lists_real_anomalies(monkeypatch, capsys):
    provider = FakeProvider(anomalies_result=(
        [{"id": "a1", "date": "2026-06-15", "service": "Amazon EC2", "impact": 245.50}], None))
    monkeypatch.setattr(finops_agent, "get_provider", lambda: provider)
    assert finops_agent.cmd_anomalies() is True
    out = capsys.readouterr().out
    assert "a1" in out and "Amazon EC2" in out and "$245.50" in out


def test_cmd_anomalies_none_found_is_still_success(monkeypatch, capsys):
    provider = FakeProvider(anomalies_result=([], None))
    monkeypatch.setattr(finops_agent, "get_provider", lambda: provider)
    assert finops_agent.cmd_anomalies() is True
    assert "No anomalies detected" in capsys.readouterr().out


def test_cmd_anomalies_error_returns_false(monkeypatch, capsys):
    provider = FakeProvider(anomalies_result=(None, "throttled"))
    monkeypatch.setattr(finops_agent, "get_provider", lambda: provider)
    assert finops_agent.cmd_anomalies() is False
    assert "throttled" in capsys.readouterr().err


def test_cmd_correlate_refuses_on_non_aws_cloud(monkeypatch, capsys):
    """Correlate is documented AWS-only; a non-AWS active provider must refuse, not silently
    skip the CloudTrail step and print misleading success."""
    provider = FakeProvider()
    provider.name = "azure"
    monkeypatch.setattr(finops_agent, "get_provider", lambda: provider)
    assert finops_agent.cmd_correlate() is False
    assert "AWS-only" in capsys.readouterr().out


def test_cmd_correlate_finds_owner_and_mutating_events(monkeypatch, capsys):
    provider = FakeProvider(
        anomalies_result=([{"id": "a1", "date": "2026-06-15", "service": "Amazon EC2", "impact": 99.0}], None),
        owner_map={"Amazon EC2": "data-platform-team"},
    )
    monkeypatch.setattr(finops_agent, "get_provider", lambda: provider)

    def fake_run_aws(args):
        return True, {"Events": [
            {"EventTime": "2026-06-15T01:00:00Z", "EventName": "RunInstances", "Username": "alice"},
            {"EventTime": "2026-06-15T01:05:00Z", "EventName": "DescribeInstances", "Username": "bob"},
        ]}, ""
    import providers.aws
    monkeypatch.setattr(providers.aws, "run_aws", fake_run_aws)

    assert finops_agent.cmd_correlate() is True
    out = capsys.readouterr().out
    assert "RunInstances" in out and "alice" in out
    assert "DescribeInstances" not in out  # not a mutating-event prefix (Create/Run/Modify/Start)
    assert "data-platform-team" in out


def test_notify_slack_denied_returns_false_and_sends_nothing(monkeypatch, capsys):
    provider = FakeProvider(anomalies_result=(
        [{"id": "a1", "date": "2026-06-15", "service": "Amazon EC2", "impact": 10.0}], None))
    monkeypatch.setattr(finops_agent, "get_provider", lambda: provider)
    monkeypatch.setattr(finops_agent, "request_approval", lambda *a, **k: False)
    called = []
    monkeypatch.setattr(finops_agent.urllib.request, "urlopen", lambda *a, **k: called.append(1))
    assert finops_agent.cmd_notify_slack("gatekeeper") is False
    assert "Not authorised" in capsys.readouterr().out
    assert called == []  # never even attempted to send


def test_notify_slack_approved_without_webhook_prepares_but_does_not_send(monkeypatch, capsys):
    provider = FakeProvider(anomalies_result=(
        [{"id": "a1", "date": "2026-06-15", "service": "Amazon EC2", "impact": 10.0}], None))
    monkeypatch.setattr(finops_agent, "get_provider", lambda: provider)
    monkeypatch.setattr(finops_agent, "request_approval", lambda *a, **k: True)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert finops_agent.cmd_notify_slack("auto-approve") is True
    assert "not sent" in capsys.readouterr().out


def test_notify_jira_writes_real_ticket_payload(monkeypatch, tmp_path):
    provider = FakeProvider(anomalies_result=(
        [{"id": "a1", "date": "2026-06-15", "service": "Amazon EC2", "impact": 10.0}], None))
    monkeypatch.setattr(finops_agent, "get_provider", lambda: provider)
    monkeypatch.setattr(finops_agent, "request_approval", lambda *a, **k: True)
    monkeypatch.setattr(finops_agent, "LOG_DIR", str(tmp_path))

    assert finops_agent.cmd_notify_jira("auto-approve") is True
    path = tmp_path / "jira_ticket_a1.json"
    assert path.exists()
    ticket = json.loads(path.read_text(encoding="utf-8"))
    assert ticket["priority"] == "High"
    assert "Amazon EC2" in ticket["summary"]


def test_notify_jira_denied_writes_no_file(monkeypatch, tmp_path):
    provider = FakeProvider(anomalies_result=(
        [{"id": "a1", "date": "2026-06-15", "service": "Amazon EC2", "impact": 10.0}], None))
    monkeypatch.setattr(finops_agent, "get_provider", lambda: provider)
    monkeypatch.setattr(finops_agent, "request_approval", lambda *a, **k: False)
    monkeypatch.setattr(finops_agent, "LOG_DIR", str(tmp_path))

    assert finops_agent.cmd_notify_jira("gatekeeper") is False
    assert list(tmp_path.iterdir()) == []
