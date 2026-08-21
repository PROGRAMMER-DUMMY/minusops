"""
Alert-storm suppression in the notification gate (TASK-TDD-2026-002 WP2).

A pipeline that fails 50 times in ten seconds should page someone once. Without a cooldown
the on-call channel fills with identical messages, the human mutes it, and the next distinct
alert is the one nobody sees -- the failure mode is not the noise, it is the muting.

Isolation: each test uses its own action string, so the module-level window is naturally
partitioned and no reset hook has to exist in production code for the tests' benefit.

Depends on: core/integrations/base_hook.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import base_hook


def _sender(calls, result=None):
    """A real callable recording invocations -- not a mock. The assertions below are about
    whether the sender RAN, which is the actual behaviour under test."""
    def send():
        calls.append(1)
        return dict(result or {"ok": True, "status": 200})
    return send


def test_first_alert_dispatches_successfully():
    calls = []
    result = base_hook.gated("dedup-first", "glue job failed", "auto-approve", _sender(calls))
    assert result["sent"] is True
    assert len(calls) == 1, "the first alert must actually reach the sender"


def test_identical_alert_within_5_minutes_is_suppressed():
    calls = []
    base_hook.gated("dedup-repeat", "glue job failed", "auto-approve", _sender(calls))
    result = base_hook.gated("dedup-repeat", "glue job failed", "auto-approve", _sender(calls))

    assert result["sent"] is False
    assert result["reason"] == "deduplicated"
    assert result["ok"] is True, (
        "suppression is not a failure -- ok must stay True so a caller does not treat a "
        "working cooldown as a broken integration"
    )
    assert len(calls) == 1, "the duplicate must never reach the sender"


def test_different_alert_payload_dispatches_immediately():
    calls = []
    base_hook.gated("dedup-distinct", "bronze ingest failed", "auto-approve", _sender(calls))
    result = base_hook.gated("dedup-distinct", "silver transform failed", "auto-approve",
                             _sender(calls))

    assert result["sent"] is True
    assert len(calls) == 2, "a different message is a different incident"


def test_alert_after_window_expires_dispatches_again():
    calls = []
    clock = [1000.0]
    original = base_hook._now
    base_hook._now = lambda: clock[0]
    try:
        base_hook.gated("dedup-window", "disk full", "auto-approve", _sender(calls))
        clock[0] += base_hook.DEDUP_WINDOW_SECONDS + 1
        result = base_hook.gated("dedup-window", "disk full", "auto-approve", _sender(calls))
    finally:
        base_hook._now = original

    assert result["sent"] is True, "a fault still firing after the cooldown must page again"
    assert len(calls) == 2


def test_default_window_is_five_minutes():
    assert base_hook.DEDUP_WINDOW_SECONDS == 300


def test_suppressed_alert_never_asks_for_approval():
    """The cooldown exists so a human is not prompted 50 times. Checking it after the
    approval prompt would defeat the purpose."""
    prompts = []
    original = base_hook.request_approval
    base_hook.request_approval = lambda *a, **k: (prompts.append(1), True)[1]
    try:
        base_hook.gated("dedup-no-prompt", "same fault", "gatekeeper", _sender([]))
        base_hook.gated("dedup-no-prompt", "same fault", "gatekeeper", _sender([]))
    finally:
        base_hook.request_approval = original

    assert len(prompts) == 1, "the suppressed duplicate must not reach the approval gate"


def test_denied_approval_is_not_recorded_as_a_sent_alert():
    """A denial must not start a cooldown -- otherwise denying once silently suppresses the
    next five minutes of real alerts."""
    calls = []
    original = base_hook.request_approval
    base_hook.request_approval = lambda *a, **k: False
    try:
        first = base_hook.gated("dedup-denied", "fault", "gatekeeper", _sender(calls))
    finally:
        base_hook.request_approval = original
    assert first["sent"] is False and first["reason"] == "not_authorized"

    second = base_hook.gated("dedup-denied", "fault", "auto-approve", _sender(calls))
    assert second["sent"] is True, "the denial must not have opened a cooldown window"
    assert len(calls) == 1
