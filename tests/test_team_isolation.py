"""
Sprint 2 (MINUS-153/141/142/147): team directory, state isolation, role binding, audit shipping.

The through-line: a team id stops being decoration the moment it decides where state lives and
which role may apply it, so these tests are mostly about what happens when it is wrong.
"""
import json
import os

import pytest

import audit_logger
import plan_gate
import synthesizer
import team_resolver

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXAMPLE = os.path.join(_ROOT, "configs", "teams.yaml.example")


# --- MINUS-153: the directory ---------------------------------------------------------------

def test_absent_directory_still_resolves(tmp_path):
    """The directory is opt-in. A machine without one must generate exactly as before."""
    record = team_resolver.resolve("acme-data", path=str(tmp_path / "nope.yaml"))
    assert record["team_id"] == "acme-data"
    assert record["configured"] is False
    assert record["deploy_role_pattern"] == "arn:aws:iam::*:role/minusops-deploy-acme-data"


def test_example_directory_parses_and_resolves():
    record = team_resolver.resolve("acme-data", path=_EXAMPLE)
    assert record["configured"] is True
    assert record["cost_center"] == "CC-4471"
    assert "acme-ml" in team_resolver.list_teams(_EXAMPLE)


def test_example_stores_a_secret_reference_not_a_webhook():
    """A Teams webhook URL is a credential -- anyone holding it can post as your bot."""
    text = open(_EXAMPLE, encoding="utf-8").read()
    assert "arn:aws:secretsmanager" in text
    assert "outlook.office.com" not in text
    assert "webhook.office.com" not in text


@pytest.mark.parametrize("bad", ["../escape", "team/sub", "UPPER", "star*", "",
                                 "a" * 64, "-leading"])
def test_ids_that_could_escape_a_prefix_or_widen_a_role_are_refused(bad):
    """The id is interpolated into an S3 key and an IAM role ARN. `..` walks out of the team
    prefix; `*` widens the role pattern it lands in."""
    with pytest.raises(team_resolver.InvalidTeamId):
        team_resolver.validate_team_id(bad)


def test_malformed_directory_raises_rather_than_looking_empty(tmp_path):
    """Silently ignoring it would be indistinguishable from "no teams configured", hiding the
    mistake behind plausible behaviour."""
    bad = tmp_path / "teams.yaml"
    bad.write_text("teams: [not, a, mapping]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        team_resolver.load_directory(str(bad))


# --- MINUS-141: state isolation -------------------------------------------------------------

def test_team_state_key_isolates_squads():
    assert team_resolver.state_key("acme-data", "lakehouse") == \
        "teams/acme-data/lakehouse/terraform.tfstate"
    assert team_resolver.state_key("acme-ml", "lakehouse") != \
        team_resolver.state_key("acme-data", "lakehouse")


def test_workload_id_is_validated_too():
    """A workload id is equally operator-supplied and lands in the same key, so a traversal
    there escapes the team prefix just as effectively."""
    with pytest.raises(team_resolver.InvalidTeamId):
        team_resolver.state_key("acme-data", "../other-team")


def test_backend_uses_the_team_key_and_native_locking():
    rendered = synthesizer._render_backend(
        {"bucket": "b", "region": "us-east-1", "team_id": "acme-data",
         "workload_id": "lakehouse"}, "acme", "run1")
    assert 'key          = "teams/acme-data/lakehouse/terraform.tfstate"' in rendered
    assert "use_lockfile = true" in rendered
    assert "dynamodb_table" not in rendered


def test_no_team_keeps_the_existing_run_scoped_key():
    """A changed key is an orphaned state file and a plan that recreates everything, so an
    existing stack's key must not silently move when the feature ships."""
    rendered = synthesizer._render_backend({"bucket": "b", "region": "us-east-1"}, "acme", "run1")
    assert 'key          = "acme/run1/terraform.tfstate"' in rendered


# --- MINUS-142: role binding ----------------------------------------------------------------

def _tf_dir(tmp_path, key):
    tf = tmp_path / "terraform"
    tf.mkdir(parents=True)
    (tf / "providers.tf").write_text(
        'terraform {\n  backend "s3" {\n    key          = "%s"\n  }\n}\n' % key,
        encoding="utf-8")
    return str(tf)


def test_team_is_read_from_the_generated_key_not_a_flag(tmp_path):
    """The flag is what an operator TYPED; the key is what the stack actually writes."""
    tf = _tf_dir(tmp_path / "a", "teams/acme-data/lakehouse/terraform.tfstate")
    assert plan_gate._backend_team(tf) == "acme-data"
    plain = _tf_dir(tmp_path / "b", "acme/run1/terraform.tfstate")
    assert plan_gate._backend_team(plain) is None


def test_another_teams_role_is_refused(tmp_path):
    tf = _tf_dir(tmp_path, "teams/acme-data/lakehouse/terraform.tfstate")
    blocked = plan_gate._reject_if_wrong_team_role(
        tf, {"arn": "arn:aws:iam::111:role/minusops-deploy-acme-ml"}, "gatekeeper")
    assert blocked is True


def test_the_teams_own_role_passes_including_an_assumed_session(tmp_path):
    tf = _tf_dir(tmp_path, "teams/acme-data/lakehouse/terraform.tfstate")
    for arn in ("arn:aws:iam::111:role/minusops-deploy-acme-data",
                "arn:aws:sts::111:assumed-role/minusops-deploy-acme-data/alice"):
        assert plan_gate._reject_if_wrong_team_role(tf, {"arn": arn}, "gatekeeper") is False


def test_unreadable_identity_fails_closed(tmp_path):
    """If we cannot tell whose session this is, we cannot tell it may write another squad's
    state."""
    tf = _tf_dir(tmp_path, "teams/acme-data/lakehouse/terraform.tfstate")
    assert plan_gate._reject_if_wrong_team_role(tf, {}, "gatekeeper") is True
    assert plan_gate._reject_if_wrong_team_role(tf, None, "auto-approve") is True


def test_auto_approve_gets_no_exemption(tmp_path):
    """An unattended runner is exactly the case this check exists for."""
    tf = _tf_dir(tmp_path, "teams/acme-data/lakehouse/terraform.tfstate")
    assert plan_gate._reject_if_wrong_team_role(
        tf, {"arn": "arn:aws:iam::111:user/admin"}, "auto-approve") is True


def test_an_unscoped_stack_is_not_blocked(tmp_path):
    """No team means nothing to check against; inventing one would block every existing run."""
    tf = _tf_dir(tmp_path, "acme/run1/terraform.tfstate")
    assert plan_gate._reject_if_wrong_team_role(
        tf, {"arn": "arn:aws:iam::1:user/x"}, "gatekeeper") is False


def test_wildcard_does_not_cross_an_arn_field():
    """An account-id wildcard must not also swallow the role name."""
    assert team_resolver.role_matches(
        "arn:aws:iam::999:role/minusops-deploy-acme-data",
        "arn:aws:iam::*:role/minusops-deploy-acme-data") is True
    assert team_resolver.role_matches(
        "arn:aws:iam::999:role/some-other-role",
        "arn:aws:iam::*:role/minusops-deploy-acme-data") is False


# --- MINUS-147: audit shipping --------------------------------------------------------------

def test_shipping_is_off_until_configured(monkeypatch):
    for var in (audit_logger.S3_BUCKET_ENV, audit_logger.CW_GROUP_ENV):
        monkeypatch.delenv(var, raising=False)
    assert audit_logger.ship_event({"timestamp": "2026-08-19T00:00:00+00:00"}) == []


def test_a_failed_ship_never_loses_the_local_event(tmp_path, monkeypatch, capsys):
    """The local chain is the system of record. Losing an event because a remote sink was
    unreachable would be strictly worse than the gap this feature closes."""
    monkeypatch.setenv(audit_logger.S3_BUCKET_ENV, "audit-bucket")
    monkeypatch.delenv(audit_logger.CW_GROUP_ENV, raising=False)
    monkeypatch.setattr(audit_logger, "_aws", lambda *a, **k: (False, "network is down"))

    assert audit_logger.log_audit_event("apply", "x", str(tmp_path)) is True
    assert (tmp_path / "audit.jsonl").exists()
    assert "audit shipping FAILED" in capsys.readouterr().err


def test_each_event_is_a_distinct_immutable_object(monkeypatch):
    """One object per event, not an append: S3 has no append, and rewriting one growing object
    under Object Lock would either be refused or retain every old version."""
    monkeypatch.setenv(audit_logger.S3_BUCKET_ENV, "audit-bucket")
    monkeypatch.delenv(audit_logger.CW_GROUP_ENV, raising=False)
    seen = []

    def _fake(args, **kwargs):
        seen.append(args)
        return True, ""

    monkeypatch.setattr(audit_logger, "_aws", _fake)
    for digest in ("aaaa1111bbbb2222", "cccc3333dddd4444"):
        audit_logger.ship_event({"timestamp": "2026-08-19T00:00:00+00:00",
                                 "entry_hash": digest})

    # By value, not by position: `aws s3 cp - s3://...` puts the destination at index 3,
    # and a positional assumption would silently assert on the "-" stdin marker instead.
    keys = [arg for args in seen for arg in args if str(arg).startswith("s3://")]
    assert len(set(keys)) == 2
    assert all(k.startswith("s3://audit-bucket/audit/") and k.endswith(".json") for k in keys)


def test_shipped_payload_carries_the_chain_hash(tmp_path, monkeypatch):
    """The remote copy must carry the same entry_hash a verifier recomputes, which is why the
    ship happens after the local append and uses what the chain returned."""
    monkeypatch.setenv(audit_logger.S3_BUCKET_ENV, "audit-bucket")
    monkeypatch.delenv(audit_logger.CW_GROUP_ENV, raising=False)
    sent = {}

    def _fake(args, stdin=None, **kwargs):
        sent["body"] = stdin
        return True, ""

    monkeypatch.setattr(audit_logger, "_aws", _fake)
    audit_logger.log_audit_event("apply", "x", str(tmp_path))

    local = json.loads(open(tmp_path / "audit.jsonl", encoding="utf-8").readlines()[-1])
    assert json.loads(sent["body"])["entry_hash"] == local["entry_hash"]
