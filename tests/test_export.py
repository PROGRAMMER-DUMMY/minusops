"""
Multi-repo export and per-pipeline CI isolation (PRD-ARCH-2026-005, FR-03 and FR-04).

Export is the moment MinusOps stops owning the code. What lands in the domain repository has
to run under plain `terraform init && terraform apply` with no MinusOps runtime present
(NFR-01), authenticate through OIDC rather than a static key (NFR-02), and leave an audit
entry behind (NFR-03).

The path handling is the part with teeth. `--dest-dir` is operator-supplied and is joined
onto a repository root, so `../../` in it writes outside the repository the operator named.

Depends on: core/reporting/export.py, core/generation/cicd.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import json
import os

import pytest

import cicd
import export


@pytest.fixture
def run(tmp_path):
    """A minimal generated run workspace: the four directories export packages."""
    root = tmp_path / "runs" / "marketing-clickstream-mwaa_20260822_111530"
    for sub, name, body in (
        ("terraform", "main.tf", 'resource "aws_s3_bucket" "bronze" {\n  bucket = "b"\n}\n'),
        ("dags", "data_pipeline_dag.py", "# airflow dag\n"),
        ("scripts", "etl.py", "# pyspark\n"),
        ("configs", "connections.yaml", "warehouse: acme\n"),
        ("reports", "plan-report.json", "{}\n"),
    ):
        (root / sub).mkdir(parents=True)
        (root / sub / name).write_text(body, encoding="utf-8")
    (root / "run.json").write_text(json.dumps({
        "run_id": root.name, "root": str(root),
        "terraform_dir": str(root / "terraform"), "domain": "marketing",
        "orchestrator": "mwaa"}), encoding="utf-8")
    return root


@pytest.fixture
def target_repo(tmp_path):
    repo = tmp_path / "marketing-analytics"
    repo.mkdir()
    return repo


# --- FR-03: what lands in the domain repository ---------------------------------------

def test_export_copies_the_four_deployable_directories(run, target_repo):
    export.export_run(str(run), str(target_repo), dest_dir="pipelines/clickstream")

    base = target_repo / "pipelines" / "clickstream"
    assert (base / "terraform" / "main.tf").exists()
    assert (base / "dags" / "data_pipeline_dag.py").exists()
    assert (base / "scripts" / "etl.py").exists()
    assert (base / "configs" / "connections.yaml").exists()


def test_export_leaves_minusops_internals_behind(run, target_repo):
    """The domain team owns the result. Shipping our reports/ and run.json couples them to
    a control plane they do not run and cannot regenerate."""
    export.export_run(str(run), str(target_repo), dest_dir="pipelines/clickstream")

    base = target_repo / "pipelines" / "clickstream"
    assert not (base / "reports").exists()
    assert not (base / "run.json").exists()


def test_export_reports_exactly_what_it_wrote(run, target_repo):
    manifest = export.export_run(str(run), str(target_repo), dest_dir="pipelines/clickstream")

    copied = {p.replace("\\", "/") for p in manifest["copied"]}
    assert "pipelines/clickstream/terraform/main.tf" in copied
    assert manifest["pipeline_name"] == "clickstream"


def test_a_run_with_no_terraform_is_refused(tmp_path, target_repo):
    """An un-synthesized run exports an empty directory and a workflow that plans nothing.
    Better to say so than to hand the domain team a pipeline-shaped hole."""
    empty = tmp_path / "runs" / "empty_20260822_111530"
    empty.mkdir(parents=True)
    with pytest.raises(ValueError):
        export.export_run(str(empty), str(target_repo), dest_dir="pipelines/x")


def test_a_missing_target_repo_is_refused(run, tmp_path):
    with pytest.raises(ValueError):
        export.export_run(str(run), str(tmp_path / "no-such-repo"), dest_dir="pipelines/x")


@pytest.mark.parametrize("escape", ["../outside", "pipelines/../../outside",
                                    "pipelines/./../.."])
def test_a_dest_dir_that_escapes_the_target_repo_is_refused(run, target_repo, escape):
    """`--dest-dir` is typed by an operator and joined onto a repo root. A traversal here
    writes generated Terraform into whatever directory sits beside the repository."""
    with pytest.raises(ValueError):
        export.export_run(str(run), str(target_repo), dest_dir=escape)


def test_an_absolute_dest_dir_is_refused(run, target_repo, tmp_path):
    """os.path.join discards the repo root entirely when the second argument is absolute."""
    with pytest.raises(ValueError):
        export.export_run(str(run), str(target_repo), dest_dir=str(tmp_path / "elsewhere"))


def test_export_appends_an_audit_entry(run, target_repo, tmp_path, monkeypatch):
    """NFR-03. An export moves infrastructure code into a repository that can deploy it;
    an unrecorded one is a deployment nobody can trace back to a decision."""
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(export, "AUDIT_DIR", str(log_dir))

    export.export_run(str(run), str(target_repo), dest_dir="pipelines/clickstream")

    entries = [json.loads(line) for line in
               (log_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(e["action"] == "export" for e in entries)


def test_re_exporting_replaces_rather_than_merging_stale_files(run, target_repo):
    """A resource removed from the run must disappear from the domain repo. A merge leaves
    an orphaned .tf file that `terraform apply` will happily still create."""
    export.export_run(str(run), str(target_repo), dest_dir="pipelines/clickstream")
    stale = target_repo / "pipelines" / "clickstream" / "terraform" / "old.tf"
    stale.write_text("# removed upstream\n", encoding="utf-8")

    export.export_run(str(run), str(target_repo), dest_dir="pipelines/clickstream")

    assert not stale.exists()


def test_export_does_not_touch_sibling_pipelines(run, target_repo):
    sibling = target_repo / "pipelines" / "customer_360" / "terraform"
    sibling.mkdir(parents=True)
    (sibling / "main.tf").write_text("# untouched\n", encoding="utf-8")

    export.export_run(str(run), str(target_repo), dest_dir="pipelines/clickstream")

    assert (sibling / "main.tf").read_text(encoding="utf-8") == "# untouched\n"


# --- FR-04: the tailored workflow -----------------------------------------------------

def test_the_generated_workflow_lands_in_dot_github_workflows(run, target_repo):
    manifest = export.export_run(str(run), str(target_repo), dest_dir="pipelines/clickstream",
                                 generate_workflow=True)

    path = target_repo / ".github" / "workflows" / "clickstream-deploy.yml"
    assert path.exists()
    assert manifest["workflow"].replace("\\", "/").endswith(
        ".github/workflows/clickstream-deploy.yml")


def test_no_workflow_is_written_unless_asked(run, target_repo):
    """A domain repo may already own its CI. Overwriting it on every export is not a
    packaging step, it is a takeover."""
    export.export_run(str(run), str(target_repo), dest_dir="pipelines/clickstream")

    assert not (target_repo / ".github").exists()


def test_the_workflow_only_triggers_on_its_own_pipeline_path():
    """AC-04. In a repo with eight pipelines, an unfiltered workflow means one commit runs
    eight plans against eight state files."""
    text = cicd.render_pipeline_workflow("clickstream", dest_dir="pipelines/clickstream")

    assert "paths:" in text
    assert "'pipelines/clickstream/**'" in text
    assert "'.github/workflows/clickstream-deploy.yml'" in text


def test_the_workflow_authenticates_by_oidc_and_carries_no_static_key():
    """NFR-02. A generated file goes straight into git; a placeholder AKIA is a placeholder
    until someone fills it in."""
    text = cicd.render_pipeline_workflow("clickstream", dest_dir="pipelines/clickstream")

    assert "id-token: write" in text
    assert "aws-actions/configure-aws-credentials" in text
    assert "AKIA" not in text
    assert "aws-secret-access-key" not in text


def test_the_workflow_runs_terraform_in_the_exported_directory():
    text = cicd.render_pipeline_workflow("clickstream", dest_dir="pipelines/clickstream")

    assert "pipelines/clickstream/terraform" in text


def test_the_workflow_promotes_through_the_four_tiers():
    """dev -> test -> uat -> prod is the lifecycle the PRD governs; a workflow wired only to
    main cannot express it."""
    text = cicd.render_pipeline_workflow("clickstream", dest_dir="pipelines/clickstream")

    for branch in ("main", "dev", "uat"):
        assert branch in text


def test_the_workflow_plans_on_pull_request_and_applies_only_on_push():
    """An apply triggered by a fork's pull request is arbitrary code execution against the
    account."""
    text = cicd.render_pipeline_workflow("clickstream", dest_dir="pipelines/clickstream")

    assert "terraform plan" in text
    assert any("terraform apply" in line for line in text.splitlines())
    assert "github.event_name == 'push'" in text


def test_a_pipeline_name_with_a_slash_cannot_forge_a_workflow_path(run, target_repo):
    """The pipeline name becomes a filename under .github/workflows/."""
    with pytest.raises(ValueError):
        export.export_run(str(run), str(target_repo), dest_dir="pipelines/clickstream",
                          generate_workflow=True, pipeline_name="../../evil")


def test_the_workflow_is_valid_yaml():
    yaml = pytest.importorskip("yaml")
    text = cicd.render_pipeline_workflow("clickstream", dest_dir="pipelines/clickstream")

    doc = yaml.safe_load(text)
    assert doc["jobs"]
    assert os.path.sep not in doc["name"]
