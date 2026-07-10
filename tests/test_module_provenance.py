"""
module_provenance.py pins a module's content hash + who/what informed it, and detects drift
if the module's files change without a matching re-pin.
"""
import json

import module_provenance
import modules as module_registry


def _make_module(tmp_path, module_id, content="resource \"aws_s3_bucket\" \"b\" {}\n"):
    module_dir = tmp_path / module_id
    module_dir.mkdir()
    (module_dir / "main.tf").write_text(content, encoding="utf-8")
    return module_dir


def _patch_registry(monkeypatch, tmp_path):
    """MODULES_DIR + output_root() both point inside tmp_path so a re-pin's upgrades/ report
    never lands in the real repo (pin() writes upgrades/<id>-v<n>.json under
    module_registry.output_root())."""
    monkeypatch.setattr(module_registry, "MODULES_DIR", str(tmp_path))
    monkeypatch.setattr(module_registry, "output_root", lambda: str(tmp_path))


def test_pin_writes_provenance_file(tmp_path, monkeypatch):
    _patch_registry(monkeypatch, tmp_path)
    _make_module(tmp_path, "widget")

    record = module_provenance.pin("widget", source="hand-authored", provider_version="~> 5.0")

    assert record["version"] == 1
    assert record["source"] == "hand-authored"
    assert record["provider_version"] == "~> 5.0"
    on_disk = json.loads((tmp_path / "widget" / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert on_disk == record


def test_pin_twice_bumps_version(tmp_path, monkeypatch):
    _patch_registry(monkeypatch, tmp_path)
    _make_module(tmp_path, "widget")

    module_provenance.pin("widget", source="first")
    second = module_provenance.pin("widget", source="second")

    assert second["version"] == 2
    assert second["source"] == "second"


def test_show_returns_none_when_never_pinned(tmp_path, monkeypatch):
    _patch_registry(monkeypatch, tmp_path)
    _make_module(tmp_path, "widget")

    assert module_provenance.show("widget") is None


def test_verify_ok_immediately_after_pin(tmp_path, monkeypatch):
    _patch_registry(monkeypatch, tmp_path)
    _make_module(tmp_path, "widget")
    module_provenance.pin("widget", source="hand-authored")

    ok, recorded, current = module_provenance.verify("widget")

    assert ok is True
    assert recorded == current


def test_verify_detects_drift_after_file_edit(tmp_path, monkeypatch):
    _patch_registry(monkeypatch, tmp_path)
    module_dir = _make_module(tmp_path, "widget")
    module_provenance.pin("widget", source="hand-authored")

    (module_dir / "main.tf").write_text("resource \"aws_s3_bucket\" \"b\" { bucket = \"changed\" }\n",
                                        encoding="utf-8")

    ok, recorded, current = module_provenance.verify("widget")

    assert ok is False
    assert recorded != current


def test_verify_fails_when_never_pinned(tmp_path, monkeypatch):
    _patch_registry(monkeypatch, tmp_path)
    _make_module(tmp_path, "widget")

    ok, recorded, current = module_provenance.verify("widget")

    assert ok is False
    assert recorded is None
    assert current is not None


def test_content_hash_ignores_the_provenance_file_itself(tmp_path, monkeypatch):
    _patch_registry(monkeypatch, tmp_path)
    _make_module(tmp_path, "widget")

    first_pin = module_provenance.pin("widget", source="v1")
    # Pinning again with identical module content (only PROVENANCE.json differs on disk between
    # the two pins) must produce the same content_hash both times -- the record isn't
    # self-referential.
    second_pin = module_provenance.pin("widget", source="v2")

    assert first_pin["content_hash"] == second_pin["content_hash"]


def test_pin_raises_for_unknown_module(tmp_path, monkeypatch):
    _patch_registry(monkeypatch, tmp_path)

    try:
        module_provenance.pin("does-not-exist", source="x")
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_cli_pin_and_verify(tmp_path, monkeypatch, capsys):
    _patch_registry(monkeypatch, tmp_path)
    _make_module(tmp_path, "widget")

    rc = module_provenance.main(["pin", "--module", "widget", "--source", "cli-test"])
    assert rc == 0
    capsys.readouterr()

    rc = module_provenance.main(["verify", "--module", "widget"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "matches its pinned version" in out


def test_pin_records_optional_schema_hash(tmp_path, monkeypatch):
    _patch_registry(monkeypatch, tmp_path)
    _make_module(tmp_path, "widget")

    record = module_provenance.pin("widget", source="v1", schema_hash="deadbeef")

    assert record["schema_hash"] == "deadbeef"
    on_disk = json.loads((tmp_path / "widget" / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert on_disk["schema_hash"] == "deadbeef"


def test_pin_schema_hash_defaults_to_none(tmp_path, monkeypatch):
    _patch_registry(monkeypatch, tmp_path)
    _make_module(tmp_path, "widget")

    record = module_provenance.pin("widget", source="v1")

    assert record["schema_hash"] is None


def test_first_pin_writes_no_upgrade_report(tmp_path, monkeypatch):
    _patch_registry(monkeypatch, tmp_path)
    _make_module(tmp_path, "widget")

    module_provenance.pin("widget", source="v1")

    assert not (tmp_path / "upgrades").exists()


def test_repin_with_changed_content_writes_upgrade_report(tmp_path, monkeypatch):
    _patch_registry(monkeypatch, tmp_path)
    module_dir = _make_module(tmp_path, "widget")
    module_provenance.pin("widget", source="v1", schema_hash="hash-v1")

    (module_dir / "main.tf").write_text(
        "resource \"aws_s3_bucket\" \"b\" { bucket = \"changed\" }\n", encoding="utf-8")
    second = module_provenance.pin("widget", source="v2", schema_hash="hash-v2")

    report_path = tmp_path / "upgrades" / "widget-v2.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["module_id"] == "widget"
    assert report["old_version"] == 1
    assert report["new_version"] == 2
    assert report["new_content_hash"] == second["content_hash"]
    assert report["old_schema_hash"] == "hash-v1"
    assert report["new_schema_hash"] == "hash-v2"


def test_repin_with_unchanged_content_writes_no_upgrade_report(tmp_path, monkeypatch):
    _patch_registry(monkeypatch, tmp_path)
    _make_module(tmp_path, "widget")
    module_provenance.pin("widget", source="v1")

    # Same file content as before (test_content_hash_ignores_the_provenance_file_itself already
    # proves the hash itself is stable across re-pins) -- no real upgrade happened.
    module_provenance.pin("widget", source="v2")

    assert not (tmp_path / "upgrades").exists()
