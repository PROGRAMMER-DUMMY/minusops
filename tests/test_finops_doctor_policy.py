"""
Sprint 4: BCM usage heuristics, doctor --fix, G6 verify-stage promotion.
"""
import json
import os
import subprocess

import bcm_pricing_calculator as bcm
import doctor
import plan_gate


# --- Usage heuristics -----------------------------------------------------------------------

def _reqs(volume=None, transforms=None, latency=None, retention=None):
    return {
        "data_pipeline": {k: v for k, v in
                          (("data_volume", volume), ("transforms", transforms)) if v},
        "non_functional": {k: v for k, v in
                           (("latency", latency), ("retention", retention)) if v},
    }


def test_cadence_is_read_from_the_stated_schedule():
    """The default is 24 runs/day whether the pipeline is nightly or a 15-minute micro-batch.
    On runs/20260818-085523 that was a 4x understatement in an estimate that had already
    passed the budget gate."""
    for phrase, expected in [
        ("Glue jobs on a 15-minute micro-batch", 96.0),
        ("nightly batch load", 1.0),
        ("hourly incremental refresh", 24.0),
        ("every 6 hours", 4.0),
        ("weekly rebuild", round(1 / 7, 4)),
    ]:
        derived = bcm.auto_populate_usage(_reqs(transforms=phrase))
        assert derived["assumptions"]["glue_runs_per_day"] == expected, phrase


def test_specific_cadence_beats_the_generic_word():
    """"15-minute" must win over a bare "minute", and an explicit interval over "hourly"."""
    assert bcm.auto_populate_usage(
        _reqs(transforms="every 30 minutes, not hourly")
    )["assumptions"]["glue_runs_per_day"] == 48.0


def test_unstated_cadence_is_left_alone_and_named():
    """A fabricated assumption that looks derived is worse than a default that is visibly a
    default."""
    derived = bcm.auto_populate_usage(_reqs(volume="50 GB per day"))
    assert "glue_runs_per_day" not in derived["assumptions"]
    assert any("glue_runs_per_day" in gap for gap in derived["unresolved"])


def test_retention_uses_the_raw_zone_not_the_longest_period():
    """Requirements normally name several ("Bronze 90 days, Gold 3 years"). Taking the maximum
    assumes every byte is kept for the longest one -- 100 GB/day became 109,500 GB-Mo, a 36x
    overstatement, before this was fixed."""
    derived = bcm.auto_populate_usage(
        _reqs(retention="Bronze raw retained 90 days; Gold curated retained 3 years"))
    assert derived["assumptions"]["s3_storage_retention_factor"] == 90


def test_retention_falls_back_to_the_shortest_when_no_zone_is_named():
    """Understating storage is a recoverable surprise; overstating it by 36x makes the whole
    forecast unusable."""
    derived = bcm.auto_populate_usage(_reqs(retention="kept 30 days, archived 5 years"))
    assert derived["assumptions"]["s3_storage_retention_factor"] == 30


def test_every_derived_value_records_the_phrase_it_came_from():
    """A derived assumption nobody can trace is worse than a default everybody knows is a
    default."""
    derived = bcm.auto_populate_usage(
        _reqs(volume="100 GB per day", transforms="hourly", retention="90 days"))
    assert set(derived["provenance"]) == set(derived["assumptions"])
    assert "hourly" in derived["provenance"]["glue_runs_per_day"]


def test_explicit_assumptions_beat_derived_ones(tmp_path, monkeypatch):
    """A derived value is a reading of prose; an operator who disagrees needs a way to say so
    that does not involve editing the requirements."""
    run = tmp_path / "run"
    (run / "reports" / "abc").mkdir(parents=True)
    (run / "requirements.json").write_text(
        json.dumps(_reqs(volume="100 GB per day", transforms="hourly")), encoding="utf-8")

    merged = bcm._merge_assumptions(str(run / "reports" / "abc"), None,
                                    {"glue_runs_per_day": 3})
    assert merged["glue_runs_per_day"] == 3
    assert merged["daily_data_gb"] == 100.0


def test_no_requirements_file_leaves_assumptions_untouched(tmp_path):
    assert bcm._merge_assumptions(str(tmp_path), None, {"x": 1}) == {"x": 1}


# --- Doctor --fix ---------------------------------------------------------------------------

def test_a_wedged_docker_daemon_is_reported_as_unresponsive_not_missing(monkeypatch):
    """Observed 2026-08-18: every Docker Desktop process alive, named pipe present, WSL distro
    Running, and `docker version` never returned. "Unresponsive" and "not installed" need
    completely different fixes."""
    monkeypatch.setattr(doctor, "_port_open", lambda *a, **k: False)
    monkeypatch.setattr(doctor.toolpath, "find_tool", lambda name, *a, **k: "/usr/bin/docker")

    def _hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=20)

    monkeypatch.setattr(doctor.subprocess, "run", _hang)
    result = doctor.start_local_emulator()
    assert result["ok"] is False
    assert result["action"] == "docker-unavailable"
    assert "unresponsive" in result["detail"]


def test_fix_never_restarts_docker_desktop():
    """A restart kills every other container on the machine. This command was asked to fix an
    emulator, not to take over the host.

    Asserted on the string LITERALS the module can pass to a subprocess, not on its prose:
    doctor.py explains why it will not restart Docker, so any text search trips over its own
    rationale."""
    import ast
    source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "core", "reporting", "doctor.py"), encoding="utf-8").read()
    literals = {node.value for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    for forbidden in ("restart", "Docker Desktop.exe", "com.docker.backend"):
        assert forbidden not in literals, f"doctor can issue a {forbidden!r} command"


def test_an_already_listening_port_is_left_alone(monkeypatch):
    monkeypatch.setattr(doctor, "_port_open", lambda *a, **k: True)
    result = doctor.start_local_emulator()
    assert result["ok"] is True
    assert result["action"] == "already-listening"


def test_a_started_container_that_never_binds_is_not_a_success(monkeypatch):
    """The container being up is not the same as the port answering; reporting success on
    `docker run` alone is how a green fix leaves a red gate."""
    monkeypatch.setattr(doctor, "_port_open", lambda *a, **k: False)
    monkeypatch.setattr(doctor, "_docker", lambda args, **k: (True, "", ""))
    monkeypatch.setattr(doctor.time, "sleep", lambda _s: None)
    monkeypatch.setattr(doctor.time, "time", _clock())
    result = doctor.start_local_emulator()
    assert result["ok"] is False
    assert result["action"] == "started-not-listening"


def _clock():
    state = {"t": 0.0}

    def _now():
        state["t"] += 30.0
        return state["t"]

    return _now


def test_fix_only_touches_checks_it_can_actually_repair(monkeypatch):
    """The other warnings need a package install or a credential decision -- things this
    command must not make on someone's behalf."""
    monkeypatch.setattr(doctor, "start_local_emulator",
                        lambda **k: {"ok": True, "action": "started", "detail": "up"})
    checks = [{"name": "opa", "status": "warn"}, {"name": "cloud credentials", "status": "warn"},
              {"name": "g9 emulator", "status": "warn"}]
    repairs = doctor.fix(checks)
    assert [r["check"] for r in repairs] == ["g9 emulator"]


# --- G6 promotion ---------------------------------------------------------------------------

def test_production_refuses_without_a_policy_engine(monkeypatch, tmp_path):
    """A passing verify with no OPA would be asserting a compliance check that never ran."""
    monkeypatch.setattr(plan_gate.toolpath, "find_tool", lambda name, *a, **k: None)
    assert plan_gate._reject_if_policy_engine_unavailable(str(tmp_path), "production") is False


def test_standard_mode_still_runs_without_opa(monkeypatch, tmp_path):
    """A developer without OPA must still be able to iterate."""
    monkeypatch.setattr(plan_gate.toolpath, "find_tool", lambda name, *a, **k: None)
    assert plan_gate._reject_if_policy_engine_unavailable(str(tmp_path), "standard") is True


def test_an_available_engine_passes_both_modes(monkeypatch, tmp_path):
    monkeypatch.setattr(plan_gate.toolpath, "find_tool", lambda name, *a, **k: "/usr/bin/opa")
    for mode in ("standard", "production"):
        assert plan_gate._reject_if_policy_engine_unavailable(str(tmp_path), mode) is True


def test_rules_are_not_bulk_promoted_to_blocking():
    """Flipping all 13 was considered and rejected: they cover 8 of 47 reviewed resource types,
    and an enforcing gate at 17% coverage reads as "policy is enforced" while 83% passes
    unexamined. Promotion stays per-rule and human-attributable."""
    import rule_stages
    registry = json.load(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "policy", "rule_stages.json"), encoding="utf-8"))
    rules = registry.get("rules", registry)
    stages = {r if isinstance(r, str) else r.get("stage") for r in rules.values()}
    assert stages == {"warn"}, f"a rule was promoted without a recorded human decision: {stages}"
    assert hasattr(rule_stages, "promote")


def test_a_failed_evaluation_is_not_a_violation():
    """OPA is optional; treating "opa not installed" as a policy breach would make an optional
    tool a hard dependency of every plan."""
    assert plan_gate._reject_if_promoted_policy_violated(
        ".", {"evaluation_failed": True, "findings": []}, False) is False
