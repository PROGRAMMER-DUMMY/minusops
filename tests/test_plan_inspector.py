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
