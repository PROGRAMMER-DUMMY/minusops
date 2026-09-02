"""
Promotion refuses every run that cannot show its work.

Four of the five tests are refusals: no run, no plan, no proving report without an explicit
skip. A pattern registry is reused by later runs, so a composition promoted without evidence
propagates -- the failure is not this run, it is the three that copy it.

Depends on: core/generation/git_agent.py, core/generation/patterns.py
Shells out to: nothing (git and gh are stubbed)
Used by: nothing (pytest entry point)
"""
import pytest
import json
import git_agent
import patterns


def test_promote_pattern_fails_when_run_does_not_exist(tmp_path):
    with pytest.raises(ValueError, match="Run workspace not found"):
        git_agent.create_pattern_pull_request(
            run_root=str(tmp_path / "non_existent"),
            pattern_name="test-pattern"
        )


def test_promote_pattern_fails_when_run_has_no_plan(tmp_path):
    run_dir = tmp_path / "run_no_plan"
    run_dir.mkdir()
    with pytest.raises(ValueError, match="Cannot promote an unplanned run"):
        git_agent.create_pattern_pull_request(
            run_root=str(run_dir),
            pattern_name="test-pattern"
        )


def test_promote_pattern_fails_when_no_proving_report_and_not_skipped(tmp_path):
    run_dir = tmp_path / "run_no_proof"
    run_dir.mkdir()
    (run_dir / "reports").mkdir()
    (run_dir / "reports" / "plan.json").write_text(json.dumps({"plan_hash": "abc123hash"}), encoding="utf-8")

    with pytest.raises(ValueError, match="Cannot promote pattern without UAT synthetic data verification"):
        git_agent.create_pattern_pull_request(
            run_root=str(run_dir),
            pattern_name="test-pattern",
            skip_proof=False
        )


def test_promote_pattern_succeeds_with_proving_report(tmp_path):
    run_dir = tmp_path / "run_valid"
    run_dir.mkdir()
    (run_dir / "reports").mkdir()
    (run_dir / "reports" / "plan.json").write_text(json.dumps({"plan_hash": "abc123hash"}), encoding="utf-8")
    (run_dir / "reports" / "proving_report.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
    (run_dir / "architecture_decision.json").write_text(json.dumps({
        "selected_architecture": "AWS Medallion Lakehouse",
        "selected_modules": ["storage-medallion-s3", "compute-glue-etl"]
    }), encoding="utf-8")

    result = git_agent.create_pattern_pull_request(
        run_root=str(run_dir),
        pattern_name="lakehouse-streaming",
        description="Production AWS Lakehouse"
    )

    assert result["ok"] is True
    assert result["branch"] == "pattern/add-lakehouse-streaming"
    assert "feat(patterns): promote approved pattern 'lakehouse-streaming'" in result["pr_title"]
    assert result["plan_hash"] == "abc123hash"
    assert result["proving_status"] == "PASS"
    assert "Draw.io" in result["pr_body"]


def test_patterns_promote_function_captures_to_local_registry(tmp_path):
    run_dir = tmp_path / "run_valid2"
    run_dir.mkdir()
    (run_dir / "reports").mkdir()
    (run_dir / "reports" / "plan.json").write_text(json.dumps({"plan_hash": "def456hash"}), encoding="utf-8")
    (run_dir / "architecture_decision.json").write_text(json.dumps({
        "decision_summary": "Glue ETL on S3",
        "selected_modules": ["storage-medallion-s3"]
    }), encoding="utf-8")

    res = patterns.promote_pattern(
        run_root=str(run_dir),
        name="glue-s3-pattern",
        description="Glue ETL on S3",
        skip_proof=True
    )

    assert res["ok"] is True
    assert res["branch"] == "pattern/add-glue-s3-pattern"
    assert res["proving_status"] == "BYPASS"
