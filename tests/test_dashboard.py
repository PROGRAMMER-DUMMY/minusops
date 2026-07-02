"""
The dashboard must surface the optimization findings the engine detects (per run),
so "we provide cost optimization" is visible on the console, not buried in a report.
"""
import os

import dashboard_app


def test_collect_optimization_findings_surfaces_findings(tmp_path, monkeypatch):
    tf = tmp_path / "tf"
    tf.mkdir()
    # A bucket with no public-access-block and no lifecycle → SEC-01 + COST-01.
    (tf / "main.tf").write_text('resource "aws_s3_bucket" "b" { bucket = "x" }', encoding="utf-8")
    run = {"run_id": "20260101-000000-demo", "terraform_dir": str(tf)}
    monkeypatch.setattr(dashboard_app.run_store, "list_runs", lambda: [run])

    findings = dashboard_app.collect_optimization_findings()
    ids = {f["id"] for f in findings}

    assert "SEC-01" in ids
    assert "COST-01" in ids
    assert all(f["run_id"] == "20260101-000000-demo" for f in findings)


def test_collect_optimization_findings_clean_run_is_empty(tmp_path, monkeypatch):
    tf = tmp_path / "tf"
    tf.mkdir()
    (tf / "main.tf").write_text(
        'resource "aws_s3_bucket" "b" { bucket = "x" }\n'
        'resource "aws_s3_bucket_public_access_block" "b" { bucket = aws_s3_bucket.b.id }\n'
        'resource "aws_s3_bucket_lifecycle_configuration" "b" { bucket = aws_s3_bucket.b.id }\n'
        'resource "aws_cloudwatch_metric_alarm" "a" { alarm_name = "x" }\n',
        encoding="utf-8")
    monkeypatch.setattr(dashboard_app.run_store, "list_runs",
                        lambda: [{"run_id": "r", "terraform_dir": str(tf)}])
    assert dashboard_app.collect_optimization_findings() == []


def test_optimization_panels_render_per_category(tmp_path, monkeypatch):
    tf = tmp_path / "tf"
    tf.mkdir()
    (tf / "main.tf").write_text('resource "aws_s3_bucket" "b" { bucket = "x" }', encoding="utf-8")
    monkeypatch.setattr(dashboard_app.run_store, "list_runs",
                        lambda: [{"run_id": "r", "terraform_dir": str(tf)}])
    panels = dashboard_app.optimization_panels()
    assert len(panels) >= 1  # at least Cost + Security panels for this input


def test_control_plane_panel_surfaces_decision_gate(tmp_path, monkeypatch):
    root = tmp_path / "runs" / "20260101-000000-requirements-first"
    tf = root / "terraform"
    tf.mkdir(parents=True)
    (root / "run.json").write_text("{}", encoding="utf-8")
    (root / "requirements.json").write_text('{"goal":"x"}', encoding="utf-8")
    run = {
        "run_id": "20260101-000000-requirements-first",
        "root": str(root),
        "terraform_dir": str(tf),
        "reports_dir": str(root / "reports"),
        "blueprint": "requirements-first",
        "cloud": "aws",
        "request": "create a governed platform",
    }
    monkeypatch.setattr(dashboard_app.run_store, "list_runs", lambda: [run])
    monkeypatch.setattr(dashboard_app.minusctl, "_readiness", lambda item: {
        "status": "NEEDS_REQUIREMENTS",
        "score": 25,
        "blockers": [],
        "warnings": [],
        "reports": [],
        "latest_report": {},
        "source": {"status": "PRE_GENERATION"},
    })

    rendered = str(dashboard_app.control_plane_panel())

    assert "architecture_decision.py check" in rendered
    assert "control-save-decision-btn" in rendered
    assert "control-accelerator-btn" in rendered
    assert "--run 20260101-000000-requirements-first" in rendered
    assert "--decision-file" in rendered
    assert "--policy-mode production" in rendered
    assert "requirements.json" in rendered


def test_control_decision_writer_saves_complete_record(tmp_path):
    root = tmp_path / "runs" / "r1"
    root.mkdir(parents=True)
    run = {"run_id": "r1", "root": str(root), "terraform_dir": str(root / "terraform")}

    result = dashboard_app.write_control_decision(
        run,
        selected_architecture="AWS governed lakehouse",
        decision_summary="Chosen for batch analytics with governed storage and query access.",
        modules_text="storage-medallion-s3\nquery-athena\ngovernance-observability",
        sources_text="https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket",
        assumptions_text="AWS is approved.",
        risks_text="BCM pricing must be run before publishing totals.",
        alternatives_text="Redshift warehouse | rejected | Lake-first storage was required.",
    )

    assert result["ok"] is True
    assert os.path.exists(result["path"])
    assert result["record"]["selected_modules"] == [
        "storage-medallion-s3",
        "query-athena",
        "governance-observability",
    ]


def test_dashboard_allows_localhost_without_token(monkeypatch):
    monkeypatch.delenv("MINUS_DASH_TOKEN", raising=False)
    monkeypatch.delenv("DASH_TOKEN", raising=False)

    assert dashboard_app._is_loopback_host("127.0.0.1")
    assert dashboard_app._is_loopback_host("localhost")
    assert not dashboard_app._remote_bind_requires_token("127.0.0.1")


def test_dashboard_refuses_remote_bind_without_token(monkeypatch):
    monkeypatch.delenv("MINUS_DASH_TOKEN", raising=False)
    monkeypatch.delenv("DASH_TOKEN", raising=False)

    assert dashboard_app._remote_bind_requires_token("0.0.0.0")
    assert dashboard_app._remote_bind_requires_token("10.0.0.5")


def test_dashboard_remote_bind_allowed_with_token(monkeypatch):
    monkeypatch.setenv("MINUS_DASH_TOKEN", "secret-token")

    assert not dashboard_app._remote_bind_requires_token("0.0.0.0")


def test_dashboard_token_auth(monkeypatch):
    monkeypatch.setenv("MINUS_DASH_TOKEN", "secret-token")
    client = dashboard_app.app.server.test_client()

    assert client.get("/").status_code == 401
    assert client.get("/", headers={"Authorization": "Bearer secret-token"}).status_code != 401
    assert client.get("/?token=secret-token").status_code != 401
