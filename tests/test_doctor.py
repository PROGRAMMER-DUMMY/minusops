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


# --- Offline init --------------------------------------------------------------------------

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


# --- G9 emulator pre-flight -----------------------------------------------------------------

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


# --- Version floors, lock seed, and the skill manifest -------------------------------------

def test_version_parser_handles_both_tool_formats():
    """`terraform version` prints "Terraform v1.15.7"; the AWS CLI prints
    "aws-cli/2.35.11 Python/3.14.5". Taking the FIRST match is what makes both work -- and is
    why the CLI's own version must be read before the Python one it embeds."""
    import doctor
    assert doctor._parse_version("Terraform v1.15.7") == (1, 15, 7)
    assert doctor._parse_version("aws-cli/2.35.11 Python/3.14.5") == (2, 35, 11)
    assert doctor._parse_version("no version here") is None


def test_terraform_below_the_generated_required_version_is_an_error(monkeypatch):
    """The synthesizer writes `required_version = ">= 1.5"` into every composed root, so an
    older binary cannot plan what this repo generates."""
    import doctor
    monkeypatch.setattr(doctor.toolpath, "find_tool", lambda name, *a, **k: "/usr/bin/terraform")
    monkeypatch.setattr(doctor, "_version", lambda path, args: "Terraform v1.4.6")
    check = doctor._cli_check("terraform", "terraform", ("version",), True, "fix",
                              min_version=(1, 5))
    assert check["status"] == "error"
    assert "below the required 1.5" in check["detail"]


def test_unreadable_version_warns_rather_than_blocks(monkeypatch):
    """Present and runnable but with unparseable output: the tool works, we just cannot prove
    the floor. Blocking on a parse failure would be worse than saying so."""
    import doctor
    monkeypatch.setattr(doctor.toolpath, "find_tool", lambda name, *a, **k: "/usr/bin/terraform")
    monkeypatch.setattr(doctor, "_version", lambda path, args: "(version probe failed: boom)")
    check = doctor._cli_check("terraform", "terraform", ("version",), True, "fix",
                              min_version=(1, 5))
    assert check["status"] == "warn"


def test_missing_lock_seed_is_reported(monkeypatch, tmp_path):
    """Without the seed every fresh run re-downloads ~855 MB per provider."""
    import doctor
    monkeypatch.setattr(doctor.os.path, "exists", lambda p: False)
    check = doctor._lockfile_check()
    assert check["status"] == "warn"
    assert "terraform.lock.hcl" in check["detail"]


def test_doctor_skill_manifest_exists_and_names_the_command():
    skill = open(os.path.join(_ROOT, ".agents", "skills", "doctor", "SKILL.md"),
                 encoding="utf-8").read()
    # The front door, not the script path. `.agents/AGENTS.md` section 2 and pyproject's
    # [project.scripts] comment both say to invoke `minusctl`; a skill that teaches
    # `python core/reporting/minusctl.py` teaches around the CLI's own context resolution.
    assert "minusctl doctor --json" in skill
    assert "python core/reporting/minusctl.py" not in skill
    # The manifest must not promise a check the code does not make.
    assert "configs/teams.yaml" in skill and "no such file" in skill.lower()


def test_the_opa_check_says_the_rego_tests_skip_without_it(monkeypatch):
    """The old remediation said the gate "degrades to warn-only". True, and not the fact
    that costs time: `tests/test_rego_gate.py` carries a module-level skipif on the opa
    binary, so without it the whole G6 suite silently skips. Two real catalog failures sat
    in CI for four commits because every local run reported green while skipping them."""
    real_find = doctor.toolpath.find_tool
    monkeypatch.setattr(doctor.toolpath, "find_tool",
                        lambda name: None if name == "opa" else real_find(name))
    checks = {c["name"]: c for c in doctor.diagnose()["checks"]}

    assert "opa" in checks
    remediation = checks["opa"]["fix"].lower()
    assert "skip" in remediation, "the remediation must name the silent-skip consequence"
    assert "rego" in remediation or "test_rego_gate" in remediation


def test_the_opa_check_pins_the_version_ci_uses(monkeypatch):
    """A local opa that disagrees with CI's produces findings that do not reproduce."""
    real_find = doctor.toolpath.find_tool
    monkeypatch.setattr(doctor.toolpath, "find_tool",
                        lambda name: None if name == "opa" else real_find(name))
    checks = {c["name"]: c for c in doctor.diagnose()["checks"]}

    assert "1.18" in checks["opa"]["fix"]


# --- The console script is reachable ---------------------------------------------------------

def test_a_missing_minusctl_on_path_is_a_warn_that_names_the_directory(monkeypatch):
    """The binary exists and the instruction still fails: on a Windows Store Python the
    scripts directory is not on PATH by default, so an operator reads "run minusctl doctor",
    gets "command not found", and concludes the install is broken.

    A warn, never an error -- `python -m core.cli.main` is the identical entry point.
    """
    import doctor
    monkeypatch.setattr(doctor.toolpath, "find_tool", lambda name, *a, **k: None)
    monkeypatch.setattr(doctor.os.path, "exists",
                        lambda p: p.endswith(("minusctl.exe", "minusctl")))

    check = doctor._console_script_check()
    assert check["status"] == "warn"
    assert "not on PATH" in check["detail"]
    assert "core.cli.main" in check["fix"], "the fix must name the working alternative"


def test_minusctl_found_on_path_is_ok(monkeypatch):
    import doctor
    monkeypatch.setattr(doctor.toolpath, "find_tool", lambda name, *a, **k: "/usr/bin/minusctl")
    assert doctor._console_script_check()["status"] == "ok"


def test_a_missing_console_script_never_blocks(monkeypatch):
    """It is a convenience, not a capability. Erroring here would fail `doctor` on a machine
    that can plan and apply perfectly well through the module entry point."""
    import doctor
    monkeypatch.setattr(doctor.toolpath, "find_tool", lambda name, *a, **k: None)
    monkeypatch.setattr(doctor.os.path, "exists", lambda p: False)
    assert doctor._console_script_check()["status"] != "error"


def test_doctor_includes_the_console_script_check(monkeypatch):
    import doctor
    monkeypatch.setattr(doctor, "_port_open", lambda *a, **k: False)
    _fake_env(monkeypatch, {"terraform", "aws"},
              {"connected": True, "account": "1", "arn": "a", "type": "temporary"})
    assert any(c["name"] == "minusctl on PATH" for c in doctor.diagnose()["checks"])
