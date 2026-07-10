"""
Approver authorization (RBAC). Open mode when no allowlist; enforced when configured.
"""
import json

import approval
import authz


def test_open_mode_when_no_allowlist(monkeypatch):
    monkeypatch.delenv(authz.APPROVERS_ENV, raising=False)
    monkeypatch.delenv(authz.OPERATOR_ENV, raising=False)
    allowed, mode, _reason = authz.authorize(workspace=".")
    assert allowed is True
    assert mode == "open"


def test_enforced_mode_allows_listed_operator(monkeypatch):
    monkeypatch.setenv(authz.OPERATOR_ENV, "alice@corp")
    monkeypatch.setenv(authz.APPROVERS_ENV, "alice@corp, bob@corp")
    allowed, mode, _reason = authz.authorize(workspace=".")
    assert allowed is True
    assert mode == "enforced"


def test_enforced_mode_denies_unlisted_operator(monkeypatch):
    monkeypatch.setenv(authz.OPERATOR_ENV, "mallory@evil")
    monkeypatch.setenv(authz.APPROVERS_ENV, "alice@corp,bob@corp")
    allowed, mode, _reason = authz.authorize(workspace=".")
    assert allowed is False
    assert mode == "enforced"


def test_allowlist_can_come_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv(authz.APPROVERS_ENV, raising=False)
    minus = tmp_path / ".minus"
    minus.mkdir()
    (minus / "approvers.json").write_text(json.dumps({"approvers": ["carol@corp"]}), encoding="utf-8")
    assert authz.authorize("carol@corp", workspace=str(tmp_path))[0] is True
    assert authz.authorize("dave@corp", workspace=str(tmp_path))[0] is False


def test_approval_denied_for_unauthorized_operator(tmp_path, monkeypatch):
    monkeypatch.setattr(approval, "LOG_DIR", str(tmp_path))
    monkeypatch.setenv(authz.OPERATOR_ENV, "mallory@evil")
    monkeypatch.setenv(authz.APPROVERS_ENV, "alice@corp")

    # Even auto-approve must be refused when the operator is not an authorized approver.
    assert approval.request_approval("apply", "do thing", mode="auto-approve") is False
    rows = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["decision"] == "DENIED_NOT_AUTHORIZED"


# --- verified_operator() (2026-07-07, Phase 1 identity fix) -----------------------

def test_principal_from_arn_extracts_assumed_role_session_name():
    arn = "arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_MinusDeploy_abc123/alice@corp.com"
    assert authz._principal_from_arn(arn) == "alice@corp.com"


def test_principal_from_arn_extracts_iam_user():
    arn = "arn:aws:iam::123456789012:user/bob"
    assert authz._principal_from_arn(arn) == "bob"


def test_principal_from_arn_returns_none_for_root_and_garbage():
    assert authz._principal_from_arn("arn:aws:iam::123456789012:root") is None
    assert authz._principal_from_arn("not-an-arn") is None
    assert authz._principal_from_arn("") is None
    assert authz._principal_from_arn(None) is None


class _FakeProvider:
    def __init__(self, posture):
        self._posture = posture

    def credential_posture(self):
        return self._posture


def test_verified_operator_none_when_no_session(monkeypatch):
    import providers.base as pb
    monkeypatch.setattr(pb, "get_provider", lambda: _FakeProvider({"connected": False}))
    assert authz.verified_operator() is None


def test_verified_operator_returns_real_identity_when_connected(monkeypatch):
    import providers.base as pb
    arn = "arn:aws:sts::123456789012:assumed-role/MinusDeploy/carol@corp.com"
    monkeypatch.setattr(pb, "get_provider", lambda: _FakeProvider({"connected": True, "arn": arn}))
    assert authz.verified_operator() == "carol@corp.com"


def test_verified_operator_cannot_be_spoofed_by_env_var(monkeypatch):
    """The whole point of this function: setting MINUS_OPERATOR must NOT change what
    verified_operator() reports -- only real, live AWS credentials can."""
    import providers.base as pb
    arn = "arn:aws:sts::123456789012:assumed-role/MinusDeploy/carol@corp.com"
    monkeypatch.setattr(pb, "get_provider", lambda: _FakeProvider({"connected": True, "arn": arn}))
    monkeypatch.setenv(authz.OPERATOR_ENV, "mallory@evil")
    assert authz.verified_operator() == "carol@corp.com"


def test_verified_operator_degrades_to_none_on_provider_error(monkeypatch):
    import providers.base as pb

    def _raise():
        raise RuntimeError("no credentials configured")
    monkeypatch.setattr(pb, "get_provider", _raise)
    assert authz.verified_operator() is None
