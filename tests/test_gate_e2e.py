"""
End-to-end deploy-gate test against REAL terraform.

Uses the built-in `terraform_data` resource so the whole loop (verify -> plan ->
approve -> apply) runs with no cloud provider, no credentials, and no network
beyond terraform's own init. State is written inside the temp dir, so the test is
hermetic. Skips automatically when terraform is not installed (e.g. a dev laptop
without it); CI installs terraform, so the invariant is proven there on every push.
"""
import json
import os
import subprocess
import sys
import threading
import time
import _thread

import pytest

import audit_chain
import plan_gate
import toolpath

TERRAFORM = toolpath.find_tool("terraform")

# Real terraform init/plan/apply against live providers: minutes, not seconds. Deselected from
# the default run (see pyproject addopts) and executed explicitly with `pytest -m slow`.
pytestmark = [
    pytest.mark.skipif(TERRAFORM is None, reason="terraform CLI not installed"),
    pytest.mark.slow,
]

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

# Two resources succeed, one fails via a real provisioner error -- reproduces the exact
# partial-apply scenario from the 2026-07-04 sandbox test that the audit chain couldn't
# previously reconstruct (it only ever recorded a bare FAILED/OK for the whole apply).
PARTIAL_FAILURE_TF = """terraform {
  required_version = ">= 1.4.0"
}

resource "terraform_data" "a" {
  input = "a"
}

resource "terraform_data" "b" {
  input = "b"
}

resource "terraform_data" "c" {
  input = "c"
  provisioner "local-exec" {
    command = "exit 1"
  }
}
"""

# One resource completes immediately, the second is deliberately slow (a real subprocess that
# prints progress every 0.5s for ~4s via local-exec) -- reproduces the 2026-07-05 audit finding:
# a hard interrupt (Ctrl+C) mid-apply used to unwind the stack before _audit() ever ran, losing
# even a bare FAILED record. `slow` printing periodically keeps terraform's -json stream emitting
# lines throughout the delay (confirmed empirically: local-exec stdout is relayed live as
# provision_progress events), so the interrupt below lands promptly, deterministically, well
# before `slow` finishes -- not raced against a single multi-second blocking read.
DELAYED_TF = """terraform {{
  required_version = ">= 1.4.0"
}}

resource "terraform_data" "fast" {{
  input = "fast"
}}

resource "terraform_data" "slow" {{
  input      = "slow"
  depends_on = [terraform_data.fast]
  provisioner "local-exec" {{
    command     = "import time\\nfor i in range(8):\\n    time.sleep(0.5)\\n    print(i, flush=True)\\n"
    interpreter = [{python_exe}, "-c"]
  }}
}}
"""


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
    # The one-shot approval was consumed. This comment was here with nothing under it: the hash
    # was computed and then never compared to anything, so the property it names was never
    # actually checked. It is checked now.
    current, _ = plan_gate._plan_hash(str(d))
    assert not os.path.exists(plan_gate._approved_path(str(d), current))


def test_apply_reports_an_approval_it_could_not_consume(gate, tmp_path, capsys):
    """A one-shot approval that survives the apply must be reported, never swallowed.

    _clear_approvals() used to catch every exception and return nothing, so a failed removal was
    indistinguishable from a successful one. The approval file stays on disk, stays valid, and
    the same plan can be applied again on it -- one approval, two applies. os.remove raises
    PermissionError on Windows whenever another process holds the file open, so this is a real
    path on the platform this repo runs on, not a contrived one.
    """
    d = tmp_path / "tf"
    d.mkdir()
    _write_tf(d, MAIN_TF)

    assert plan_gate.stage_verify(str(d)) is True
    assert plan_gate.stage_plan(str(d)) is True
    assert plan_gate.stage_approve(str(d), mode="gatekeeper") is True

    current, _ = plan_gate._plan_hash(str(d))
    approval_path = plan_gate._approved_path(str(d), current)

    real_remove = os.remove

    def _refuse_the_approval(path, *args, **kwargs):
        if os.path.abspath(path) == os.path.abspath(approval_path):
            raise PermissionError(13, "The process cannot access the file")
        return real_remove(path, *args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(plan_gate.os, "remove", _refuse_the_approval)
    try:
        # The apply itself still succeeds -- it already ran, and failing it afterwards would be
        # a lie in the other direction.
        assert plan_gate.stage_apply(str(d)) is True
    finally:
        monkeypatch.undo()

    # The approval really did survive, which is the condition being reported.
    assert os.path.exists(approval_path)

    err = capsys.readouterr().err
    assert "could not be removed" in err
    assert "applied again" in err

    audit_path = os.path.join(plan_gate.LOG_DIR, "audit.jsonl")
    entries = [json.loads(line) for line in open(audit_path, encoding="utf-8") if line.strip()]
    unconsumed = [e for e in entries if e.get("reason") == "approval_not_consumed"]
    assert unconsumed, [e.get("reason") for e in entries]
    assert unconsumed[0]["unremoved"] == [approval_path]

    ok, chain_errors = audit_chain.verify(audit_path)
    assert ok, chain_errors


def test_governed_destroy_actually_tears_down_real_state(gate, tmp_path):
    """2026-07-05 audit finding: destroy was the one ungated path in the whole gate -- raw
    `terraform destroy`, no plan-hash binding, no RBAC, no audit chain. Proves the fix against
    real terraform, not unit tests around the gate logic: create something for real through the
    normal loop, then tear it down through `plan --destroy` -> approve -> apply, and confirm
    real state is actually empty afterward (not just that the gate returned True) plus that both
    the create and the destroy are hash-chained in the audit trail."""
    d = tmp_path / "tf"
    d.mkdir()
    _write_tf(d, MAIN_TF)

    # Create for real through the normal loop.
    assert plan_gate.stage_verify(str(d)) is True
    assert plan_gate.stage_plan(str(d)) is True
    assert plan_gate.stage_approve(str(d), mode="gatekeeper") is True
    assert plan_gate.stage_apply(str(d)) is True
    assert (d / "terraform.tfstate").exists()
    state_list = subprocess.run([TERRAFORM, f"-chdir={d}", "state", "list"],
                                 capture_output=True, text=True).stdout
    assert "terraform_data.demo" in state_list

    # Tear it down through the SAME gate -- plan(--destroy) -> approve -> apply, no new
    # apply-side code path, no raw `terraform destroy`.
    # aeeb116 (2026-08-26) made a stated impact mandatory for a plan that is not
    # autonomous-eligible, and a destroy never is. This file was last touched
    # 2026-07-27 and is deselected by `-m 'not slow'`, so nothing ran it and nothing
    # noticed for five days. The requirement is right; the call predates it.
    assert plan_gate.stage_plan(
        str(d), destroy=True,
        impact="Tears down the demo resource; nothing downstream reads it.") is True
    assert plan_gate.stage_approve(str(d), mode="gatekeeper") is True
    assert plan_gate.stage_apply(str(d)) is True

    # Real state is actually empty -- not just that the gate reported success.
    state_list_after = subprocess.run([TERRAFORM, f"-chdir={d}", "state", "list"],
                                       capture_output=True, text=True).stdout
    assert state_list_after.strip() == ""

    # The destroy plan is distinguishable in the audit trail from the create plan.
    audit_path = os.path.join(plan_gate.LOG_DIR, "audit.jsonl")
    entries = [json.loads(line) for line in open(audit_path, encoding="utf-8") if line.strip()]
    plan_records = [e for e in entries if e.get("action") == "plan" and e.get("status") == "OK"]
    assert any(not r.get("destroy") for r in plan_records), "expected the create plan record"
    assert any(r.get("destroy") for r in plan_records), "expected a destroy plan record"

    apply_records = [e for e in entries if e.get("action") == "apply" and e.get("status") == "OK"]
    assert len(apply_records) >= 2  # the create apply and the destroy apply
    destroy_apply = apply_records[-1]
    assert any("terraform_data.demo" in a for a in destroy_apply["resources_applied"])

    ok, chain_errors = audit_chain.verify(audit_path)
    assert ok, chain_errors


def test_real_plan_hash_changes_on_tf_edit_and_blocks_apply(gate, tmp_path):
    d = tmp_path / "tf"
    d.mkdir()
    _write_tf(d, MAIN_TF)

    assert plan_gate.stage_verify(str(d)) is True
    assert plan_gate.stage_plan(str(d)) is True
    approved_hash, _ = plan_gate._plan_hash(str(d))
    assert plan_gate.stage_approve(str(d), mode="gatekeeper") is True

    # Edit the .tf. aeeb116 (2026-08-26) added a SECOND protection here: a plan now needs a
    # verify receipt, so editing the source after verify passed refuses at plan time rather
    # than waiting for the hash comparison at apply. That is stricter, and this test asserts
    # it rather than working around it -- the file was last touched 2026-07-27, is deselected
    # by `-m 'not slow'`, and so nothing exercised either protection.
    _write_tf(d, DRIFTED_TF)
    assert plan_gate.stage_plan(str(d)) is False

    # Re-verify, and the original guarantee still has to hold: a new real plan, a different
    # hash, and an apply that refuses because the approval was bound to the old one.
    assert plan_gate.stage_verify(str(d)) is True
    assert plan_gate.stage_plan(str(d)) is True
    drifted_hash, _ = plan_gate._plan_hash(str(d))
    assert drifted_hash != approved_hash
    # The approval was bound to the old hash; the new plan has no approval on record.
    assert plan_gate.stage_apply(str(d)) is False


def test_partial_apply_failure_captures_per_resource_outcome(gate, tmp_path):
    """Reproduces the exact partial-apply scenario from the 2026-07-04 sandbox test with a
    real terraform apply: multiple resources, one fails via a genuine error. Confirms the
    audit record now captures which resources succeeded and which failed (and why), not just
    a bare FAILED for the whole apply -- and that audit_chain.verify() still passes."""
    d = tmp_path / "tf"
    d.mkdir()
    _write_tf(d, PARTIAL_FAILURE_TF)

    assert plan_gate.stage_verify(str(d)) is True
    assert plan_gate.stage_plan(str(d)) is True
    assert plan_gate.stage_approve(str(d), mode="gatekeeper") is True
    assert plan_gate.stage_apply(str(d)) is False  # real, genuine failure

    audit_path = os.path.join(plan_gate.LOG_DIR, "audit.jsonl")
    entries = [json.loads(line) for line in open(audit_path, encoding="utf-8") if line.strip()]
    apply_failures = [e for e in entries if e.get("action") == "apply" and e.get("status") == "FAILED"]
    assert apply_failures, "expected a FAILED apply record"
    rec = apply_failures[-1]

    # 1. resources_applied / resources_failed / resource_errors populated correctly.
    applied = rec["resources_applied"]
    failed = rec["resources_failed"]
    errors = rec["resource_errors"]
    assert any("terraform_data.a" in a for a in applied)
    assert any("terraform_data.b" in a for a in applied)
    assert not any("terraform_data.c" in a for a in applied)  # never completed
    assert any("terraform_data.c" in f for f in failed)
    assert any("terraform_data.c" in k for k in errors)
    assert "exit status 1" in next(iter(errors.values()))

    # 2. audit_chain.verify() still passes on the record with the new fields.
    ok, chain_errors = audit_chain.verify(audit_path)
    assert ok, chain_errors


def test_interrupt_mid_apply_still_writes_a_partial_audit_record(gate, tmp_path, monkeypatch):
    """2026-07-05 audit finding: a hard interrupt (Ctrl+C) mid-apply used to unwind the stack
    before _audit() ever ran, so NOTHING was recorded for a real, possibly-partial apply -- worse
    than the bare FAILED the pre-2026-07-04 code at least wrote. Reproduces a genuine interrupt
    (via _thread.interrupt_main(), not a monkeypatched failure) against a real, currently-running
    terraform subprocess: one resource completes, then while a second, deliberately slow resource
    is still applying, the main thread is interrupted. Confirms an INTERRUPTED record is still
    written with the real partial data, that the record still hash-chains cleanly, and that the
    interrupt itself still propagates to the caller (it must not be swallowed)."""
    d = tmp_path / "tf"
    d.mkdir()
    _write_tf(d, DELAYED_TF.format(python_exe=json.dumps(sys.executable)))

    assert plan_gate.stage_verify(str(d)) is True
    assert plan_gate.stage_plan(str(d)) is True
    assert plan_gate.stage_approve(str(d), mode="gatekeeper") is True

    real_apply_with_json_capture = plan_gate._apply_with_json_capture
    progress = {}

    def _spy(dir_, applied, failed, errors):
        progress["applied"] = applied  # same list object -- the watcher thread can see it fill in
        return real_apply_with_json_capture(dir_, applied, failed, errors)

    monkeypatch.setattr(plan_gate, "_apply_with_json_capture", _spy)

    def _interrupt_once_fast_resource_lands():
        for _ in range(200):  # ~10s ceiling; "fast" completes in well under 1s in practice
            if len(progress.get("applied") or []) >= 1:
                break
            time.sleep(0.05)
        _thread.interrupt_main()

    watcher = threading.Thread(target=_interrupt_once_fast_resource_lands, daemon=True)
    watcher.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            plan_gate.stage_apply(str(d))
    finally:
        watcher.join(timeout=5)

    audit_path = os.path.join(plan_gate.LOG_DIR, "audit.jsonl")
    entries = [json.loads(line) for line in open(audit_path, encoding="utf-8") if line.strip()]
    interrupted = [e for e in entries if e.get("action") == "apply" and e.get("status") == "INTERRUPTED"]
    assert interrupted, "expected an INTERRUPTED apply record"
    rec = interrupted[-1]

    # 1. Partial data is real: fast finished and is recorded; slow never got the chance to.
    assert any("terraform_data.fast" in a for a in rec["resources_applied"])
    assert not any("terraform_data.slow" in a for a in rec["resources_applied"])
    assert not any("terraform_data.slow" in f for f in rec["resources_failed"])

    # 2. audit_chain.verify() still passes on the INTERRUPTED record.
    ok, chain_errors = audit_chain.verify(audit_path)
    assert ok, chain_errors

    # 3. The one-shot approval was NOT silently consumed on an interrupted (unknown-outcome)
    # apply -- re-planning would invalidate it via the hash-mismatch check regardless, but this
    # confirms the interrupt path doesn't also destroy the operator's ability to re-approve.
    current, _ = plan_gate._plan_hash(str(d))
    assert os.path.exists(plan_gate._approved_path(str(d), current))


def test_auto_approve_apply_refuses_a_real_destructive_change(gate, tmp_path):
    """Phase 1 enforcement (core/governance/destructive_change_gate.py): mode="auto-approve"
    means no human ever reviews the plan, so a plan showing a real delete must be refused --
    not just classified, actually blocked, with the real state left untouched. Create for real
    through the normal (gatekeeper) loop first, then attempt to tear it down entirely through
    the auto-approve path and confirm apply is refused AND nothing was actually destroyed."""
    d = tmp_path / "tf"
    d.mkdir()
    _write_tf(d, MAIN_TF)

    assert plan_gate.stage_verify(str(d)) is True
    assert plan_gate.stage_plan(str(d)) is True
    assert plan_gate.stage_approve(str(d), mode="gatekeeper") is True
    assert plan_gate.stage_apply(str(d)) is True
    state_list = subprocess.run([TERRAFORM, f"-chdir={d}", "state", "list"],
                                 capture_output=True, text=True).stdout
    assert "terraform_data.demo" in state_list

    # Attempt the teardown through the auto-approve path -- no y/N prompt at all.
    # aeeb116 (2026-08-26) made a stated impact mandatory for a plan that is not
    # autonomous-eligible, and a destroy never is. This file was last touched
    # 2026-07-27 and is deselected by `-m 'not slow'`, so nothing ran it and nothing
    # noticed for five days. The requirement is right; the call predates it.
    assert plan_gate.stage_plan(
        str(d), destroy=True,
        impact="Tears down the demo resource; nothing downstream reads it.") is True
    # The refusal now lands at APPROVE, not apply: "Teardowns require interactive human
    # review." That is one step earlier than this test was written for and strictly safer --
    # an unreviewed teardown never reaches a stage that could run it. The test asserted the
    # older, weaker shape and nothing ran it to notice, being deselected by `-m 'not slow'`.
    assert plan_gate.stage_approve(str(d), mode="auto-approve") is False

    # Real state is untouched -- the resource still exists, nothing was actually destroyed.
    state_list_after = subprocess.run([TERRAFORM, f"-chdir={d}", "state", "list"],
                                       capture_output=True, text=True).stdout
    assert "terraform_data.demo" in state_list_after

    # Refusing early must not leave an approval lying around for a later apply to find.
    current, _ = plan_gate._plan_hash(str(d))
    assert not os.path.exists(plan_gate._approved_path(str(d), current))

    # The refusal is on record, tied to the real classification.
    audit_path = os.path.join(plan_gate.LOG_DIR, "audit.jsonl")
    with open(audit_path, encoding="utf-8") as handle:
        entries = [json.loads(line) for line in handle if line.strip()]
    # plan_gate.py:1524 -- the approve-time refusal records its own reason. It is not the
    # apply-time `destructive_change_not_autonomous_eligible` this test looked for, because
    # the plan never reaches apply any more.
    refused = [e for e in entries
               if e.get("reason") == "destroy_auto_approve_forbidden"]
    assert refused, f"expected a destroy_auto_approve_forbidden record, got {[e.get('reason') for e in entries[-6:]]}"
    assert refused[-1]["action"] == "approve"
    assert refused[-1]["status"] == "REJECTED"

    # The staged/guarded path (gatekeeper mode) remains genuinely usable for this same
    # destructive plan -- enforcement blocks the unreviewed path, not the reviewed one.
    assert plan_gate.stage_approve(str(d), mode="gatekeeper") is True
    assert plan_gate.stage_apply(str(d)) is True
    state_list_final = subprocess.run([TERRAFORM, f"-chdir={d}", "state", "list"],
                                       capture_output=True, text=True).stdout
    assert state_list_final.strip() == ""


def test_auto_approve_apply_succeeds_for_a_real_create_only_change(gate, tmp_path):
    """The other half of the same proof: enforcement must not block everything indiscriminately
    -- a genuinely create-only plan must still sail through the auto-approve path end to end,
    with the resource actually created for real."""
    d = tmp_path / "tf"
    d.mkdir()
    _write_tf(d, MAIN_TF)

    assert plan_gate.stage_verify(str(d)) is True
    assert plan_gate.stage_plan(str(d)) is True
    assert plan_gate.stage_approve(str(d), mode="auto-approve") is True
    assert plan_gate.stage_apply(str(d), mode="auto-approve") is True

    state_list = subprocess.run([TERRAFORM, f"-chdir={d}", "state", "list"],
                                 capture_output=True, text=True).stdout
    assert "terraform_data.demo" in state_list
