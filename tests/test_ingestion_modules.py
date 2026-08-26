"""
Step 7 (MINUS-116/117/118/123-125): ingestion catalog, quarantine, scaffold, 7 pillars.

Fast: reads the module HCL and the registry, no Terraform. The four ingestion modules were
validated against the installed AWS provider schema (v6) during development; what is asserted
here is the properties that are easy to regress silently.
"""
import os

import modules
import synthesizer

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INGESTION = ("ingestion-dms", "ingestion-appflow", "ingestion-sftp", "ingestion-webhook")


def _hcl(module_id):
    return open(os.path.join(_ROOT, "modules", module_id, "main.tf"), encoding="utf-8").read()


def test_registry_stays_valid_with_the_new_modules():
    assert modules.validate_modules() == []
    registered = {m["id"] for m in modules.list_modules()}
    assert set(_INGESTION) <= registered


def test_each_ingestion_archetype_is_reachable_by_keyword():
    """A module nobody's words reach is a module that does not exist. Each archetype is
    matched from how someone would actually describe it, not from its module id."""
    for phrasing, expected in [
        ("replicate our postgres database into the lake with change data capture", "ingestion-dms"),
        ("pull accounts and opportunities from salesforce every night", "ingestion-appflow"),
        ("a partner drops csv files for us over sftp", "ingestion-sftp"),
        ("stripe pushes webhook events to us in real time", "ingestion-webhook"),
    ]:
        ids = [m["id"] for m in modules.match_modules(phrasing)]
        assert expected in ids[:3], f"{phrasing!r} -> {ids[:3]}"


def test_no_ingestion_module_takes_a_credential_as_an_input():
    """TerraShark FM-02. A password or token as a Terraform variable ends up in the plan and
    in state. Every one of these takes a Secrets Manager ARN or a connector-profile name
    instead -- the reference, never the secret."""
    for module_id in _INGESTION:
        hcl = _hcl(module_id)
        for forbidden in ('variable "password"', 'variable "api_key"', 'variable "secret_key"',
                          'variable "token"', 'variable "client_secret"'):
            assert forbidden not in hcl, f"{module_id} takes {forbidden}"


def test_dms_uses_secrets_manager_not_inline_credentials():
    hcl = _hcl("ingestion-dms")
    assert "secrets_manager_arn" in hcl
    assert "secrets_manager_access_role_arn" in hcl
    # `username`/`password` on aws_dms_endpoint are exactly what puts a plaintext password in
    # state, which is why the module does not expose them.
    assert "\n  password" not in hcl


def test_dms_replication_instance_is_not_public():
    """A CDC instance reaching a production database has no reason to hold a public address,
    and DMS cannot be moved out of a public subnet after creation."""
    assert "publicly_accessible         = false" in _hcl("ingestion-dms").replace("\t", " ")


def test_sftp_gives_each_user_their_own_role_and_prefix():
    """One shared role across partners is the usual shortcut and the usual incident: partner A
    must not list, read, or overwrite partner B's drop."""
    hcl = _hcl("ingestion-sftp")
    assert 'resource "aws_iam_role" "user"' in hcl and "for_each = var.users" in hcl
    assert "sftp/${each.key}/*" in hcl
    assert "home_directory_type = \"LOGICAL\"" in hcl  # chroot, not the whole bucket


def test_webhook_has_a_dead_letter_queue_and_a_throttle():
    """Without a DLQ one poison payload blocks the queue head until it ages out; without a
    throttle a public endpoint is a billing denial-of-service."""
    hcl = _hcl("ingestion-webhook")
    assert 'resource "aws_sqs_queue" "dlq"' in hcl
    assert "deadLetterTargetArn" in hcl
    assert "throttling_rate_limit" in hcl


def test_webhook_enqueues_the_body_verbatim():
    """The consumer needs the exact bytes the sender signed -- re-serializing the JSON here
    would break every downstream signature check."""
    assert 'MessageBody = "$request.body"' in _hcl("ingestion-webhook")


def test_quarantine_bucket_exists_and_is_separately_encrypted():
    """MINUS-117. Quarantined rows are the same data that failed validation, often the
    PII-bearing ones, so they must not land in a less-protected bucket than the lake."""
    hcl = _hcl("dq-great-expectations")
    assert 'resource "aws_s3_bucket" "quarantine"' in hcl
    assert 'variable "quarantine_kms_key_arn"' in hcl
    assert '"--quarantine_path"' in hcl
    assert 'sid       = "WriteQuarantine"' in hcl


def test_three_alert_tiers_are_distinct_topics():
    """One inbox for everything is why nobody reads the inbox. A spend threshold must not
    reach the on-call topic."""
    hcl = _hcl("governance-observability")
    assert 'resource "aws_sns_topic" "data_quality"' in hcl
    assert 'resource "aws_sns_topic" "budget"' in hcl
    # The budget alarm routes to Tier 3, not to the on-call topic.
    assert "alarm_actions       = [aws_sns_topic.budget.arn]" in hcl


def test_scaffold_writes_every_layer_and_never_overwrites(tmp_path):
    """MINUS-118. Re-synthesising into an existing run must not discard the operator's
    PySpark."""
    written = synthesizer.write_project_scaffold(str(tmp_path))
    assert len(written) == 6
    for layer in ("compute", "sql", "quality", "orchestration"):
        assert (tmp_path / "src" / layer / "README.md").exists()
    assert (tmp_path / "tests" / "fixtures" / "sample.json").exists()

    (tmp_path / "src" / "compute" / "README.md").write_text("MY NOTES", encoding="utf-8")
    assert synthesizer.write_project_scaffold(str(tmp_path)) == []
    assert (tmp_path / "src" / "compute" / "README.md").read_text(encoding="utf-8") == "MY NOTES"


def test_sample_fixture_contains_a_row_a_quality_suite_can_catch():
    """A fixture where every row passes proves nothing about the quality gate."""
    import json
    rows = json.loads(synthesizer._SAMPLE_FIXTURE)
    assert len(rows) == 3
    assert any(r["amount"] == 0.0 for r in rows)


def _grill_me_skill():
    return open(os.path.join(_ROOT, ".agents", "skills", "grill-me", "SKILL.md"),
                encoding="utf-8").read()


def test_grill_me_drops_the_demo_blueprint():
    """MINUS-116 plus TASK-TDD-2026-002 WP3. The stale guidance AGENTS.md flags must not
    survive as instruction -- the only surviving mention is the explicit prohibition."""
    skill = _grill_me_skill()
    prohibition = [line for line in skill.splitlines() if "aws-data-pipeline-standard" in line]
    assert len(prohibition) == 1 and prohibition[0].lstrip().startswith("> **Do not**")


def test_the_interview_covers_every_pillar_topic():
    """Asserted against the catalogue, not against a transcription of it.

    The pillars used to be written out longhand in SKILL.md and asserted there, so the
    document and the generator's idea of a pillar could disagree and only the document was
    checked. They now live in core/architecture/pillars.py, which is what the interview reads
    and what requirements.py validates against -- so that is where the coverage check belongs.
    """
    import pillars

    covered = " ".join(p["title"] + " " + p["question"] + " " + " ".join(p["maps_to"])
                       for p in pillars.PILLARS).lower()
    for topic in ("ingestion source", "storage medallion", "partitioning", "data quality",
                  "compute engine", "worker sizing", "orchestration", "serving",
                  "access control", "alert routing", "criticality", "proving"):
        assert topic in covered, topic


def test_the_skill_points_at_the_catalogue_rather_than_copying_it():
    """A second copy of eighteen questions is a second copy that goes stale. The document was
    carrying its own transcription of the pillars AND its own roadmap while
    requirements.LIFECYCLE held another; the duplicate is what drifted."""
    skill = _grill_me_skill()
    assert "core/architecture/pillars.py" in skill
    assert skill.count("#### Pillar") == 0, "the pillars are not transcribed into the skill"


def test_logging_pillar_names_retention_as_a_cost_leak():
    """"Never expire" is the CloudWatch default and the single most common silent FinOps
    leak in a data platform. The pillar is worthless if it does not say so."""
    skill = _grill_me_skill()
    assert "Never expire" in skill or "never expire" in skill
    assert "retention" in skill.lower()


def test_secrets_pillar_forbids_static_keys():
    """The deploy gate already rejects long-term AKIA keys in production. Asking the question
    at design time is what stops someone building a pipeline around them first."""
    skill = _grill_me_skill()
    assert "AKIA" in skill
    assert "AssumeRole" in skill or "assume role" in skill.lower()


def test_network_pillar_names_the_nat_charge_endpoints_avoid():
    """The S3 gateway endpoint is free and removes per-GB NAT processing for lake traffic.
    Without the number the question sounds like a preference rather than a bill."""
    skill = _grill_me_skill()
    assert "gateway endpoint" in skill.lower()
    assert "0.045" in skill or "NAT" in skill
