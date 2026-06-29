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
