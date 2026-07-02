"""Offline terraform-validate helper — non-mutating, credential-free correctness check.

These tests avoid invoking real terraform (they hit the early-return / formatting paths),
so they run fast and pass with or without terraform installed.
"""
import tf_validate


def test_missing_dir_is_not_ok():
    # terraform present -> dir-not-found returns ok False; terraform absent -> ok None (skipped).
    r = tf_validate.validate("/definitely/not/a/real/dir")
    assert r["ok"] in (False, None)
    assert r.get("available") in (True, False)


def test_format_states():
    assert "OK" in tf_validate._format({"available": True, "ok": True, "warning_count": 0})
    assert "skipped" in tf_validate._format({"available": False, "ok": None})
    invalid = {"available": True, "ok": False, "phase": "validate", "error_count": 2,
               "diagnostics": [{"severity": "error", "summary": "bad ref"}]}
    out = tf_validate._format(invalid)
    assert "INVALID" in out and "bad ref" in out


def test_load_returns_none_when_absent(tmp_path):
    assert tf_validate.load(str(tmp_path)) is None
