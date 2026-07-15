"""
schema_watch.py fetches a real `terraform providers schema -json`, reduces it to the
resource/data types MinusOps' modules actually reference, and diffs it against the last
committed snapshot. Most of this is tested with synthetic schema fixtures (fast, hermetic,
no network) -- one real-terraform test at the bottom proves the actual fetch/parse mechanism
works against the live AWS provider, matching the rigor of test_databricks_workspace_module.py.
"""
import json
import os

import pytest

import modules as module_registry
import schema_watch
import toolpath

TERRAFORM = toolpath.find_tool("terraform")


# ---------------------------------------------------------------------------
# used_types(): parsing modules/*/main.tf
# ---------------------------------------------------------------------------

def test_used_types_parses_resource_and_data_blocks(tmp_path):
    module_dir = tmp_path / "foo"
    module_dir.mkdir()
    (module_dir / "main.tf").write_text(
        'resource "aws_s3_bucket" "x" {}\n'
        'data "aws_iam_policy_document" "y" {}\n'
        'resource "databricks_catalog" "z" {}\n',
        encoding="utf-8",
    )

    aws_used = schema_watch.used_types(str(tmp_path), "aws")
    databricks_used = schema_watch.used_types(str(tmp_path), "databricks")

    assert aws_used == {("resource", "aws_s3_bucket"), ("data", "aws_iam_policy_document")}
    assert databricks_used == {("resource", "databricks_catalog")}


def test_used_types_scans_every_module_dir(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "main.tf").write_text('resource "aws_vpc" "v" {}\n', encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "main.tf").write_text('resource "aws_subnet" "s" {}\n', encoding="utf-8")

    used = schema_watch.used_types(str(tmp_path), "aws")

    assert used == {("resource", "aws_vpc"), ("resource", "aws_subnet")}


# ---------------------------------------------------------------------------
# _reduce(): extracting version + deprecated attributes from a schema
# ---------------------------------------------------------------------------

def test_reduce_extracts_version_and_top_level_deprecated_attr():
    schema = {
        "resource_schemas": {
            "aws_s3_bucket": {
                "version": 0,
                "block": {
                    "attributes": {
                        "acl": {"deprecated": True, "deprecation_message": "use aws_s3_bucket_acl"},
                        "bucket": {"type": "string"},
                    },
                    "block_types": {},
                },
            }
        },
        "data_source_schemas": {},
    }

    reduced = schema_watch._reduce(schema, {("resource", "aws_s3_bucket")})

    assert reduced["resource:aws_s3_bucket"]["version"] == 0
    assert reduced["resource:aws_s3_bucket"]["deprecated_attributes"] == ["acl"]


def test_reduce_recurses_into_nested_block_types():
    schema = {
        "resource_schemas": {
            "aws_s3_bucket": {
                "version": 0,
                "block": {
                    "attributes": {},
                    "block_types": {
                        "versioning": {
                            "nesting_mode": "list",
                            "block": {
                                "attributes": {
                                    "enabled": {"type": "bool"},
                                    "mfa_delete": {"deprecated": True, "deprecation_message": "x"},
                                },
                                "block_types": {},
                            },
                        }
                    },
                },
            }
        },
        "data_source_schemas": {},
    }

    reduced = schema_watch._reduce(schema, {("resource", "aws_s3_bucket")})

    assert reduced["resource:aws_s3_bucket"]["deprecated_attributes"] == ["versioning.mfa_delete"]


def test_reduce_skips_types_absent_from_the_live_schema():
    schema = {"resource_schemas": {}, "data_source_schemas": {}}

    reduced = schema_watch._reduce(schema, {("resource", "aws_ghost_resource")})

    assert reduced == {}


def test_reduce_handles_data_sources_via_the_right_table():
    schema = {
        "resource_schemas": {},
        "data_source_schemas": {
            "aws_caller_identity": {"version": 0, "block": {"attributes": {}, "block_types": {}}},
        },
    }

    reduced = schema_watch._reduce(schema, {("data", "aws_caller_identity")})

    assert reduced["data:aws_caller_identity"]["kind"] == "data"


# ---------------------------------------------------------------------------
# _diff(): finding removed / schema_version_bump / deprecated, scoped to still-used types
# ---------------------------------------------------------------------------

def _entry(version=0, deprecated=None):
    return {"kind": "resource", "version": version, "deprecated_attributes": deprecated or []}


def test_diff_no_baseline_returns_no_findings():
    findings = schema_watch._diff(None, {"resource:aws_s3_bucket": _entry()}, {"resource:aws_s3_bucket"})
    assert findings == []


def test_diff_detects_removed_type():
    old_snapshot = {"resource_types": {"resource:aws_s3_bucket": _entry()}}
    findings = schema_watch._diff(old_snapshot, {}, {"resource:aws_s3_bucket"})
    assert findings == [{"finding": "removed", "type": "resource:aws_s3_bucket",
                          "detail": "no longer present in the live provider schema"}]


def test_diff_detects_schema_version_bump():
    old_snapshot = {"resource_types": {"resource:aws_vpc": _entry(version=1)}}
    reduced = {"resource:aws_vpc": _entry(version=2)}
    findings = schema_watch._diff(old_snapshot, reduced, {"resource:aws_vpc"})
    assert findings == [{"finding": "schema_version_bump", "type": "resource:aws_vpc",
                          "old_version": 1, "new_version": 2}]


def test_diff_detects_newly_deprecated_attribute():
    old_snapshot = {"resource_types": {"resource:aws_s3_bucket": _entry(deprecated=[])}}
    reduced = {"resource:aws_s3_bucket": _entry(deprecated=["acl"])}
    findings = schema_watch._diff(old_snapshot, reduced, {"resource:aws_s3_bucket"})
    assert findings == [{"finding": "deprecated", "type": "resource:aws_s3_bucket", "attributes": ["acl"]}]


def test_diff_ignores_types_no_longer_used_even_if_they_changed():
    old_snapshot = {"resource_types": {"resource:aws_vpc": _entry(version=1)}}
    reduced = {"resource:aws_vpc": _entry(version=2)}
    # aws_vpc changed, but no module references it any more -- not tracked, not a finding.
    findings = schema_watch._diff(old_snapshot, reduced, set())
    assert findings == []


def test_diff_ignores_a_used_type_with_no_prior_history():
    # First time this type is tracked (not in old_snapshot yet) -- nothing to diff against.
    old_snapshot = {"resource_types": {}}
    reduced = {"resource:aws_vpc": _entry(version=1)}
    findings = schema_watch._diff(old_snapshot, reduced, {"resource:aws_vpc"})
    assert findings == []


def test_diff_reports_multiple_findings_for_the_same_type():
    old_snapshot = {"resource_types": {"resource:aws_s3_bucket": _entry(version=0, deprecated=[])}}
    reduced = {"resource:aws_s3_bucket": _entry(version=1, deprecated=["acl"])}
    findings = schema_watch._diff(old_snapshot, reduced, {"resource:aws_s3_bucket"})
    kinds = {f["finding"] for f in findings}
    assert kinds == {"schema_version_bump", "deprecated"}


# ---------------------------------------------------------------------------
# _new_resources_of_interest(): informational only, never a finding
# ---------------------------------------------------------------------------

def test_new_resources_of_interest_requires_a_baseline():
    schema = {"resource_schemas": {"aws_lakeformation_permissions": {}}, "data_source_schemas": {}}
    result = schema_watch._new_resources_of_interest(schema, set(), {"lakeformation"}, None)
    assert result == []


def test_new_resources_of_interest_matches_vocab_and_excludes_seen():
    schema = {
        "resource_schemas": {
            "aws_lakeformation_permissions": {},  # matches vocab, never seen -> interesting
            "aws_zzz_unrelated_thing": {},        # no vocab overlap -> not interesting
        },
        "data_source_schemas": {},
    }
    old_snapshot = {"all_type_names": []}
    result = schema_watch._new_resources_of_interest(
        schema, used_keys=set(), vocab_tokens={"lakeformation"}, old_snapshot=old_snapshot)
    assert result == ["aws_lakeformation_permissions"]


def test_new_resources_of_interest_skips_already_seen_types():
    schema = {"resource_schemas": {"aws_lakeformation_permissions": {}}, "data_source_schemas": {}}
    old_snapshot = {"all_type_names": ["aws_lakeformation_permissions"]}
    result = schema_watch._new_resources_of_interest(
        schema, used_keys=set(), vocab_tokens={"lakeformation"}, old_snapshot=old_snapshot)
    assert result == []


def test_new_resources_of_interest_skips_already_used_types():
    schema = {"resource_schemas": {"aws_lakeformation_permissions": {}}, "data_source_schemas": {}}
    old_snapshot = {"all_type_names": []}
    result = schema_watch._new_resources_of_interest(
        schema, used_keys={"resource:aws_lakeformation_permissions"},
        vocab_tokens={"lakeformation"}, old_snapshot=old_snapshot)
    assert result == []


def test_vocab_tokens_is_derived_from_the_real_module_registry():
    tokens = schema_watch._vocab_tokens()
    # Spot-check a couple of words that are definitely in modules.py's satisfies/services lists.
    assert "glue" in tokens
    assert "athena" in tokens


# ---------------------------------------------------------------------------
# _version_constraint(): one source of truth, read from synthesizer.py's own templates
# ---------------------------------------------------------------------------

def test_version_constraint_matches_synthesizer():
    assert schema_watch._version_constraint("aws") == ">= 5.0"
    assert schema_watch._version_constraint("databricks") == ">= 1.0"


# ---------------------------------------------------------------------------
# run_provider(): end-to-end with a stubbed _fetch_schema (no network)
# ---------------------------------------------------------------------------

def _stub_schema(version=0, deprecated=None):
    return {
        "resource_schemas": {
            "aws_s3_bucket": {
                "version": version,
                "block": {"attributes": {
                    n: {"deprecated": True, "deprecation_message": "x"} for n in (deprecated or [])
                }, "block_types": {}},
            }
        },
        "data_source_schemas": {},
    }


@pytest.fixture
def fake_modules(tmp_path, monkeypatch):
    modules_dir = tmp_path / "modules"
    (modules_dir / "storage").mkdir(parents=True)
    (modules_dir / "storage" / "main.tf").write_text(
        'resource "aws_s3_bucket" "b" {}\n', encoding="utf-8")
    monkeypatch.setattr(module_registry, "MODULES_DIR", str(modules_dir))
    return modules_dir


def test_run_provider_first_run_seeds_snapshot_only(tmp_path, fake_modules, monkeypatch):
    monkeypatch.setattr(schema_watch, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))

    findings, new_of_interest = schema_watch.run_provider(
        "aws", recent_changes_dir=str(tmp_path / "rc"), log_dir=str(tmp_path / "logs"))

    assert findings == []
    assert new_of_interest == []
    snapshot_path = tmp_path / "rc" / "aws" / "schema-snapshot.json"
    assert snapshot_path.exists()
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["resolved_version"] == "6.54.0"
    assert "resource:aws_s3_bucket" in snapshot["resource_types"]
    # first run: no prior snapshot to diff against, so no timestamped report is written
    other_files = [p for p in (tmp_path / "rc" / "aws").iterdir() if p.name != "schema-snapshot.json"]
    assert other_files == []


def test_run_provider_second_run_detects_version_bump(tmp_path, fake_modules, monkeypatch):
    calls = {"n": 0}

    def fake_fetch(provider, workdir):
        calls["n"] += 1
        version = 0 if calls["n"] == 1 else 1
        return _stub_schema(version=version), "6.54.0"

    monkeypatch.setattr(schema_watch, "_fetch_schema", fake_fetch)
    rc_dir = str(tmp_path / "rc")
    log_dir = str(tmp_path / "logs")

    schema_watch.run_provider("aws", recent_changes_dir=rc_dir, log_dir=log_dir)
    findings, new_of_interest = schema_watch.run_provider("aws", recent_changes_dir=rc_dir, log_dir=log_dir)

    assert findings == [{"finding": "schema_version_bump", "type": "resource:aws_s3_bucket",
                          "old_version": 0, "new_version": 1}]
    reports = [p for p in (tmp_path / "rc" / "aws").iterdir() if p.name != "schema-snapshot.json"]
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["findings"] == findings


def test_main_exits_1_from_a_real_diff_not_a_stubbed_run_provider(tmp_path, fake_modules, monkeypatch):
    """Closes the one gap the tests above leave: test_cli_run_exits_1_on_findings proves main()
    maps findings -> exit 1, but with run_provider stubbed -- decoupled from the real diff.
    test_run_provider_second_run_detects_version_bump proves the real diff produces a finding,
    but calls run_provider() directly, not main(). This drives the *actual* CLI entrypoint end
    to end: only the network fetch is stubbed, everything else (used-type extraction, reduce,
    diff, snapshot/report I/O, audit-chain append, and finally main()'s exit-code decision) is
    the real, unstubbed code path `minus-schema-watch` runs in production."""
    monkeypatch.setattr(module_registry, "output_root", lambda: str(tmp_path))
    calls = {"n": 0}

    def fake_fetch(provider, workdir):
        calls["n"] += 1
        version = 0 if calls["n"] == 1 else 1
        return _stub_schema(version=version), "6.54.0"

    monkeypatch.setattr(schema_watch, "_fetch_schema", fake_fetch)

    first_rc = schema_watch.main(["run", "--provider", "aws"])
    second_rc = schema_watch.main(["run", "--provider", "aws"])

    assert first_rc == 0   # first run: no baseline yet, nothing to diff against
    assert second_rc == 1  # second run: a real schema_version_bump was actually detected


def test_run_provider_writes_an_audit_chain_entry(tmp_path, fake_modules, monkeypatch):
    monkeypatch.setattr(schema_watch, "_fetch_schema", lambda provider, workdir: (_stub_schema(), "6.54.0"))
    log_dir = str(tmp_path / "logs")

    schema_watch.run_provider("aws", recent_changes_dir=str(tmp_path / "rc"), log_dir=log_dir)

    audit_path = os.path.join(log_dir, "audit.jsonl")
    assert os.path.exists(audit_path)
    with open(audit_path, encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert lines[-1]["action"] == "schema_watch"
    assert lines[-1]["details"]["provider"] == "aws"
    assert "entry_hash" in lines[-1]  # tied into the same hash-chained log audit_chain.py uses


def test_run_provider_rejects_unknown_provider():
    with pytest.raises(ValueError):
        schema_watch.run_provider("azure")


# ---------------------------------------------------------------------------
# CLI (main())
# ---------------------------------------------------------------------------

def test_cli_run_requires_provider_or_all(capsys):
    rc = schema_watch.main(["run"])
    assert rc == 1
    assert "--provider" in capsys.readouterr().err


def test_cli_run_exits_1_on_findings(monkeypatch):
    monkeypatch.setattr(schema_watch, "run_provider",
                         lambda provider, **kw: ([{"finding": "removed", "type": "x"}], []))
    assert schema_watch.main(["run", "--provider", "aws"]) == 1


def test_cli_run_exits_0_when_clean(monkeypatch):
    monkeypatch.setattr(schema_watch, "run_provider", lambda provider, **kw: ([], []))
    assert schema_watch.main(["run", "--provider", "aws"]) == 0


def test_cli_run_all_covers_every_tracked_provider(monkeypatch):
    seen = []
    monkeypatch.setattr(schema_watch, "run_provider",
                         lambda provider, **kw: (seen.append(provider), ([], []))[1])
    schema_watch.main(["run", "--all"])
    assert set(seen) == set(schema_watch._PROVIDER_PREFIX)


def test_cli_run_returns_1_on_fetch_failure(monkeypatch, capsys):
    def boom(provider, **kw):
        raise RuntimeError("terraform init failed")
    monkeypatch.setattr(schema_watch, "run_provider", boom)
    rc = schema_watch.main(["run", "--provider", "aws"])
    assert rc == 1
    assert "FAILED" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Real terraform integration (skipped if terraform isn't installed) -- proves the actual
# `terraform providers schema -json` call parses and resolves against the live AWS provider.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_fetch_schema_real_aws_contains_known_types(tmp_path):
    schema, resolved_version = schema_watch._fetch_schema("aws", str(tmp_path / "wd"))

    assert resolved_version is not None
    assert "aws_vpc" in schema["resource_schemas"]
    assert "aws_s3_bucket" in schema["resource_schemas"]


# ---------------------------------------------------------------------------
# get_type_schema() (Phase 7 Item 4, docs/phase7_generation_engine_plan.md): the thin per-type
# live schema query composing _fetch_schema() with a plain dict lookup -- deliberately NOT
# _reduce(), which strips to {kind, version, deprecated_attributes} for drift comparison and
# would throw away the attribute detail this function exists to expose. The resource-vs-data
# branch is the only real logic in the function, so it's the one thing all three tests below
# are actually checking, not just re-proving _fetch_schema() works (already covered above).
# ---------------------------------------------------------------------------

@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_get_type_schema_returns_real_attributes_for_a_known_resource_type():
    block = schema_watch.get_type_schema("aws", "aws_s3_bucket")

    assert block is not None
    assert "bucket" in block.get("attributes", {})


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_get_type_schema_returns_none_for_an_unknown_type():
    assert schema_watch.get_type_schema("aws", "aws_totally_made_up_type") is None


@pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")
def test_get_type_schema_resolves_a_data_source_via_kind_data():
    block = schema_watch.get_type_schema("aws", "aws_caller_identity", kind="data")

    assert block is not None
    assert "account_id" in block.get("attributes", {})
    # confirms the branch actually matters -- looking this data source up as a "resource"
    # (the wrong table) must NOT find it
    assert schema_watch.get_type_schema("aws", "aws_caller_identity", kind="resource") is None
