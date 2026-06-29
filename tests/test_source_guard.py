import json

import source_guard


def test_source_guard_detects_manual_edits(tmp_path):
    tf = tmp_path / "terraform"
    tf.mkdir()
    main = tf / "main.tf"
    main.write_text('resource "aws_s3_bucket" "data" {}\n', encoding="utf-8")

    baseline = source_guard.write_baseline(tf, label="test")
    assert baseline["file_count"] == 1
    assert source_guard.status(tf)["status"] == "CURRENT"

    main.write_text('resource "aws_s3_bucket" "data" { bucket = "changed" }\n', encoding="utf-8")
    state = source_guard.status(tf)
    diff = "\n".join(source_guard.diff(tf))

    assert state["status"] == "STALE"
    assert state["changed"] == ["main.tf"]
    assert 'bucket = "changed"' in diff


def test_source_guard_baseline_file_is_not_self_tracked(tmp_path):
    tf = tmp_path / "terraform"
    tf.mkdir()
    (tf / "main.tf").write_text('locals { name = "x" }\n', encoding="utf-8")

    source_guard.write_baseline(tf)
    record = json.loads((tf / ".minus" / "baseline.json").read_text(encoding="utf-8"))

    assert "main.tf" in record["hashes"]
    assert all(not name.startswith(".minus/") for name in record["hashes"])
