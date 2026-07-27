"""
find_report must not be shadowed by an incomplete report dir that shares a plan-hash
(several runs can produce the same hash; only the complete one is usable).
"""
import json

import plan_inspector as pi


def test_find_report_prefers_complete_over_partial(tmp_path, monkeypatch):
    partial = tmp_path / "a" / "abc123"
    partial.mkdir(parents=True)                      # no manifest/plan -> unusable
    complete = tmp_path / "b" / "abc123"
    complete.mkdir(parents=True)
    (complete / "manifest.json").write_text(json.dumps({"short": "abc123"}), encoding="utf-8")
    (complete / "plan.json").write_text(json.dumps({"resource_changes": []}), encoding="utf-8")

    monkeypatch.setattr(pi, "report_roots", lambda: [tmp_path / "a", tmp_path / "b"])

    assert pi.find_report("abc123") == complete
    # and load_report works against it
    rd, manifest, _plan = pi.load_report("abc123")
    assert rd == complete and manifest["short"] == "abc123"


def test_snapshot_hashes_tfvars_but_never_copies_it(tmp_path):
    """Secrets in .tfvars must not land in a report bundle the dashboard serves,
    but must still be hashed so source-drift detection keeps working."""
    src = tmp_path / "tf"
    src.mkdir()
    (src / "main.tf").write_text('resource "null_resource" "a" {}', encoding="utf-8")
    (src / "secrets.tfvars").write_text('db_password = "hunter2"', encoding="utf-8")
    report = tmp_path / "report"
    report.mkdir()

    hashes = pi.write_source_snapshot(src, report)

    assert "secrets.tfvars" in hashes, "tfvars must still be hashed for drift detection"
    assert "main.tf" in hashes
    snap = report / "source_snapshot"
    assert (snap / "main.tf").exists()
    assert not (snap / "secrets.tfvars").exists(), "tfvars content must not be copied"
    assert "hunter2" not in (report / "source_hashes.json").read_text(encoding="utf-8")
