"""`minusctl doctor` — the ok/warn/error contract the exit code is bound to."""
import doctor
import minusctl


def _fake_env(monkeypatch, tools, posture):
    """Pretend `tools` are the only CLIs on PATH and the provider reports `posture`."""
    monkeypatch.setattr(doctor.toolpath, "find_tool", lambda name, *a, **k: (
        f"/usr/bin/{name}" if name in tools else None))
    monkeypatch.setattr(doctor, "_version", lambda path, args: "v0-test")
    monkeypatch.setattr(doctor, "get_provider",
                        lambda *a, **k: type("P", (), {"credential_posture": lambda self: posture})())


def _status(result, name):
    return next(c["status"] for c in result["checks"] if c["name"] == name)


def test_missing_terraform_blocks(monkeypatch):
    _fake_env(monkeypatch, {"aws"}, {"connected": True, "account": "1", "arn": "a", "type": "temporary"})
    result = doctor.diagnose()
    assert result["ok"] is False
    assert _status(result, "terraform") == "error"
    assert "blocked on: terraform" in doctor.format_result(result)


def test_missing_optional_tools_only_warn(monkeypatch):
    _fake_env(monkeypatch, {"aws", "terraform"},
              {"connected": True, "account": "1", "arn": "a", "type": "temporary"})
    result = doctor.diagnose()
    assert result["ok"] is True
    assert _status(result, "opa") == "warn"
    assert _status(result, "policy scanners") == "warn"
    assert _status(result, "cloud credentials") == "ok"


def test_static_credentials_warn(monkeypatch):
    """Long-term keys still "work", but an unattended auto-approve run holding them can
    apply real infrastructure -- that must never read as a clean [OK]."""
    _fake_env(monkeypatch, {"aws", "terraform"},
              {"connected": True, "account": "1", "arn": "a", "type": "long_term"})
    assert _status(doctor.diagnose(), "cloud credentials") == "warn"


def test_no_credentials_is_an_error(monkeypatch):
    _fake_env(monkeypatch, {"aws", "terraform"}, {"connected": False, "error": "expired"})
    result = doctor.diagnose()
    assert result["ok"] is False
    assert _status(result, "cloud credentials") == "error"


def test_minusctl_doctor_exit_code_follows_ok(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "diagnose", lambda: {"ok": False, "checks": [
        {"name": "terraform", "status": "error", "detail": "missing", "fix": "install it"}]})
    assert minusctl.main(["doctor"]) == 1
    assert "install it" in capsys.readouterr().out
