"""`minusctl doctor` — the ok/warn/error contract the exit code is bound to."""
import os

import pytest

import doctor
import minusctl

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


# --- MINUS-138: offline init ---------------------------------------------------------------

def test_cache_dir_is_created_not_merely_detected(tmp_path, monkeypatch):
    """The old code only exported TF_PLUGIN_CACHE_DIR `if os.path.isdir(cache_dir)`, so on a
    fresh clone the very first init -- the slow one -- ran with no cache and never populated
    it, leaving every later init slow too."""
    import toolpath
    monkeypatch.setattr(toolpath, "_ensured", False)
    monkeypatch.setattr(toolpath, "_refresh_windows_path", lambda: None)
    monkeypatch.delenv("TF_PLUGIN_CACHE_DIR", raising=False)

    toolpath.ensure_external_tools()

    cache = os.environ["TF_PLUGIN_CACHE_DIR"]
    assert cache.endswith(os.path.join(".agents", "tf-plugin-cache"))
    assert os.path.isdir(cache)


def test_compose_seeds_the_dependency_lock_file(tmp_path):
    """TF_PLUGIN_CACHE_DIR alone does not make init offline: with no lock file entry Terraform
    contacts the registry for checksums and downloads the whole package, ignoring the cache.
    Measured: no lock file, init never finished in 15 min; lock file + cache, 5.4 s."""
    import synthesizer
    out = tmp_path / "tf"
    synthesizer.compose(["storage-medallion-s3"], "t", str(out), run_id="r1")
    seeded = out / ".terraform.lock.hcl"
    repo_lock = os.path.join(_ROOT, ".agents", "terraform.lock.hcl")
    if not os.path.exists(repo_lock):
        pytest.skip("no canonical lock file in .agents/ to seed from")
    assert seeded.exists()
    assert "registry.terraform.io/hashicorp/aws" in seeded.read_text(encoding="utf-8")


# --- MINUS-139: G9 emulator pre-flight ------------------------------------------------------

def _emulator_env(monkeypatch, name=None, listening=False):
    import doctor
    monkeypatch.delenv(doctor.plan_gate.G9_EMULATOR_ENV, raising=False)
    monkeypatch.delenv(doctor.ephemeral_apply.LOCALSTACK_ENDPOINT_ENV, raising=False)
    if name is not None:
        monkeypatch.setenv(doctor.plan_gate.G9_EMULATOR_ENV, name)
    monkeypatch.setattr(doctor, "_port_open", lambda host, port, timeout=0.4: listening)
    return doctor._emulator_check()


def test_emulator_ok_when_selected_and_listening(monkeypatch):
    check = _emulator_env(monkeypatch, name="localstack", listening=True)
    assert check["status"] == "ok"
    assert "localstack" in check["detail"] and "4566" in check["detail"]


def test_selected_but_dead_is_reported_distinctly_from_absent(monkeypatch):
    """These fail independently and the fix differs. A selected emulator with nothing behind
    it is the worse case -- the gate looks configured, then fails on every plan."""
    dead = _emulator_env(monkeypatch, name="localstack", listening=False)
    absent = _emulator_env(monkeypatch, name=None, listening=False)
    assert dead["status"] == absent["status"] == "warn"
    assert "nothing is listening" in dead["detail"]
    assert "no emulator configured" in absent["detail"]
    assert dead["detail"] != absent["detail"]


def test_port_up_but_unset_env_tells_you_to_name_it(monkeypatch):
    check = _emulator_env(monkeypatch, name=None, listening=True)
    assert check["status"] == "warn"
    assert "MINUS_G9_EMULATOR" in check["detail"]
    assert "set MINUS_G9_EMULATOR=localstack" in check["fix"]


def test_unsupported_emulator_name_is_surfaced(monkeypatch):
    """ephemeral_apply BLOCKS on an unrecognized name rather than guessing, so a typo here
    disables G9 on every plan without ever saying why."""
    check = _emulator_env(monkeypatch, name="locolstack", listening=True)
    assert check["status"] == "warn"
    assert "not a supported emulator" in check["detail"]
    assert "ministack" in check["fix"]


def test_emulator_never_errors_so_a_machine_without_docker_can_still_plan(monkeypatch):
    for name, listening in ((None, False), ("localstack", False), ("nope", False)):
        assert _emulator_env(monkeypatch, name=name, listening=listening)["status"] != "error"


def test_doctor_includes_the_emulator_check(monkeypatch):
    import doctor
    monkeypatch.setattr(doctor, "_port_open", lambda *a, **k: False)
    _fake_env(monkeypatch, {"terraform", "aws"},
              {"connected": True, "account": "1", "arn": "a", "type": "temporary"})
    assert any(c["name"] == "g9 emulator" for c in doctor.diagnose()["checks"])
