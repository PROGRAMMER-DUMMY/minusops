"""The apply release check: does a human approval of THIS plan exist?

Every test here is a refusal except two, and that ratio is the point. The check exists to say
no; the release path is the uninteresting one.

Fails closed on every ambiguity -- an unparseable record, an unreadable timestamp, an
unattributable approver. The one thing it must never do is release because it could not tell.

Depends on: core/governance/apply_broker.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import datetime
import json

import pytest

import apply_broker


HASH = "a" * 64
OTHER = "b" * 64


def _now():
    return datetime.datetime(2026, 8, 26, 12, 0, tzinfo=datetime.timezone.utc)


def _approval(**overrides):
    record = {
        "plan_hash": HASH,
        "approved_by": "alice",
        "approver": "alice",
        "approved_at": (_now() - datetime.timedelta(minutes=10)).isoformat(),
    }
    record.update(overrides)
    return record


def _verify(approval, **kwargs):
    kwargs.setdefault("now", _now())
    kwargs.setdefault("planner", "bob")
    return apply_broker.verify(HASH, approval, **kwargs)


# --- The one that matters -------------------------------------------------------------------

def test_an_approval_for_a_different_plan_does_not_release_this_one():
    """The case the module exists for. An approval is not a permission slip for whatever is
    on disk now."""
    decision = _verify(_approval(plan_hash=OTHER))
    assert decision["released"] is False
    assert decision["reason"] == "hash_mismatch"
    assert decision["approved_hash"] == OTHER


def test_no_approval_at_all_is_a_refusal_naming_the_plan():
    decision = _verify(None)
    assert decision["released"] is False
    assert decision["reason"] == "no_approval"
    assert HASH[:16] in decision["detail"]


def test_a_matching_fresh_approval_by_someone_else_releases():
    decision = _verify(_approval())
    assert decision["released"] is True
    assert decision["approver"] == "alice"


# --- Two people, or it is one person agreeing with themselves --------------------------------

def test_the_planner_cannot_approve_their_own_plan():
    """authz's "open" mode -- no approver allowlist configured -- lets one person do both,
    and the audit record then shows two fields carrying one name."""
    decision = _verify(_approval(approved_by="bob"), planner="bob")
    assert decision["released"] is False
    assert decision["reason"] == "self_approval"


def test_self_approval_is_caught_through_an_arn():
    """The same person spelled two ways: `arn:aws:sts::1:assumed-role/deploy/bob` and `bob`."""
    decision = _verify(_approval(approved_by="arn:aws:sts::1:assumed-role/deploy/bob"),
                       planner="bob")
    assert decision["reason"] == "self_approval"


def test_two_different_people_are_not_confused_by_the_arn_comparison():
    decision = _verify(_approval(approved_by="arn:aws:sts::1:assumed-role/deploy/alice"),
                       planner="bob")
    assert decision["released"] is True


def test_an_unattributable_approval_is_refused():
    """An approval nobody's name is on is not an approval."""
    decision = _verify(_approval(approved_by="", approver=""))
    assert decision["reason"] == "no_approver"


# --- Freshness ---------------------------------------------------------------------------------

def test_a_stale_approval_is_refused():
    """Not because approval expires as a matter of principle, but because an approval made
    against last week's account state was made against facts that may no longer hold."""
    old = (_now() - datetime.timedelta(hours=30)).isoformat()
    decision = _verify(_approval(approved_at=old))
    assert decision["released"] is False
    assert decision["reason"] == "approval_stale"
    assert decision["age_seconds"] > 24 * 3600


def test_the_age_limit_is_configurable():
    old = (_now() - datetime.timedelta(hours=30)).isoformat()
    assert _verify(_approval(approved_at=old),
                   max_age_seconds=48 * 3600)["released"] is True


def test_an_approval_timestamped_in_the_future_is_refused():
    """A clock is wrong or the record was written by hand. Either way, do not release."""
    future = (_now() + datetime.timedelta(hours=1)).isoformat()
    decision = _verify(_approval(approved_at=future))
    assert decision["reason"] == "approval_in_the_future"


@pytest.mark.parametrize("timestamp", [None, "", "yesterday", 12345, "2026-13-45"])
def test_an_unreadable_timestamp_is_refused_not_ignored(timestamp):
    """Skipping the age check when the time cannot be parsed would make a malformed record
    the easiest one to get past the gate."""
    decision = _verify(_approval(approved_at=timestamp))
    assert decision["released"] is False
    assert decision["reason"] == "no_approval_time"


# --- Fails closed --------------------------------------------------------------------------------

@pytest.mark.parametrize("approval", ["a string", 42, [], True])
def test_a_malformed_approval_record_is_refused(approval):
    assert _verify(approval)["reason"] == "malformed_approval"


def test_no_plan_hash_is_refused_rather_than_matching_anything():
    assert apply_broker.verify("", _approval())["reason"] == "no_plan_hash"


def test_verify_never_raises():
    """It runs immediately before an apply. An exception there is an outage, and an outage
    that gets worked around with --force is worse than a refusal."""
    for approval in (None, {}, {"plan_hash": None}, {"approved_at": object()},
                     {"plan_hash": HASH, "approved_by": None}):
        decision = apply_broker.verify(HASH, approval, planner="bob", now=_now())
        assert decision["released"] in (True, False)


# --- The decision is recorded ---------------------------------------------------------------------

def test_a_refusal_is_written_to_the_audit_chain(tmp_path):
    """The more interesting of the two entries: someone tried to apply something that was
    not approved."""
    import audit_chain

    path = str(tmp_path / "audit.jsonl")
    apply_broker.record(_verify(_approval(plan_hash=OTHER)), audit_path=path)

    entry = json.loads(open(path, encoding="utf-8").readline())
    assert entry["action"] == "apply-release"
    assert entry["status"] == apply_broker.REFUSED
    assert entry["reason"] == "hash_mismatch"
    ok, errors = audit_chain.verify(path)
    assert ok, errors


def test_a_release_is_written_to_the_audit_chain(tmp_path):
    path = str(tmp_path / "audit.jsonl")
    apply_broker.record(_verify(_approval()), audit_path=path)
    entry = json.loads(open(path, encoding="utf-8").readline())
    assert entry["status"] == apply_broker.RELEASED


def test_an_unwritable_audit_path_does_not_fail_the_release(tmp_path):
    """The same rule reconciler follows: work that already happened must not be reported as
    failed because the log was unwritable."""
    blocker = tmp_path / "occupied"
    blocker.write_text("not a directory", encoding="utf-8")
    assert apply_broker.record(_verify(_approval()),
                               audit_path=str(blocker / "audit.jsonl")) is None


# --- The local store is documented as the fallback, not the design ----------------------------------

def test_the_module_says_the_local_store_proves_little():
    """A reader must not take load_approval() as the intended production path: it reads from
    the same disk the agent works on."""
    source = open(apply_broker.__file__, encoding="utf-8").read()
    assert "fallback, not the design" in source
    assert "cannot write" in source


def test_the_module_explains_why_it_is_not_an_oidc_broker():
    """The obvious design is a service minting tokens that carry the plan digest. It cannot
    be built from AWS primitives, and a reader who does not know that will ask for it."""
    source = open(apply_broker.__file__, encoding="utf-8").read()
    assert "NOT AN OIDC TOKEN BROKER" in source.upper()
    assert "no CI provider issues a token carrying a digest" in source
