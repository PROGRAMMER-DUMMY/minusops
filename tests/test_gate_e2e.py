"""
End-to-end deploy-gate test against REAL terraform.

Uses the built-in `terraform_data` resource so the whole loop (verify -> plan ->
approve -> apply) runs with no cloud provider, no credentials, and no network
beyond terraform's own init. State is written inside the temp dir, so the test is
hermetic. Skips automatically when terraform is not installed (e.g. a dev laptop
without it); CI installs terraform, so the invariant is proven there on every push.
"""
import subprocess

import pytest

import plan_gate
import toolpath

TERRAFORM = toolpath.find_tool("terraform")

pytestmark = pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed")

MAIN_TF = """terraform {
  required_version = ">= 1.4.0"
}

resource "terraform_data" "demo" {
  input = "hello"
}

output "value" {
  value = terraform_data.demo.output
}
"""

DRIFTED_TF = MAIN_TF.replace('input = "hello"', 'input = "changed"')


def _write_tf(directory, body):
    (directory / "main.tf").write_text(body, encoding="utf-8")
    subprocess.run([TERRAFORM, f"-chdir={directory}", "fmt"], capture_output=True, text=True)


@pytest.fixture
def gate(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_gate, "LOG_DIR", str(tmp_path / "logs"))
    # Source-provenance and identity are exercised by unit tests; here we focus on the
    # real terraform plan/hash/apply path, so stub those two cross-module hooks.
    monkeypatch.setattr(plan_gate, "_source_status_for_hash",
                        lambda _h: {"status": "CURRENT", "stale": False, "reason": ""})
    monkeypatch.setattr(plan_gate, "_identity", lambda: ("local-test-account", True))
    monkeypatch.setattr(plan_gate, "_credential_posture", lambda: {"connected": True, "type": "temporary"})
    monkeypatch.setattr(plan_gate, "_timed_input", lambda *a, **k: "y")
    # Report generation is covered by test_reporter; keep this test focused on the gate.
    import reporter
    monkeypatch.setattr(reporter, "generate", lambda *a, **k: None)
    return tmp_path


def test_full_gate_loop_applies_exact_plan(gate, tmp_path):
    d = tmp_path / "tf"
    d.mkdir()
    _write_tf(d, MAIN_TF)

    assert plan_gate.stage_verify(str(d)) is True
    assert plan_gate.stage_plan(str(d)) is True
    assert plan_gate.stage_approve(str(d), mode="gatekeeper") is True
    assert plan_gate.stage_apply(str(d)) is True

    # Real terraform actually applied: local state now exists.
    assert (d / "terraform.tfstate").exists()
    # The one-shot approval was consumed.
    current, _ = plan_gate._plan_hash(str(d))


def test_real_plan_hash_changes_on_tf_edit_and_blocks_apply(gate, tmp_path):
    d = tmp_path / "tf"
    d.mkdir()
    _write_tf(d, MAIN_TF)

    assert plan_gate.stage_verify(str(d)) is True
    assert plan_gate.stage_plan(str(d)) is True
    approved_hash, _ = plan_gate._plan_hash(str(d))
    assert plan_gate.stage_approve(str(d), mode="gatekeeper") is True

    # Edit the .tf -> a new real plan -> a different hash -> apply must refuse.
    _write_tf(d, DRIFTED_TF)
    plan_gate.stage_plan(str(d))
    drifted_hash, _ = plan_gate._plan_hash(str(d))
    assert drifted_hash != approved_hash
    # The approval was bound to the old hash; the new plan has no approval on record.
    assert plan_gate.stage_apply(str(d)) is False
