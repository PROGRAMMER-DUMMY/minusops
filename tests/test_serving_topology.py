"""
PRD v9 section 3: serving endpoints on the spec card, and a connection scaffold on export.

Gold data that nobody can connect to is not served. The four archetypes -- ad-hoc Athena,
the Redshift warehouse, the semantic layer, and reverse-ETL staging -- each have a concrete
address, and today an analyst has to reconstruct each one from Terraform outputs by hand.

The rule that shapes every test here: an endpoint is rendered only when the stack actually
provisioned it. A Redshift connection string printed for a stack with no Redshift is a
credential-shaped string that fails at connect time, and the analyst blames the tool.

Depends on: core/reporting/serving.py, core/cli/commands/runs.py, core/reporting/export.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import io
import json
import os
from contextlib import redirect_stdout

import pytest

import export
import runs
import serving
from core.cli import context as cli_context
from core.cli import main as cli_main

FULL_OUTPUTS = {
    "region": "us-east-1",
    "account_id": "123456789012",
    "athena_workgroup": "marketing_clickstream",
    "glue_catalog_database": "marketing_gold",
    "redshift_workgroup": "clickstream-wg",
    "redshift_database": "analytics",
    "bucket_names": {"gold": "acme-mktg-gold-prod-001"},
    "quarantine_bucket": "acme-mktg-quarantine-prod-001",
}


def _capture(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli_main.main(argv)
    return code, out.getvalue()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(cli_context, "WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- The endpoint builder -------------------------------------------------------------

def test_athena_renders_a_jdbc_url_an_analyst_can_paste():
    endpoints = serving.endpoints(FULL_OUTPUTS)
    athena = next(e for e in endpoints if e["archetype"] == "ad_hoc_sql")

    assert athena["connection"] == (
        "jdbc:awsathena://AwsRegion=us-east-1;Workgroup=marketing_clickstream")


def test_redshift_renders_the_serverless_host_and_port():
    endpoints = serving.endpoints(FULL_OUTPUTS)
    warehouse = next(e for e in endpoints if e["archetype"] == "data_warehouse")

    assert warehouse["connection"] == (
        "clickstream-wg.123456789012.us-east-1.redshift-serverless.amazonaws.com:5439/analytics")


def test_the_quarantine_bucket_is_surfaced_as_a_uri():
    endpoints = serving.endpoints(FULL_OUTPUTS)
    quarantine = next(e for e in endpoints if e["archetype"] == "reverse_etl")

    assert quarantine["connection"].startswith("s3://acme-mktg-quarantine-prod-001")


def test_a_stack_without_redshift_gets_no_warehouse_endpoint():
    """The rule this whole module turns on. A connection string for infrastructure that does
    not exist fails at connect time and the analyst blames the tool, not the stack."""
    outputs = {k: v for k, v in FULL_OUTPUTS.items()
               if k not in ("redshift_workgroup", "redshift_database")}

    archetypes = {e["archetype"] for e in serving.endpoints(outputs)}

    assert "data_warehouse" not in archetypes
    assert "ad_hoc_sql" in archetypes


def test_an_empty_stack_yields_no_endpoints_rather_than_placeholders():
    assert serving.endpoints({}) == []


def test_a_missing_region_does_not_produce_a_half_built_url():
    """`jdbc:awsathena://AwsRegion=None;...` is worse than no line at all -- it looks
    plausible enough to paste."""
    outputs = {k: v for k, v in FULL_OUTPUTS.items() if k != "region"}

    for endpoint in serving.endpoints(outputs):
        assert "None" not in endpoint["connection"]
        assert "=;" not in endpoint["connection"]


def test_the_semantic_layer_endpoint_points_at_the_model_file():
    endpoints = serving.endpoints(FULL_OUTPUTS, modules=["dbt-semantic-layer"])
    semantic = next(e for e in endpoints if e["archetype"] == "semantic_layer")

    assert "semantic_models.yml" in semantic["connection"]


def test_no_semantic_endpoint_without_a_semantic_module():
    archetypes = {e["archetype"] for e in serving.endpoints(FULL_OUTPUTS, modules=[])}
    assert "semantic_layer" not in archetypes


def test_every_archetype_is_one_of_the_four_declared():
    for endpoint in serving.endpoints(FULL_OUTPUTS, modules=["cube-semantic-layer"]):
        assert endpoint["archetype"] in serving.ARCHETYPES


# --- On the spec card -----------------------------------------------------------------

@pytest.fixture
def served_run(workspace):
    run = runs.new_run(name="clickstream", domain="marketing", orchestrator="mwaa")
    root = workspace / "runs" / run["run_id"]
    (root / "terraform").mkdir(exist_ok=True)
    (root / "terraform" / "outputs.json").write_text(json.dumps(FULL_OUTPUTS),
                                                     encoding="utf-8")
    (root / "architecture_decision.json").write_text(json.dumps(
        {"selected_modules": ["query-athena", "consumption-redshift-serverless",
                              "dbt-semantic-layer"]}), encoding="utf-8")
    return run


def test_describe_shows_the_serving_section(served_run):
    _, output = _capture(["runs", "describe", served_run["run_id"]])

    assert "[Serving Endpoints & Consumption]" in output
    assert "jdbc:awsathena://" in output
    assert "redshift-serverless.amazonaws.com:5439" in output


def test_describe_omits_the_serving_section_when_nothing_is_served(workspace):
    """A run created but not yet applied has no outputs. An empty section header is noise."""
    run = runs.new_run(name="fresh", domain="ops")

    code, output = _capture(["runs", "describe", run["run_id"]])

    assert code == 0
    assert "[Serving Endpoints & Consumption]" not in output


def test_the_serving_section_is_ascii(served_run):
    _, output = _capture(["runs", "describe", served_run["run_id"]])
    assert all(ord(ch) < 0x2190 for ch in output)


# --- On export ------------------------------------------------------------------------

@pytest.fixture
def exportable(tmp_path):
    root = tmp_path / "runs" / "marketing-clickstream-mwaa_20260822_111530"
    (root / "terraform").mkdir(parents=True)
    (root / "terraform" / "main.tf").write_text('resource "aws_s3_bucket" "b" {}\n',
                                                encoding="utf-8")
    (root / "terraform" / "outputs.json").write_text(json.dumps(FULL_OUTPUTS),
                                                     encoding="utf-8")
    (root / "architecture_decision.json").write_text(json.dumps(
        {"selected_modules": ["query-athena", "dbt-semantic-layer"]}), encoding="utf-8")
    repo = tmp_path / "marketing-analytics"
    repo.mkdir()
    return str(root), repo


def test_export_writes_a_connections_file(exportable):
    run_root, repo = exportable
    export.export_run(run_root, str(repo), dest_dir="pipelines/clickstream")

    path = repo / "pipelines" / "clickstream" / "connections.yaml"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "jdbc:awsathena://" in text
    assert all(ord(ch) < 0x2190 for ch in text)


def test_export_writes_a_runnable_sample_query(exportable):
    """An analyst should be able to open the repo and run something. The query names the
    catalog database the stack actually created, not a placeholder."""
    run_root, repo = exportable
    export.export_run(run_root, str(repo), dest_dir="pipelines/clickstream")

    sql = (repo / "pipelines" / "clickstream" / "queries" / "sample_queries.sql").read_text(
        encoding="utf-8")
    assert "marketing_gold" in sql
    assert "SELECT" in sql.upper()


def test_the_connection_scaffold_carries_no_credentials(exportable):
    """These files land in a domain repo and are committed. Anything secret-shaped in them is
    a secret in git."""
    run_root, repo = exportable
    export.export_run(run_root, str(repo), dest_dir="pipelines/clickstream")

    text = (repo / "pipelines" / "clickstream" / "connections.yaml").read_text(
        encoding="utf-8")
    for forbidden in ("AKIA", "aws_secret_access_key", "password:", "SecretAccessKey"):
        assert forbidden not in text


def test_export_without_outputs_writes_no_connection_scaffold(tmp_path):
    """An un-applied run has no endpoints. A connections.yaml full of blanks would be
    committed once and never corrected."""
    root = tmp_path / "runs" / "unapplied"
    (root / "terraform").mkdir(parents=True)
    (root / "terraform" / "main.tf").write_text('resource "aws_s3_bucket" "b" {}\n',
                                                encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()

    export.export_run(str(root), str(repo), dest_dir="pipelines/x")

    assert not (repo / "pipelines" / "x" / "connections.yaml").exists()


def test_the_manifest_reports_the_scaffold_it_wrote(exportable):
    run_root, repo = exportable
    manifest = export.export_run(run_root, str(repo), dest_dir="pipelines/clickstream")

    copied = {p.replace("\\", "/") for p in manifest["copied"]}
    assert "pipelines/clickstream/connections.yaml" in copied
    assert "pipelines/clickstream/queries/sample_queries.sql" in copied
