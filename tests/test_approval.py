"""
The approval gate guards every side effect. Two invariants matter most:
  * gatekeeper mode is FAIL-CLOSED with no interactive terminal (denies, never proceeds);
  * auto-approve proceeds but is always audited.
"""
import json
import os

import pytest

import approval


@pytest.fixture
def audit_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(approval, "LOG_DIR", str(tmp_path))
    return tmp_path


def _audit_lines(tmp_path):
    f = tmp_path / "audit.jsonl"
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_gatekeeper_fails_closed_without_tty(audit_to_tmp, monkeypatch):
    class _NoTTY:
        def isatty(self):
            return False
    monkeypatch.setattr(approval.sys, "stdin", _NoTTY())

    assert approval.request_approval("send-slack", "notify channel", mode="gatekeeper") is False
    decisions = [r["decision"] for r in _audit_lines(audit_to_tmp)]
    assert decisions == ["DENIED_NO_TTY"]


def test_auto_approve_proceeds_and_audits(audit_to_tmp):
    assert approval.request_approval("scheduled-report", "nightly read-only", mode="auto-approve") is True
    rows = _audit_lines(audit_to_tmp)
    assert rows and rows[-1]["decision"] == "AUTO_APPROVED"
    assert rows[-1]["approval_mode"] == "auto-approve"


def test_unknown_mode_defaults_to_gatekeeper(audit_to_tmp, monkeypatch):
    class _NoTTY:
        def isatty(self):
            return False
    monkeypatch.setattr(approval.sys, "stdin", _NoTTY())
    # An unrecognised mode must not accidentally behave like auto-approve.
    assert approval.request_approval("danger", "do thing", mode="bogus-mode") is False
