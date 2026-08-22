"""
Run workspace identity and the central registry (PRD-ARCH-2026-005, FR-01 and FR-02).

A run id used to be `<timestamp>-<blueprint>`, which tells an operator staring at forty
directories nothing about which pipeline any of them builds. FR-01 makes the id semantic;
FR-02 makes the set of runs enumerable from one file instead of forty `run.json` reads.

Both changes are load-bearing for backward compatibility, which is most of what is tested
here: an existing run directory named the old way must keep resolving, and the registry must
never invent a cost figure for a run that has no cost evidence.

Depends on: core/reporting/runs.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import json
import re

import pytest

import runs


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    return tmp_path / "runs"


def test_get_run_by_prefix_returns_existing_run(workspace):
    run = runs.new_run(blueprint="requirements-first", request="create platform")

    found = runs.get_run(run["run_id"][:12])

    assert found["run_id"] == run["run_id"]


# --- FR-01: semantic naming -----------------------------------------------------------

def test_a_named_run_is_domain_workload_orchestrator_then_timestamp(workspace):
    run = runs.new_run(name="clickstream", domain="marketing", orchestrator="mwaa")

    # The stamp itself contains an underscore, so match the whole shape rather than
    # splitting on the last one.
    assert re.fullmatch(r"marketing-clickstream-mwaa_\d{8}_\d{6}", run["run_id"])


def test_an_unnamed_run_keeps_the_legacy_timestamp_blueprint_id(workspace):
    """Every existing caller passes only a blueprint. Changing their ids would orphan the
    run directories already on disk and every path recorded against them."""
    run = runs.new_run(blueprint="requirements-first")

    assert run["run_id"].endswith("-requirements-first")
    assert run["run_id"].split("-")[0].isdigit()


def test_absent_name_parts_collapse_rather_than_leaving_empty_segments(workspace):
    """`minusctl create --name x` supplies no domain. `--x-mwaa_...` would be a nonsense id
    and `--` is not a legal S3 prefix segment in half the places these ids land."""
    run = runs.new_run(name="clickstream", orchestrator="mwaa")

    assert run["run_id"].startswith("clickstream-mwaa_")


def test_a_name_with_shell_or_path_characters_is_slugged(workspace):
    """The id becomes a directory name. `../` in it walks out of runs/."""
    run = runs.new_run(name="../etc/passwd", domain="ops")

    assert ".." not in run["run_id"]
    assert "/" not in run["run_id"] and "\\" not in run["run_id"]


def test_semantic_metadata_is_recorded_on_the_run(workspace):
    run = runs.new_run(name="clickstream", domain="marketing", orchestrator="mwaa",
                       owner="marketing-data-eng@acme.com")

    saved = json.loads((workspace / run["run_id"] / "run.json").read_text(encoding="utf-8"))
    assert saved["domain"] == "marketing"
    assert saved["orchestrator"] == "mwaa"
    assert saved["owner"] == "marketing-data-eng@acme.com"


def test_legacy_and_semantic_runs_are_listed_side_by_side(workspace):
    legacy = runs.new_run(blueprint="requirements-first")
    semantic = runs.new_run(name="clickstream", domain="marketing", orchestrator="mwaa")

    listed = {item["run_id"] for item in runs.list_runs()}
    assert {legacy["run_id"], semantic["run_id"]} <= listed


# --- FR-02: the central registry ------------------------------------------------------

def test_creating_a_run_writes_the_registry(workspace):
    run = runs.new_run(name="clickstream", domain="marketing", orchestrator="mwaa")

    index = json.loads((workspace / "index.json").read_text(encoding="utf-8"))
    entry = next(item for item in index if item["run_name"] == run["run_id"])
    assert entry["domain"] == "marketing"
    assert entry["orchestrator"] == "mwaa"
    assert entry["path"].replace("\\", "/").endswith(run["run_id"])


def test_the_registry_never_invents_a_cost(workspace):
    """MinusOps reports cost only from BCM evidence. A registry column that defaulted to 0.0
    would read as "this pipeline is free" on the one page executives actually open."""
    runs.new_run(name="clickstream", domain="marketing")

    index = json.loads((workspace / "index.json").read_text(encoding="utf-8"))
    assert index[0]["estimated_monthly_cost"] is None


def test_the_markdown_registry_renders_a_row_per_run(workspace):
    runs.new_run(name="clickstream", domain="marketing", orchestrator="mwaa")
    runs.new_run(name="ledger", domain="finance", orchestrator="stepfunctions")

    text = (workspace / "INDEX.md").read_text(encoding="utf-8")
    assert "marketing-clickstream-mwaa_" in text
    assert "finance-ledger-stepfunctions_" in text
    assert text.count("\n|") >= 4  # header, separator, two rows


def test_the_markdown_registry_links_to_each_run(workspace):
    run = runs.new_run(name="clickstream", domain="marketing")

    text = (workspace / "INDEX.md").read_text(encoding="utf-8")
    assert f"({run['run_id']}/)" in text


def test_a_run_directory_removed_by_hand_drops_out_of_the_registry(workspace):
    """Operators delete run directories. A registry that still advertises them sends the
    next reader to a path that does not exist."""
    import shutil
    keep = runs.new_run(name="keep", domain="ops")
    gone = runs.new_run(name="gone", domain="ops")
    shutil.rmtree(workspace / gone["run_id"])

    entries = runs.sync_index()

    names = {item["run_name"] for item in entries}
    assert keep["run_id"] in names and gone["run_id"] not in names


def test_the_registry_is_replaced_atomically_never_truncated(workspace, monkeypatch):
    """Two runs created in parallel both rewrite this file. A reader that catches a
    half-written index gets a JSONDecodeError, not a stale-but-valid list."""
    runs.new_run(name="first", domain="ops")
    seen = []

    real_replace = runs.os.replace

    def _spy(src, dst):
        if str(dst).endswith("index.json"):
            seen.append(json.loads(open(src, encoding="utf-8").read()))
        return real_replace(src, dst)

    monkeypatch.setattr(runs.os, "replace", _spy)
    runs.new_run(name="second", domain="ops")

    assert seen, "index.json must be swapped into place, not written in situ"
    assert len(seen[-1]) == 2


def test_the_registry_survives_an_unreadable_run_json(workspace):
    """One corrupt run must not make the whole registry unbuildable."""
    good = runs.new_run(name="good", domain="ops")
    bad = runs.new_run(name="bad", domain="ops")
    (workspace / bad["run_id"] / "run.json").write_text("{not json", encoding="utf-8")

    names = {item["run_name"] for item in runs.sync_index()}
    assert good["run_id"] in names and bad["run_id"] not in names
