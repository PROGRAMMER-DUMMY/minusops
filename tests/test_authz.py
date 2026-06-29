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
