"""
The deploy gate must enforce the product's MFA-gated promise: apply runs on a
temporary session (SSO / assumed MFA role), never long-term static keys or root,
unless explicitly overridden (and then audited).
"""
import json
import os

import pytest

import plan_gate
from providers import aws


# ---- credential classification (pure) ----
def test_classify_temporary_from_access_key_prefix():
    assert aws.classify_credentials("arn:aws:iam::123:user/x", "ASIASOMETHING") == "temporary"


def test_classify_long_term_from_access_key_prefix():
    assert aws.classify_credentials("arn:aws:iam::123:user/x", "AKIASOMETHING") == "long_term"


def test_classify_temporary_from_assumed_role_arn():
    assert aws.classify_credentials("arn:aws:sts::123:assumed-role/Deploy/sess") == "temporary"


def test_classify_long_term_from_user_arn():
    assert aws.classify_credentials("arn:aws:iam::123:user/alice") == "long_term"


def test_classify_root():
    assert aws.classify_credentials("arn:aws:iam::123:root") == "root"


# ---- gate enforcement ----
@pytest.fixture
def applyable(tmp_path, monkeypatch):
    """A gate primed so stage_apply reaches the credential-posture check."""
    monkeypatch.setattr(plan_gate, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(plan_gate, "_source_status_for_hash",
                        lambda _h: {"status": "CURRENT", "stale": False, "reason": ""})
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("123456789012", True))
    monkeypatch.setattr(plan_gate, "_tf", _stub_tf(PLAN))
    current, _ = plan_gate._plan_hash("d")
    os.makedirs(plan_gate._approval_dir("d"), exist_ok=True)
    with open(plan_gate._approved_path("d", current), "w", encoding="utf-8") as f:
        json.dump({"plan_hash": current, "dir": "d",
                   "canonical_dir": plan_gate._canonical_dir("d")}, f)
    return tmp_path


PLAN = {"resource_changes": [{"address": "aws_s3_bucket.d", "type": "aws_s3_bucket",
                              "name": "d", "change": {"actions": ["create"]}}],
        "output_changes": {}}


def _stub_tf(plan):
    def _tf(dir_, *args, capture=False):
        if "show" in args:
            return 0, json.dumps(plan), ""
        return 0, "", ""
    return _tf


def test_apply_refuses_long_term_credentials(applyable, monkeypatch):
    monkeypatch.delenv("MINUS_ALLOW_STATIC_CREDS", raising=False)
    monkeypatch.setattr(plan_gate, "_credential_posture", lambda: {"connected": True, "type": "long_term"})
    assert plan_gate.stage_apply("d") is False
    # Approval is preserved so the operator can re-auth and retry.
    current, _ = plan_gate._plan_hash("d")
    assert os.path.exists(plan_gate._approved_path("d", current))


def test_apply_refuses_root_credentials(applyable, monkeypatch):
    monkeypatch.delenv("MINUS_ALLOW_STATIC_CREDS", raising=False)
    monkeypatch.setattr(plan_gate, "_credential_posture", lambda: {"connected": True, "type": "root"})
    assert plan_gate.stage_apply("d") is False


def test_apply_allows_long_term_with_explicit_override(applyable, monkeypatch):
    monkeypatch.setenv("MINUS_ALLOW_STATIC_CREDS", "1")
    monkeypatch.setattr(plan_gate, "_credential_posture", lambda: {"connected": True, "type": "long_term"})
    assert plan_gate.stage_apply("d") is True


def test_apply_allows_temporary_credentials(applyable, monkeypatch):
    monkeypatch.setattr(plan_gate, "_credential_posture", lambda: {"connected": True, "type": "temporary"})
    assert plan_gate.stage_apply("d") is True
