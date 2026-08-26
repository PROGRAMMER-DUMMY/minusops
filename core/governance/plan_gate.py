"""
plan_gate.py — state-aware, plan-bound Terraform deploy gate.

Enforces the secure deployment loop in code (not just docs):

    verify  ->  terraform fmt -check + validate + security scan
    plan    ->  terraform plan -out=tfplan, record a PLAN HASH
    approve ->  review the exact plan -> record a hash-bound approval (NO secrets stored)
    apply   ->  current hash must match the approved one -> terraform apply tfplan

Credential model — we never handle secrets:
  * The operator authenticates via the cloud CLI BEFORE applying (e.g. `aws sso login`,
    or assume an MFA-gated deploy role into their CLI session).
  * MFA is enforced upstream by that role's trust policy — the gate does not mint or
    store tokens. terraform apply uses the ambient CLI credential chain.

Guarantees:
  * Apply runs only the exact reviewed plan (apply tfplan, never re-plan).
  * Any .tf change -> new plan hash -> prior approval is void -> re-review required.
  * The approval record holds only a hash + caller identity + timestamp — no credentials.
  * auto-approve skips the y/N prompt but still cannot apply a hash you did not approve.
  * --policy-mode production enforces: an approver allowlist is required, the approver
    must differ from the planner (two-person rule), and MINUS_ALLOW_STATIC_CREDS is not
    honored (a temporary MFA-gated session is required). --policy-mode dev keeps these
    relaxed for single-operator work.

Cross-platform (Windows / macOS / Linux): os.path, list-form subprocess, no shell.

Destroy: teardown goes through the exact same loop, not a raw `terraform destroy` — pass
--destroy to `plan`. approve/apply are unchanged: a destroy plan's resource_changes are just
actions=["delete"], which hash-binding, RBAC, and the audit chain already handle like any
other plan.

Examples (point --dir at any Terraform directory — the engine is workload-agnostic):
    minusctl gate verify  --dir path/to/terraform
    minusctl gate plan    --dir path/to/terraform
    minusctl gate approve --dir path/to/terraform
    minusctl gate apply   --dir path/to/terraform
    minusctl gate run     --dir path/to/terraform [--mode auto-approve]

    minusctl gate plan    --dir path/to/terraform --destroy   # governed teardown
    minusctl gate approve --dir path/to/terraform
    minusctl gate apply   --dir path/to/terraform

Depends on: providers.base (get_provider), plan_inspector, cli_diagnostics, team_resolver,
    toolpath, audit_chain, authz, destructive_change_gate, cloud_drift, address_churn,
    rule_stages, optimize_analyzer, rego_gate, intent_assertions, requirements (as reqgate),
    architecture_decision (as adecision), ephemeral_apply; plus lazy imports of reporter and
    coverage_audit inside the cost/report paths. All resolved through the core/ sys.path shim
    below, not by package path.
Shells out to: terraform (fmt -check, init, validate, plan, show -json, apply -json), the
    cloud CLI indirectly via providers.base for identity/credential posture, opa via
    rego_gate/toolpath, and core/reporting/optimize_analyzer.py as a subprocess.
Used by: core/governance/reflector.py (lazy, for _pending_path), core/reporting/doctor.py,
    core/reporting/cli_diagnostics.py (lazy)
"""
import os
import re
import sys
import json
import hashlib
import getpass
import argparse
import datetime
import textwrap
import threading
import time
import subprocess

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
from providers.base import get_provider  # noqa: E402
import plan_inspector  # noqa: E402
import cli_diagnostics  # noqa: E402
import team_resolver  # noqa: E402
import toolpath  # noqa: E402
import audit_chain  # noqa: E402
import authz  # noqa: E402
import destructive_change_gate  # noqa: E402
import cloud_drift  # noqa: E402
import address_churn  # noqa: E402
import rule_stages  # noqa: E402
import optimize_analyzer  # noqa: E402
import rego_gate  # noqa: E402
import intent_assertions  # noqa: E402
import requirements as reqgate  # noqa: E402
import architecture_decision as adecision  # noqa: E402
import ephemeral_apply  # noqa: E402
import source_guard  # noqa: E402

WORKSPACE = os.getcwd()
LOG_DIR = os.path.join(WORKSPACE, ".agents", "logs")
SCAN = os.path.join(WORKSPACE, "core", "reporting", "optimize_analyzer.py")

PLAN_FILE = "tfplan"          # written inside the target dir via terraform -chdir
CONFIRM_TIMEOUT = 45          # seconds to confirm before the request is denied


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _audit(action, status, **extra):
    os.makedirs(LOG_DIR, exist_ok=True)
    rec = {"timestamp": _now(), "operator": getpass.getuser(),
           "component": "plan_gate", "action": action, "status": status}
    rec.update(extra)
    try:
        audit_chain.append(os.path.join(LOG_DIR, "audit.jsonl"), rec)
    except Exception as e:
        print(f"[gate] WARNING: could not write audit record: {e}", file=sys.stderr)


def _gate_state_lock(path):
    """Mutual exclusion for read-modify-write on gate state.

    Reuses audit_chain._AppendLock -- threading.Lock for intra-process plus an OS-native
    advisory lock for inter-process, already hardened against this repo's recurring Windows
    lock/handle divergences. Writing a second one is how the first acquired its bugs.

    Needed because atomicity alone does not prevent LOST UPDATES: operator B's write can
    land between A reading and A approving, so A records an approval bound to B's plan hash
    while believing it is their own. That is approval integrity, the guarantee everything
    else rests on.
    """
    return audit_chain._AppendLock(path)


def _write_json_atomic(path, payload):
    """Write gate state so a crash can never leave it truncated.

    open(path, "w") truncates immediately, so a process killed mid-write destroys the
    pending plan or the approval record outright. os.replace is atomic on POSIX and Windows.

    The temp file carries pid+thread so two concurrent writers cannot clobber each other's
    staging file -- a single shared "<path>.tmp" made this corrupt under contention, which
    is exactly the failure atomicity was supposed to remove (caught by
    tests/test_gate_concurrency.py, not by inspection).

    Atomic per write. For read-modify-write, hold _gate_state_lock across the whole cycle.
    """
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        # Windows: os.replace maps to MoveFileEx, which transiently fails with
        # ERROR_ACCESS_DENIED while any other handle touches the destination -- a competing
        # writer, an indexer, antivirus. POSIX rename has no such window. Retry briefly
        # rather than surfacing a spurious PermissionError as gate-state corruption. Bounded
        # so a GENUINE permissions problem still raises instead of hanging.
        deadline = time.monotonic() + 5.0
        while True:
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _canonical_dir(dir_):
    """Return a stable absolute directory identity for approval binding."""
    return os.path.normcase(os.path.abspath(dir_))


def _dir_key(dir_):
    return hashlib.sha256(_canonical_dir(dir_).encode("utf-8")).hexdigest()[:16]


def _state_dir(dir_):
    return os.path.join(LOG_DIR, "plan_gate", _dir_key(dir_))


def _pending_path(dir_):
    return os.path.join(_state_dir(dir_), "pending_plan.json")


def _approval_dir(dir_):
    return os.path.join(_state_dir(dir_), "approvals")


def _approved_path(dir_, plan_hash):
    return os.path.join(_approval_dir(dir_), f"{plan_hash}.json")


def _run(args, capture=False):
    """Run a command (list form, no shell). Returns (rc, stdout, stderr)."""
    toolpath.ensure_external_tools()
    try:
        res = subprocess.run(args, text=True, capture_output=capture)
        return res.returncode, (res.stdout or ""), (res.stderr or "")
    except FileNotFoundError:
        return 127, "", f"command not found: {args[0]}"


def _policy_mode(value=None):
    mode = (value or os.environ.get("MINUS_POLICY_MODE") or "dev").strip().lower()
    if mode not in {"dev", "production"}:
        raise ValueError("policy mode must be 'dev' or 'production'")
    return mode


_TERRAFORM_BIN = None


def _terraform_bin():
    """Resolve terraform to an absolute path so the gate runs it regardless of PATH state
    (Windows WinGet installs often aren't on the subprocess PATH). Falls back to the bare
    name if discovery fails, preserving the original 'command not found' behavior."""
    global _TERRAFORM_BIN
    if _TERRAFORM_BIN is None:
        toolpath.ensure_external_tools()
        _TERRAFORM_BIN = toolpath.find_tool("terraform") or "terraform"
    return _TERRAFORM_BIN


def _tf(dir_, *tf_args, capture=False):
    return _run([_terraform_bin(), f"-chdir={dir_}", *tf_args], capture=capture)


_ACTION_VERB = {"create": "Creating", "update": "Modifying", "delete": "Destroying", "read": "Reading"}
_ACTION_DONE = {"create": "creation", "update": "modification", "delete": "destruction", "read": "read"}


def _apply_with_json_capture(dir_, applied, failed, errors):
    """Run `terraform apply -json`, re-rendering it as plain readable lines (one per
    resource start/complete/error, plus progress pings for long-running resources -- not
    Terraform's own colored UI) while capturing structured per-resource outcomes.

    The per-resource outcomes exist so the audit chain records WHICH resources succeeded
    before a failure. Without them a partial apply has to be reconstructed by hand from
    `terraform state list` plus the cloud CLI.

    applied/failed/errors are caller-owned containers on purpose. Building them as locals and
    returning them at the end loses everything when Ctrl+C unwinds the stack before the
    return, taking the caller's audit write with it. Appending here mutates the same objects
    the caller already holds, so partial data survives however this function exits. Do not
    convert these back into return values.

    Returns returncode; resources_applied/resources_failed/resource_errors are accumulated
    into the caller-provided applied/failed/errors.
    """
    toolpath.ensure_external_tools()
    cmd = [_terraform_bin(), f"-chdir={dir_}", "apply", "-json", PLAN_FILE]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1)
    except FileNotFoundError:
        print(f"[gate] command not found: {cmd[0]}", file=sys.stderr)
        return 127

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(line)  # non-JSON output is rare with -json, but never swallow it
            continue
        etype = event.get("type")
        hook = event.get("hook") or {}
        addr = (hook.get("resource") or {}).get("addr")
        action = hook.get("action")
        elapsed = hook.get("elapsed_seconds", "?")
        if etype == "apply_start" and addr:
            print(f"  {addr}: {_ACTION_VERB.get(action, action)}...")
        elif etype == "apply_progress" and addr:
            print(f"  {addr}: still {_ACTION_VERB.get(action, action).lower()}... ({elapsed}s)")
        elif etype == "apply_complete" and addr:
            applied.append(addr)
            print(f"  {addr}: {_ACTION_DONE.get(action, action)} complete ({elapsed}s)")
        elif etype == "apply_errored" and addr:
            failed.append(addr)
            print(f"  {addr}: {_ACTION_DONE.get(action, action)} ERRORED")
        elif etype == "diagnostic" and (event.get("diagnostic") or {}).get("severity") == "error":
            diag = event["diagnostic"]
            message = diag.get("detail") or diag.get("summary") or ""
            diag_addr = diag.get("address")
            if diag_addr:
                errors[diag_addr] = message
            print(f"[gate] ERROR{f' ({diag_addr})' if diag_addr else ''}: {diag.get('summary', message)}")
        elif etype == "change_summary":
            changes = event.get("changes") or {}
            print(f"[gate] apply finished: +{changes.get('add', 0)} ~{changes.get('change', 0)} "
                  f"-{changes.get('remove', 0)}")

    proc.wait()
    return proc.returncode


def _plan_hash(dir_):
    """Stable hash of the planned changes (resource + output changes from `terraform show -json`)."""
    rc, out, err = _tf(dir_, "show", "-json", PLAN_FILE, capture=True)
    if rc != 0:
        return None, err.strip() or "terraform show failed"
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        return None, f"could not parse plan json: {e}"
    payload = {
        "resource_changes": data.get("resource_changes", []),
        "output_changes": data.get("output_changes", {}),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), ""


def _plan_json(dir_):
    """Parse `terraform show -json` fresh (the same command _plan_hash already runs, kept as a
    separate call rather than changing _plan_hash's return signature -- 4 existing call sites
    unpack it as a 2-tuple and this avoids touching any of them). For callers that need the
    full resource_changes payload, not just its hash -- currently the destructive-change
    classification below. Returns (plan_json_dict_or_None, err)."""
    rc, out, err = _tf(dir_, "show", "-json", PLAN_FILE, capture=True)
    if rc != 0:
        return None, err.strip() or "terraform show failed"
    try:
        return json.loads(out), ""
    except json.JSONDecodeError as e:
        return None, f"could not parse plan json: {e}"


def _classify_plan(dir_):
    """Destructive-change classification (core/governance/destructive_change_gate.py) for the
    current tfplan on disk. Fail-closed: if the plan can't be read/parsed for any reason, this
    returns a classification that is NOT autonomous-eligible rather than silently treating an
    unknown as safe -- the same fail-closed posture destructive_change_gate.py itself uses for
    every other unrecognized shape."""
    plan_json, err = _plan_json(dir_)
    if plan_json is None:
        return {
            "autonomous_eligible": False,
            "findings": [{"address": None, "type": None, "reason": "plan_unreadable", "detail": err}],
            "reduced_assurance": False,
            "reduced_assurance_reason": None,
            "databricks_resources": [],
            "resource_change_count": 0,
        }
    return destructive_change_gate.classify(plan_json)


G6_RULE_IDS = ("SEC-01", "COST-01", "SEC-03", "SEC-04", "COST-02", "COST-03", "SEC-05", "SEC-02",
               "SEC-06", "SEC-07", "SEC-08", "SEC-09", "SEC-10")
# Every new rule ID must be added here the moment it lands in rules.rego. Leaving one out does
# not merely skip a comparison: _g6_shadow_eval's divergence loop iterates only this tuple, so
# an unlisted rule's real (non-unresolved) findings vanish from both the divergence report and
# the audit chain. Its field_unresolved findings would still surface (separate, unfiltered
# list), so the uncertain case stays visible while the confirmed violation goes silent --
# backwards from what the shadow mechanism exists to guarantee. SEC-06/SEC-07
# (docs/g6_iam_extension_scope.md) have no regex counterpart at all, which is how that gap
# first appeared.


def _g6_shadow_eval(dir_, plan_json):
    """G6 (docs/g6_scope.md): Rego-over-plan-JSON evaluation run in SHADOW MODE alongside the
    existing regex-over-HCL scan -- logged and printed, never blocks stage_plan, never
    enforces. `optimize_analyzer.scan_hcl_files(dir_)` is re-run here (a second, redundant,
    harmless read-only invocation on the same dir -- stage_verify's own call happens earlier,
    before a plan exists, and can't be reused for a same-moment comparison) so both verdicts
    are computed against the identical HCL, at the identical point in the pipeline, making the
    divergence comparison fair.

    Divergence is checked in BOTH directions, never just one: a finding Rego produces that the
    regex path didn't (documented as a resolved-JSON improvement, e.g. SEC-05b/c, SEC-02 --
    see docs/g6_scope.md's rule map) AND, more dangerously under this posture, a finding the
    regex path produced that Rego's resolved-JSON view no longer does -- IAM policy
    canonicalization (key order, Statement array-vs-object, principal formatting) means
    "resolved JSON is a strict superset of text matching" is not guaranteed. A disappeared SEC
    finding is a false-compliance-claim until proven a genuine old-regex false positive, so a
    lost finding is treated as a bug-until-explained, exactly the same as a new one."""
    regex_findings = []
    try:
        regex_findings = optimize_analyzer.scan_hcl_files(dir_)
    except Exception as exc:
        regex_findings = None
        regex_error = str(exc)
    else:
        regex_error = None

    rego_result = rego_gate.evaluate(plan_json)

    if regex_findings is None or rego_result["evaluation_failed"]:
        return {
            "comparable": False,
            "regex_error": regex_error,
            "rego_evaluation_failed": rego_result["evaluation_failed"],
            "rego_reason": rego_result.get("reason"),
            "rego_detail": rego_result.get("detail"),
        }

    regex_by_rule = {}
    for f in regex_findings:
        if f["id"] not in G6_RULE_IDS:
            continue  # DATA-*/OBS-* stay out of scope for this migration, per the scope doc
        regex_by_rule.setdefault(f["id"], set()).add(f.get("resource"))

    rego_by_rule = {}
    unresolved = []
    for f in rego_result["findings"]:
        if f.get("finding_kind") == "field_unresolved":
            unresolved.append(f)
            continue
        rego_by_rule.setdefault(f["id"], set()).add(f.get("resource"))

    divergence = {}
    for rule_id in G6_RULE_IDS:
        regex_resources = regex_by_rule.get(rule_id, set())
        rego_resources = rego_by_rule.get(rule_id, set())
        new_in_rego = sorted(r for r in (rego_resources - regex_resources) if r is not None)
        lost_in_regex = sorted(r for r in (regex_resources - rego_resources) if r is not None)
        # A rule whose regex form never attaches a resource at all (the original SEC-02) can't
        # be compared per-resource -- fall back to a simple presence comparison so this doesn't
        # silently read as "no divergence" when it's actually incomparable at that grain.
        regex_had_unattributed = None in regex_resources
        rego_had_any = bool(rego_resources)
        if new_in_rego or lost_in_regex or (regex_had_unattributed and not rego_had_any) or (
            rego_had_any and not regex_had_unattributed and not regex_resources
        ):
            divergence[rule_id] = {
                "new_in_rego": new_in_rego,
                "lost_in_regex": lost_in_regex,
                "regex_unattributed_finding": regex_had_unattributed,
            }

    return {
        "comparable": True,
        "divergence": divergence,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
    }


def _print_g6_shadow(result):
    if not result["comparable"]:
        print(f"[gate] G6 shadow evaluation incomplete -- regex_error={result.get('regex_error')} "
              f"rego_evaluation_failed={result['rego_evaluation_failed']} "
              f"rego_reason={result.get('rego_reason')}", file=sys.stderr)
        return
    if not result["divergence"] and not result["unresolved_count"]:
        print("[gate] G6 shadow: Rego parity with regex scan, no divergence, no unresolved fields")
        return
    print(f"[gate] G6 shadow: {len(result['divergence'])} rule(s) diverge, "
          f"{result['unresolved_count']} unresolved-field finding(s)", file=sys.stderr)
    for rule_id, d in result["divergence"].items():
        if d["new_in_rego"]:
            print(f"  - {rule_id}: NEW in Rego (not in regex): {d['new_in_rego']}", file=sys.stderr)
        if d["lost_in_regex"]:
            print(f"  - {rule_id}: LOST vs regex (regex had it, Rego doesn't): {d['lost_in_regex']}", file=sys.stderr)
    for f in result["unresolved"]:
        print(f"  - {f['id']} unresolved (unknown-until-apply): {f['resource']}", file=sys.stderr)


G9_EMULATOR_ENV = "MINUS_G9_EMULATOR"
# Explicit opt-in, deliberately not default-on. G9 runs a real terraform
# init/plan/apply/destroy cycle against an emulator container; invoking that unconditionally
# from stage_plan() would make the whole test suite (none of which runs Docker) depend on
# emulator infrastructure most environments running this gate do not have. Setting
# MINUS_G9_EMULATOR to a supported emulator name turns real ephemeral-apply on. Unset means
# "not configured", which is fail-CLOSED at the auto-approve enforcement boundary (see
# _reject_if_g9_not_clean_and_auto_approve) -- never read as "nothing to check, must be safe."


def _g9_eval(dir_, plan_json):
    """G9 (docs/phase5_scope.md): real ephemeral-apply verdict for the current plan.

    Computed once here at plan time and carried through the approval record to apply time --
    the same shape destroy already uses -- rather than re-run at apply. A real
    init/apply/destroy cycle is expensive, and running it twice per deploy yields no new
    information.

    Coverage "none" (no AWS content at all: a Databricks-only plan, or a zero-cloud-footprint
    one like the terraform_data e2e fixtures) means G9 has nothing to prove and is skipped
    cleanly, matching ephemeral_apply.py's own "none" verdict -- never reported as if G9 ran
    and passed.

    No emulator configured (MINUS_G9_EMULATOR unset, the current state here: no LocalStack
    token, and both free emulators fail IAM/KMS/S3 negative fidelity -- docs/phase5_scope.md
    section 7.5/8.6) returns a synthetic, always-non-clean verdict in the same
    {evaluation_failed, reason, ...} shape ephemeral_apply.py's _fail() produces, so the
    enforcement check and the audit record treat it identically to any other G9 failure. Do
    not special-case it into a "skip because it isn't set up" path.
    """
    if plan_json is None:
        return {"evaluation_failed": True, "reason": "plan_unreadable", "detail": "",
                "coverage": None, "databricks_resources": [], "findings": [], "emulator": None}
    coverage, databricks_addresses, _aws_addresses = ephemeral_apply.classify_coverage(plan_json)
    if coverage == "none":
        return {"evaluation_failed": False, "reason": None, "detail": "no AWS content in plan",
                "coverage": "none", "databricks_resources": databricks_addresses,
                "findings": [], "emulator": None}
    emulator = os.environ.get(G9_EMULATOR_ENV, "").strip().lower()
    if not emulator:
        return {"evaluation_failed": True, "reason": "g9_not_configured",
                "detail": f"{G9_EMULATOR_ENV} is not set -- no emulator configured for this "
                          "plan's real ephemeral-apply check", "coverage": coverage,
                "databricks_resources": databricks_addresses, "findings": [], "emulator": None}
    return ephemeral_apply.run_ephemeral_apply(dir_, emulator=emulator)


def _print_g9_result(result):
    if result.get("coverage") == "none":
        print("[gate] G9: no AWS content in plan -- ephemeral apply does not apply here")
        return
    if result.get("evaluation_failed"):
        print(f"[gate] G9 FAILED: reason={result.get('reason')} detail={result.get('detail')}",
              file=sys.stderr)
        return
    print(f"[gate] G9: real ephemeral apply clean (emulator={result.get('emulator')}, "
          f"coverage={result.get('coverage')})")


def _print_intent_assertions(result):
    """Phase 4 (docs/phase4_scope.md, G3/G4): intent-vs-reality advisory findings. ADVISORY
    ONLY -- printed and audited, never blocks stage_plan, same shadow discipline as G6."""
    if result.get("evaluation_failed"):
        print(f"[gate] Phase 4 intent-assertions evaluation failed: "
              f"{result['findings'][0]['detail'] if result['findings'] else 'unknown'}", file=sys.stderr)
        return
    findings = result.get("findings", [])
    if not findings:
        print("[gate] Phase 4 intent-assertions: no findings (advisory)")
        return
    print(f"[gate] Phase 4 intent-assertions: {len(findings)} finding(s) (advisory, non-blocking)",
          file=sys.stderr)
    for f in findings:
        print(f"  - {f['id']} [{f['finding_kind']}] {f.get('resource')}: {f['detail']}", file=sys.stderr)


def _print_classification(classification):
    if classification["autonomous_eligible"]:
        print(f"[gate] destructive-change classification: autonomous-eligible "
              f"({classification['resource_change_count']} resource change(s), all create-only)")
        return
    print(f"[gate] destructive-change classification: STAGED PATH REQUIRED "
          f"({len(classification['findings'])} finding(s))", file=sys.stderr)
    for finding in classification["findings"]:
        detail = finding["reason"]
        if finding["reason"] == "non_create_action":
            detail += f" actions={finding.get('actions')}"
        print(f"  - {finding['address']} ({finding['type']}): {detail}", file=sys.stderr)
    if classification["reduced_assurance"]:
        print(f"  - reduced assurance: {classification['reduced_assurance_reason']}", file=sys.stderr)
        for addr in classification["databricks_resources"]:
            print(f"    - {addr}", file=sys.stderr)


# --- Verify receipts ----------------------------------------------------------------------
#
# `plan` used to run on a directory `verify` had never seen. Measured: a plan hash was minted,
# an approval could be attached to it, and nothing recorded that the fmt check, `terraform
# validate` and the SEC scan had all been skipped. The G6 evaluation inside stage_plan is
# shadow-only by design and never blocked anything, so there was no second line holding it.
#
# The fix is the plan-hash pattern one stage earlier: verify binds a receipt to the SOURCE
# content, and plan refuses without a receipt matching what is on disk right now. An edit
# after verify invalidates it for exactly the reason an edit after plan voids an approval --
# the evidence describes content that is no longer there.

VERIFY_RECEIPT = "verified.json"

# Long enough that "ok", "fine" and "n/a" cannot pass for a consequence. Not a quality bar --
# no length check makes a sentence true -- but it stops the field being satisfied by a
# keystroke, which is the failure mode a free-text box actually has.
MIN_IMPACT_CHARS = 20

# Ordered weakest to strongest. A receipt satisfies a plan when it was produced under a mode
# at least as strict, so production evidence covers a dev plan but never the reverse: a dev
# verify never ran the external policy scanners production requires, and honouring it would
# report a check that did not happen.
_POLICY_STRENGTH = {"dev": 0, "production": 1}


def _verify_receipt_path(dir_):
    return os.path.join(_state_dir(dir_), VERIFY_RECEIPT)


def _source_digest(dir_):
    """One digest over the content of every source file in the directory.

    Built on source_guard.source_hashes(), which already walks the tree and hashes each file
    for the drift check -- this only folds that mapping into a single comparable value.
    """
    hashes = source_guard.source_hashes(dir_)
    canonical = json.dumps(hashes, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_verify_receipt(dir_, policy_mode):
    """Record that verify passed, and over exactly what."""
    os.makedirs(_state_dir(dir_), exist_ok=True)
    record = {
        "source_digest": _source_digest(dir_),
        "policy_mode": policy_mode,
        "canonical_dir": _canonical_dir(dir_),
        "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "verified_by": getpass.getuser(),
    }
    with open(_verify_receipt_path(dir_), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=2)
    return record


def _read_verify_receipt(dir_):
    """The receipt, or None. A corrupt record reads as ABSENT, never as valid -- treating a
    truncated file as a pass is how a disk-full event silently turns the gate off."""
    path = _verify_receipt_path(dir_)
    if not os.path.exists(path):
        return None
    try:
        record = json.load(open(path, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return record if isinstance(record, dict) else None


def _verify_state(dir_, policy_mode):
    """{ok, reason, detail} -- whether verify's evidence covers a plan of this directory now."""
    receipt = _read_verify_receipt(dir_)
    if receipt is None:
        return {"ok": False, "reason": "verify_not_run",
                "detail": "no verify receipt for this directory"}
    if receipt.get("canonical_dir") != _canonical_dir(dir_):
        return {"ok": False, "reason": "verified_another_directory",
                "detail": f"receipt was written for {receipt.get('canonical_dir')!r}"}
    if receipt.get("source_digest") != _source_digest(dir_):
        return {"ok": False, "reason": "source_changed_since_verify",
                "detail": "the Terraform source changed after verify passed"}
    have = _POLICY_STRENGTH.get(receipt.get("policy_mode"), -1)
    want = _POLICY_STRENGTH.get(policy_mode, 0)
    if have < want:
        return {"ok": False, "reason": "verified_under_weaker_policy_mode",
                "detail": f"verified under {receipt.get('policy_mode')!r}, "
                          f"planning under {policy_mode!r}"}
    return {"ok": True, "reason": None, "detail": None, "receipt": receipt}


def _record_impact(dir_, impact_text, classification):
    """Attach the author's impact statement to the pending plan, so approve can show it."""
    with _gate_state_lock(_pending_path(dir_)):
        try:
            pending = json.load(open(_pending_path(dir_), encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        pending["impact"] = {
            "statement": impact_text,
            "stated_by": authz.verified_operator() or authz.operator(),
            "stated_at": _now(),
            "finding_count": len(classification.get("findings") or []),
        }
        _write_json_atomic(_pending_path(dir_), pending)


def _clear_pending(dir_):
    """Remove the pending record. A refused plan must leave nothing approvable behind."""
    try:
        os.remove(_pending_path(dir_))
    except OSError:
        pass


def _print_impact(pending):
    """Show the impact statement to the approver, above the y/N."""
    impact = (pending or {}).get("impact") or {}
    statement = impact.get("statement")
    if not statement:
        return
    print("")
    print("  WHAT THIS CHANGES, stated by the author of the plan:")
    for line in textwrap.wrap(statement, width=76):
        print(f"    {line}")
    print(f"    -- {impact.get('stated_by')}, {impact.get('stated_at', '')[:19]}")
    print("")


def _reject_if_unverified(dir_, policy_mode):
    """True when plan must not proceed. Prints what to run rather than only what went wrong."""
    state = _verify_state(dir_, policy_mode)
    if state["ok"]:
        return False
    print(f"[gate] refusing to plan: {state['detail']}.", file=sys.stderr)
    print(f"[gate] run `minusctl gate verify --dir {dir_}` first -- a plan a human can "
          f"approve must have been format-checked, validated and security-scanned.",
          file=sys.stderr)
    _audit("plan", "REJECTED", reason=state["reason"], dir=dir_, policy_mode=policy_mode)
    return True


def _source_status_for_hash(plan_hash):
    try:
        return plan_inspector.source_status(plan_hash[:12])
    except Exception:
        return {"status": "UNKNOWN", "stale": False, "reason": "source snapshot unavailable"}


def _reject_if_source_stale(stage, dir_, plan_hash):
    status = _source_status_for_hash(plan_hash)
    if status.get("status") == "CURRENT":
        return False
    label = status.get("status") or "UNKNOWN"
    if label == "STALE":
        print("[gate] Terraform source changed after this plan was generated. Re-run `plan`.", file=sys.stderr)
    else:
        print("[gate] Terraform source provenance is unavailable for this plan. Re-run `plan`.", file=sys.stderr)
    reason = status.get("reason") or "source_drift"
    if reason:
        print(f"[gate] reason: {reason}", file=sys.stderr)
    for label in ("changed", "added", "missing"):
        items = status.get(label, [])
        if items:
            print(f"[gate] {label}: {', '.join(items[:8])}", file=sys.stderr)
    _audit(stage, "REJECTED", reason="source_drift", dir=dir_, plan_hash=plan_hash, source_status=label)
    return True


def _timed_input(prompt, timeout):
    """Cross-platform input with a timeout. Returns the line, or None on timeout."""
    print(prompt, end="", flush=True)
    box = {}

    def _reader():
        try:
            box["v"] = sys.stdin.readline()
        except Exception:
            box["v"] = None

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        print(f"\n[gate] no response within {timeout}s.")
        return None
    return (box.get("v") or "").strip()


def _identity():
    """(account/subscription id, connected) for the active cloud — proves auth without secrets."""
    try:
        return get_provider().identity()
    except Exception:
        return None, False


def _backend_team(dir_):
    """The team id this directory's remote state is scoped to, or None.

    Read from the generated backend key rather than a flag: the flag is what an operator
    TYPED, the key is what the stack actually writes. Checking the flag would let a wrong
    --team argument authorise an apply against another team's state.
    """
    for name in ("providers.tf", "backend.tf", "main.tf"):
        path = os.path.join(dir_, name)
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            continue
        match = re.search(r'key\s*=\s*"teams/([a-z0-9][a-z0-9-]*)/', text)
        if match:
            return match.group(1)
    return None


def _reject_if_wrong_team_role(dir_, posture, mode):
    """Refuse to approve when the active session is not the team's deploy role.

    Only applies to a stack whose state is team-scoped -- an unscoped stack has no team to
    check against, and inventing one would block every existing run.

    Fails CLOSED on an unreadable identity: if we cannot tell whose session this is, we
    cannot tell that it is allowed to write another squad's state. `auto-approve` gets NO
    exemption; an unattended runner is exactly the case this exists for.
    """
    team_id = _backend_team(dir_)
    if not team_id:
        return False

    record = team_resolver.resolve(team_id)
    pattern = record["deploy_role_pattern"]
    arn = (posture or {}).get("arn")
    if not arn:
        print(f"[gate] REFUSED - state is scoped to team {team_id!r} but the active identity "
              f"could not be read, so it cannot be shown to be that team's deploy role.",
              file=sys.stderr)
        return True
    if team_resolver.role_matches(arn, pattern):
        print(f"[gate] team role OK: {arn} matches {pattern}")
        return False
    print(f"[gate] REFUSED - this session is not authorised to apply team {team_id!r}.",
          file=sys.stderr)
    print(f"        active session : {arn}", file=sys.stderr)
    print(f"        required role  : {pattern}", file=sys.stderr)
    if not record["configured"]:
        print(f"        (no entry for {team_id!r} in {team_resolver.config_path()}; the "
              "pattern above is the default. Add the team to override it.)", file=sys.stderr)
    return True


def _credential_posture():
    """Active credential posture for the apply session (temporary vs long-term)."""
    try:
        return get_provider().credential_posture()
    except Exception:
        return {"connected": False, "type": "unknown"}


def _reject_if_weak_credentials(dir_, posture, policy_mode="dev", destroy=False):
    """
    Enforce the product's MFA-gated-deploy promise: apply must run on a TEMPORARY
    session (SSO / assumed MFA role), never long-term static keys or root. Override
    with MINUS_ALLOW_STATIC_CREDS=1 (recorded as a downgrade in the audit trail).
    """
    ctype = posture.get("type")
    if ctype not in ("long_term", "root"):
        return False
    allow = os.environ.get("MINUS_ALLOW_STATIC_CREDS", "").strip().lower() in ("1", "true", "yes")
    # Dev only: the override is honored as an audited downgrade.
    if allow and policy_mode != "production":
        print(f"[gate] WARNING: applying with {ctype} credentials "
              "(MINUS_ALLOW_STATIC_CREDS override).", file=sys.stderr)
        _audit("apply", "WARN", reason="weak_credentials_override", dir=dir_, cred_type=ctype,
               destroy=destroy)
        return False
    # Production: the override is not honored — a temporary MFA-gated session is required.
    if allow and policy_mode == "production":
        print("[gate] refusing apply (production): MINUS_ALLOW_STATIC_CREDS is not honored in "
              "production. Use a temporary MFA-gated session (`aws sso login` or assume your "
              "deploy role).", file=sys.stderr)
        _audit("apply", "REJECTED", reason="static_creds_override_denied_in_production",
               dir=dir_, cred_type=ctype, destroy=destroy)
        return True
    print(f"[gate] refusing apply: this session uses {ctype} credentials. The MFA-gated "
          "deploy guarantee requires a temporary session — authenticate with `aws sso login` "
          "or assume your MFA-gated deploy role. (Override: MINUS_ALLOW_STATIC_CREDS=1, audited "
          "and honored in dev only.)", file=sys.stderr)
    _audit("apply", "REJECTED", reason="weak_credentials", dir=dir_, cred_type=ctype, destroy=destroy)
    return True


def _reject_if_nonsandbox_dev(dir_, account, policy_mode, destroy=False):
    """
    Dev-mode controls are deliberately weaker, so dev applies are only allowed into
    known sandbox accounts. MINUS_SANDBOX_ACCOUNTS (comma-separated account ids)
    declares them: unset -> loud audited warning (phase 1); set and the target is not
    listed -> refuse (phase 2, enforced). Production mode has its own controls.
    """
    if policy_mode == "production":
        return False
    raw = os.environ.get("MINUS_SANDBOX_ACCOUNTS", "").strip()
    if not raw:
        print(f"[gate] WARNING: dev policy mode and MINUS_SANDBOX_ACCOUNTS is not set — cannot "
              f"confirm account {account} is a sandbox. Declare your sandbox accounts "
              "(MINUS_SANDBOX_ACCOUNTS=111111111111,222222222222) or use --policy-mode production.",
              file=sys.stderr)
        _audit("apply", "WARN", reason="dev_mode_sandbox_unverified", dir=dir_, account=str(account),
               destroy=destroy)
        return False
    sandboxes = {a.strip() for a in raw.split(",") if a.strip()}
    if str(account) in sandboxes:
        return False
    print(f"[gate] refusing apply: dev policy mode targets account {account}, which is not in "
          "MINUS_SANDBOX_ACCOUNTS. Governed accounts require --policy-mode production.",
          file=sys.stderr)
    _audit("apply", "REJECTED", reason="dev_mode_nonsandbox_account", dir=dir_, account=str(account),
           destroy=destroy)
    return True


def _reject_if_apply_identity_mismatches_approver(dir_, approval, policy_mode, destroy=False):
    """Require the credentials running `apply` to belong to the recorded approver.

    The approval record proves WHO approved a plan hash; without this check two different
    people can satisfy "someone approved" and "someone applied" without ever being the same
    person. Compares the current apply-time verified AWS identity against the approver
    recorded at approval time.

    Only compares when BOTH sides are real, AWS-STS-verified identities (not the
    env-var/OS-user fallback) -- an unverifiable approval predates this feature or ran with
    no cloud session, and re-litigating it here would produce false rejections for a setup
    that was never broken in the first place. Same graduated strictness as the rest of this
    file: production refuses on mismatch, dev only warns (single-operator dev sessions
    legitimately re-authenticate between approve and apply).
    """
    current_identity = authz.verified_operator()
    approved_identity = approval.get("approver_verified_identity")
    if not current_identity or not approved_identity:
        return False  # nothing verifiable to compare; don't invent a rejection
    if current_identity == approved_identity:
        return False
    if policy_mode == "production":
        print(f"[gate] refusing apply (production): this session is authenticated as "
              f"{current_identity}, but the plan was approved by {approved_identity}. Apply "
              "must run under the same verified identity that approved it.", file=sys.stderr)
        _audit("apply", "REJECTED", reason="apply_identity_mismatches_approver", dir=dir_,
               approved_identity=approved_identity, apply_identity=current_identity, destroy=destroy)
        return True
    print(f"[gate] WARNING: this session is authenticated as {current_identity}, but the plan "
          f"was approved by {approved_identity}. Proceeding (dev mode) -- use --policy-mode "
          "production to enforce this.", file=sys.stderr)
    _audit("apply", "WARN", reason="apply_identity_mismatches_approver_dev", dir=dir_,
           approved_identity=approved_identity, apply_identity=current_identity, destroy=destroy)
    return False


def _clear_approvals(dir_, plan_hash=None):
    try:
        if plan_hash:
            path = _approved_path(dir_, plan_hash)
            if os.path.exists(path):
                os.remove(path)
            return
        approvals = _approval_dir(dir_)
        if os.path.isdir(approvals):
            for name in os.listdir(approvals):
                if name.endswith(".json"):
                    os.remove(os.path.join(approvals, name))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------
def stage_verify(dir_, policy_mode=None):
    print("== verify ==")
    policy_mode = _policy_mode(policy_mode)
    rc, _, err = _tf(dir_, "fmt", "-check", capture=True)
    if rc != 0:
        print("[gate] terraform fmt -check failed (run `terraform fmt`).", file=sys.stderr)
        _audit("verify", "FAILED", reason="fmt", dir=dir_)
        return False
    # Install providers (without configuring the remote backend) so validate can run.
    rc, _, err = _tf(dir_, "init", "-backend=false", "-input=false", capture=True)
    if rc != 0:
        print(f"[gate] terraform init (providers) failed:\n{err}", file=sys.stderr)
        _audit("verify", "FAILED", reason="init", dir=dir_)
        return False
    rc, _, err = _tf(dir_, "validate", capture=True)
    if rc != 0:
        print(f"[gate] terraform validate failed:\n{err}", file=sys.stderr)
        _audit("verify", "FAILED", reason="validate", dir=dir_)
        return False
    if os.path.exists(SCAN):
        rc, out, err = _run([
            sys.executable, SCAN, "--source-dir", dir_,
            "--log-dir", LOG_DIR,
            "--policy-mode", policy_mode,
        ], capture=True)
        print(f"[gate] security scan complete ({policy_mode}) -> see {os.path.join('.agents', 'logs', 'optimization_report.md')}")
        if rc != 0:
            if out:
                print(out, file=sys.stderr)
            if err:
                print(err, file=sys.stderr)
            _audit("verify", "FAILED", reason="scan", dir=dir_, policy_mode=policy_mode)
            return False
    # G6 is a verify-stage AVAILABILITY requirement, not a blanket blocker. Two separate facts,
    # deliberately kept apart:
    #
    #   1. Can policy be evaluated at all? In production, OPA being absent means the run can
    #      make no claim about Rego compliance, and printing "verify OK" while the evaluator is
    #      missing is exactly the false assurance this check removes -- so it fails hard. In
    #      standard mode it stays a warning; a developer without OPA must still be able to
    #      iterate.
    #
    #   2. Does a violated rule stop the run? That stays with the per-rule promotion registry
    #      (policy/rule_stages.json, enforced at plan by _reject_if_promoted_policy_violated).
    #      Do not flip every rule to blocking here -- see the G6 coverage note in stage_plan for
    #      why. The path is shadow -> warn -> enforce, per rule, on an attributable promotion.
    if not _reject_if_policy_engine_unavailable(dir_, policy_mode):
        return False

    receipt = _write_verify_receipt(dir_, policy_mode)
    print("[gate] verify OK")
    _audit("verify", "OK", dir=dir_, policy_mode=policy_mode,
           source_digest=receipt["source_digest"])
    return True


def _reject_if_policy_engine_unavailable(dir_, policy_mode):
    """True to continue. In production mode an unusable Rego engine fails verify.

    Checked here rather than at plan because verify is where an operator learns whether this
    machine can produce a governed result at all -- discovering it after a plan has already
    been recorded wastes the plan and buries the reason.
    """
    opa = toolpath.find_tool("opa")
    stages = rule_stages.summary() if hasattr(rule_stages, "summary") else None
    if opa:
        detail = f"opa at {opa}"
        if stages:
            detail += f"; rule stages: {stages}"
        print(f"[gate] policy engine available ({detail})")
        return True

    if policy_mode == "production":
        print("[gate] REFUSED - production policy mode requires OPA, and it is not on PATH.",
              file=sys.stderr)
        print("        Without it no Rego rule is evaluated, so a passing verify would be "
              "asserting a compliance check that never ran.", file=sys.stderr)
        print("        Install OPA, or run with --policy-mode standard and accept that "
              "G6 findings are unavailable.", file=sys.stderr)
        _audit("verify", "FAILED", reason="opa_missing_production", dir=dir_,
               policy_mode=policy_mode)
        return False

    print("[gate] policy engine UNAVAILABLE (opa not on PATH) -- G6 rules are not evaluated "
          "in this run. Production policy mode refuses this state.")
    return True


def _check_coverage(dir_, plan_hash_, policy_mode):
    """Cost-coverage audit: every resource type in the plan must be auto-priced, mapped
    (needs a reviewed usage profile), or confirmed free — never silently absent. Dev mode
    warns loudly; production treats an unresolved resource type as a blocking finding, same
    posture as the two-person-rule / weak-credential checks elsewhere in this gate."""
    try:
        import reporter
        import coverage_audit
        report_dir = os.path.join(reporter.reports_root_for_dir(dir_), plan_hash_[:12])
        coverage = coverage_audit.audit(report_dir)
    except Exception as exc:
        print(f"[gate] (coverage audit skipped: {exc})", file=sys.stderr)
        return True
    unresolved = coverage.get("unresolved") or []
    if not unresolved:
        return True
    names = ", ".join(f"{u['resource_type']} x{u['count']}" for u in unresolved)
    if policy_mode == "production":
        print(f"[gate] refusing plan (production): unresolved cost coverage for: {names}. "
              "Add these to core/cost/pricing_data/aws_resource_map.json (priced) or "
              "core/cost/pricing_data/free_resources.json (confirmed free) after checking the AWS "
              "Price List catalog — never guess.", file=sys.stderr)
        _audit("plan", "REJECTED", reason="unresolved_cost_coverage", dir=dir_,
               plan_hash=plan_hash_, unresolved=names)
        return False
    print(f"[gate] WARNING: unresolved cost coverage for: {names} — these resource types have "
          "no known AWS service mapping, so they are silently excluded from the cost report. "
          "Run `python core/cost/coverage_audit.py audit --report-dir {}`.".format(report_dir),
          file=sys.stderr)
    _audit("plan", "WARN", reason="unresolved_cost_coverage", dir=dir_,
           plan_hash=plan_hash_, unresolved=names)
    return True


TELEMETRY_ENV = "MINUS_TELEMETRY"


def _telemetry_requested(with_telemetry=False):
    """Opt-in only: the flag, or MINUS_TELEMETRY=1 for CI where there is no flag to type but
    there are ambient role credentials.

    Off is the default because the common case is an offline plan on a laptop with no
    credentials, and a gate that reaches for CloudTrail there is slower for no answer."""
    if with_telemetry:
        return True
    return os.environ.get(TELEMETRY_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _drift_for_plan(plan_json, with_telemetry=False):
    """Classify cloud drift, correlating it with CloudTrail/Glue telemetry only on request.

    The lookup is passed in rather than looked up inside cloud_drift so the default path
    makes no AWS call at all -- see that module on why correlation is advisory and
    fail-open. A telemetry failure never changes the verdict."""
    telemetry_fn = cloud_drift.aws_telemetry if _telemetry_requested(with_telemetry) else None
    return cloud_drift.classify(plan_json or {}, telemetry=telemetry_fn)


def stage_plan(dir_, policy_mode=None, destroy=False, with_telemetry=False,
               impact=None):
    """destroy=True governs teardown through the exact same hash-bind -> approve -> apply loop
    as create/modify, so teardown is never a raw `terraform destroy` with no plan-hash binding,
    no RBAC, and no audit chain. A destroy plan's resource_changes carry actions=["delete"],
    which the rest of the pipeline handles unchanged: `apply -json` on a saved destroy plan
    emits the same apply_start/apply_complete stream, and _ACTION_VERB/_ACTION_DONE already
    render "delete" as Destroying/destruction."""
    print("== plan (destroy) ==" if destroy else "== plan ==")
    policy_mode = _policy_mode(policy_mode)
    if _reject_if_unverified(dir_, policy_mode):
        return False
    plan_args = ["plan", f"-out={PLAN_FILE}"]
    if destroy:
        plan_args.insert(1, "-destroy")
    rc, _, err = _tf(dir_, *plan_args)
    if rc != 0:
        print(f"[gate] terraform plan failed:\n{err}", file=sys.stderr)
        _audit("plan", "FAILED", dir=dir_, destroy=destroy)
        return False
    h, herr = _plan_hash(dir_)
    if not h:
        print(f"[gate] could not hash plan: {herr}", file=sys.stderr)
        _audit("plan", "FAILED", reason="hash", dir=dir_, destroy=destroy)
        return False
    os.makedirs(_state_dir(dir_), exist_ok=True)
    _write_json_atomic(_pending_path(dir_), {
            "plan_hash": h,
            "dir": dir_,
            "canonical_dir": _canonical_dir(dir_),
            # Prefer the AWS-STS-verified identity (cannot be spoofed by MINUS_OPERATOR) so
            # the two-person production rule below compares real authenticated principals,
            # not two self-reported strings. Falls back to operator() when no cloud session
            # is active yet, e.g. dev-mode planning before credentials are configured.
            "planner": authz.verified_operator() or authz.operator(),
            "created": _now(),
            "destroy": destroy,
    })
    _clear_approvals(dir_)  # a new plan for this dir invalidates prior approvals

    # Destructive-change classification (core/governance/destructive_change_gate.py, Phase 1 of
    # the generation-time-authoring gate stack). SHADOW ONLY here: logged and printed, never
    # blocks stage_plan -- enforcement (refusing an auto-approve apply that isn't
    # autonomous-eligible) lives in stage_apply below, the actual mutation point.
    classification = _classify_plan(dir_)
    _print_classification(classification)

    # A staged plan must arrive with a written impact statement (FR: forced articulation).
    #
    # The gate already KNEW the plan was staged and printed why -- machine-readable findings
    # naming the address and the reason. What nobody had to do was say what BREAKS. A human
    # reading "STAGED PATH REQUIRED (3 finding(s))" over a 400-resource diff is being asked to
    # rubber-stamp, and a y/N on a diff nobody can hold in their head is not review.
    #
    # So the author states the consequence at plan time, and the human reads that sentence at
    # approve time. Modelled on the awslabs ccapi-mcp-server, which requires an explain() call
    # describing deletion impact before it will accept a confirmation.
    if not classification.get("autonomous_eligible", False):
        impact_text = (impact or "").strip()
        if len(impact_text) < MIN_IMPACT_CHARS:
            print(f"[gate] refusing to stage this plan: it is not autonomous-eligible and "
                  f"carries no impact statement.", file=sys.stderr)
            print(f"[gate] re-run with --impact \"<what breaks, and for whom>\" "
                  f"({MIN_IMPACT_CHARS} characters minimum). The approver reads this "
                  f"sentence, not the {len(classification['findings'])} finding(s) above.",
                  file=sys.stderr)
            _audit("plan", "REJECTED", reason="staged_plan_without_impact_statement",
                   dir=dir_, plan_hash=h, destroy=destroy)
            _clear_pending(dir_)
            return False
        _record_impact(dir_, impact_text, classification)

    # Announced only once the plan has actually survived staging. Printing it earlier said
    # "plan saved" and then refused two lines later, and an operator reading the tail of the
    # output saw a success that had been discarded.
    print(f"[gate] plan saved. plan_hash = {h[:16]}...")

    # G6 (docs/g6_scope.md): SEC-*/COST-* rules over real plan JSON via OPA/Rego, run alongside
    # the regex-over-HCL scan (core/reporting/optimize_analyzer.py, invoked separately in
    # stage_verify above). SHADOW ONLY: this never blocks stage_plan and never enforces.
    # Enforcement stays where it already is, in optimize_analyzer.py's own SEC- prefix check.
    #
    # WHY IT IS STILL SHADOW. Parity is not the blocker -- tests/test_rego_gate.py::
    # test_g6_zero_false_positives_across_real_catalog proves zero unexpected false positives
    # across the real module catalog using real terraform plans, green in CI (opa v1.18.2
    # pinned). COVERAGE is the blocker: the tracked rule IDs reach only a small minority of the
    # resource types reviewed in destructive_change_gate.py (STATEFUL_RESOURCE_TYPES +
    # IAM_RESOURCE_TYPES + REVIEWED_UNSAFE_TYPES + AUTO_SHIP_ELIGIBLE_TYPES), and most of the
    # stateful danger set has no rule at all. Enforcing at that reach reads as "policy is
    # enforced" while the large remainder passes unexamined -- worse than an honest shadow
    # gate. Widen coverage first, then go shadow -> warn -> enforce; never shadow -> enforce.
    plan_json_for_g6, plan_json_err = _plan_json(dir_)
    g6_result = _g6_shadow_eval(dir_, plan_json_for_g6) if plan_json_for_g6 is not None else {
        "comparable": False, "regex_error": None,
        "rego_evaluation_failed": True, "rego_reason": "plan_unreadable", "rego_detail": plan_json_err,
    }
    _print_g6_shadow(g6_result)

    # The shadow -> warn -> enforce path the comment above calls for, made per-rule rather
    # than all-or-nothing. Every rule defaults to warn, and only a
    # human promotion in policy/rule_stages.json lets one block. That is what makes accepting
    # agent-authored rules safe: adding a rule cannot change what ships until someone signs
    # off on it. Today every rule is seeded at warn, so this is a no-op until first promotion.
    if plan_json_for_g6 is not None:
        _rego_for_promotion = rego_gate.evaluate(plan_json_for_g6)
        if _reject_if_promoted_policy_violated(dir_, _rego_for_promotion, destroy):
            return False

    # G9 (docs/phase5_scope.md): unlike G6, NOT shadow-only -- a not-clean verdict blocks the
    # auto-approve path (see _reject_if_g9_not_clean_and_auto_approve in stage_apply). Skipped
    # for destroy plans on the same reasoning as the cost-coverage skip below: G9 catches
    # create-order apply-time failures, and a teardown gives it nothing new to check. Computed
    # once here and carried pending record -> approval record -> stage_apply, never re-run at
    # apply; a real init/apply/destroy cycle is expensive and the second run adds no
    # information.
    g9_result = _g9_eval(dir_, plan_json_for_g6) if not destroy else {
        "evaluation_failed": False, "reason": None, "detail": "destroy plan -- G9 not applicable",
        "coverage": None, "databricks_resources": [], "findings": [], "emulator": None,
    }
    _print_g9_result(g9_result)
    # Merge into the pending record already written above rather than recomputing it (planner
    # identity can be a real AWS STS call -- doing that twice per plan would be pure waste).
    # Did the CLOUD change outside Terraform, and does this plan undo it? Computed from the
    # same plan JSON, carried to apply like g9_result.
    drift_result = _drift_for_plan(plan_json_for_g6, with_telemetry=with_telemetry)
    print(cloud_drift.format_result(drift_result))

    # A rename-shaped destroy+create of a STATEFUL resource is data loss wearing an ordinary
    # update's clothes. Blocks the PLAN outright rather than
    # deferring to apply -- unlike the auto-approve checks, there is no mode in which
    # silently destroying a bucket is the intended outcome, so a human reviewing it at
    # approve time is not a sufficient answer. The fix is one `moved` block.
    churn_result = address_churn.classify(
        plan_json_for_g6 or {}, moved_blocks=address_churn.read_moved_blocks(dir_))

    # All three verdicts merge in ONE locked read-modify-write. Computed above, outside the
    # lock, because none of them touch the file and two shell out to terraform -- holding the
    # lock across that would serialise unrelated work. Held end to end here so a second
    # operator planning the same directory cannot land a write between the read and the
    # write, silently dropping whichever verdict lost the race.
    with _gate_state_lock(_pending_path(dir_)):
        try:
            pending_for_update = json.load(open(_pending_path(dir_), encoding="utf-8"))
        except Exception:
            pending_for_update = {}
        pending_for_update["g9_result"] = g9_result
        pending_for_update["cloud_drift"] = drift_result
        pending_for_update["address_churn"] = churn_result
        _write_json_atomic(_pending_path(dir_), pending_for_update)
    if churn_result["blocked"]:
        print(address_churn.format_result(churn_result), file=sys.stderr)
        _audit("plan", "REJECTED", reason="rename_shaped_address_churn", dir=dir_,
               destroy=destroy, address_churn=churn_result)
        return False
    if churn_result["advisory"]:
        print(address_churn.format_result(churn_result))

    # Phase 4 (docs/phase4_scope.md, G3/G4): intent-vs-reality advisory checks. ADVISORY ONLY
    # -- never blocks stage_plan, same shadow discipline as G6. requirements.json and
    # architecture_decision.json are looked up in dir_'s parent (the run root, matching
    # runs.new_run()'s terraform_dir = root/"terraform" convention); their absence just means
    # this run is not part of the requirements-first workflow, not an error.
    #
    # check_controls is deliberately NOT wired here. The demo blueprint's synthetic plan
    # (demo.py's synthetic_plan(), not a real terraform plan) has no `configuration` key, so
    # its two sibling-reference checks (public access blocks, versioning/lifecycle) would
    # false-positive on every demo run regardless of real correctness. check_module_presence
    # and check_numerics need only resource_changes and are wired.
    run_root = os.path.dirname(os.path.normpath(dir_))
    requirements_record = reqgate.load(run_root)
    architecture_decision_record = adecision.load(run_root)
    intent_result = intent_assertions.evaluate(
        requirements=requirements_record, architecture_decision=architecture_decision_record,
        plan_json=plan_json_for_g6,
    ) if plan_json_for_g6 is not None else {
        "advisory": True, "evaluation_failed": True,
        "findings": [{"id": "INTENT-PLAN-UNREADABLE", "detail": plan_json_err}],
    }
    _print_intent_assertions(intent_result)

    _audit("plan", "OK", plan_hash=h, dir=dir_, destroy=destroy,
           destructive_classification=classification, g6_shadow=g6_result,
           g9_result=g9_result, intent_assertions=intent_result)

    # Auto-generate the versioned deploy report (plan + cost + architecture).
    # Informational — a report failure must never fail the plan.
    try:
        import reporter
        reporter.generate(dir_)
    except Exception as e:
        print(f"[gate] (report skipped: {e})", file=sys.stderr)
        return True

    if destroy:
        # Cost coverage exists to catch under-priced resources being CREATED; every type in a
        # destroy plan was already priced correctly to get created in the first place, so this
        # check has nothing meaningful to add here and would only risk a confusing,
        # wrong-direction block on a teardown in production mode.
        return True
    return _check_coverage(dir_, h, policy_mode)


def _enforce_production_approval(dir_, policy_mode, approver, authz_mode, pending, plan_hash):
    """Production controls (enforced): approvals must be attributable and segregated.

    Returns True if approval may proceed, False if it must be blocked. In production
    an approver allowlist is required, and the approver must be a principal distinct
    from the planner (two-person rule); a plan with no recorded planner cannot prove
    that separation and is refused. Dev mode always proceeds.
    """
    if policy_mode != "production":
        return True
    if authz_mode == "open":
        print("[gate] refusing approval (production): no approver allowlist configured. "
              "Set MINUS_APPROVERS or .minus/approvers.json so approvals are attributable.",
              file=sys.stderr)
        _audit("approve", "REJECTED", reason="open_allowlist_in_production",
               plan_hash=plan_hash, dir=dir_, approver=approver)
        return False
    planner = (pending or {}).get("planner")
    if not planner:
        print("[gate] refusing approval (production): plan has no recorded planner to enforce "
              "two-person separation. Re-run `plan`, then approve as a different principal.",
              file=sys.stderr)
        _audit("approve", "REJECTED", reason="missing_planner_in_production",
               plan_hash=plan_hash, dir=dir_, approver=approver)
        return False
    if planner == approver:
        print(f"[gate] refusing approval (production): {approver} cannot approve their own plan "
              "(two-person rule). A different authorized principal must approve.", file=sys.stderr)
        _audit("approve", "REJECTED", reason="self_approval_in_production",
               plan_hash=plan_hash, dir=dir_, approver=approver, planner=planner)
        return False
    return True


def _warn_if_over_budget(dir_, plan_hash_):
    """The plan provisions its OWN budget guardrail; approving a forecast that already
    exceeds it must be a conscious act. Loud warning + audit record — a reviewer seeing
    the approval trail sees the operator approved over budget knowingly."""
    try:
        import reporter
        report_dir = os.path.join(reporter.reports_root_for_dir(dir_), plan_hash_[:12])
        cost = reporter.load_bcm_estimate(report_dir)
        if not cost or not cost.get("ok"):
            return
        total = float(cost.get("monthly_total_usd") or 0)
        budget = cost.get("monthly_budget_usd")
        if budget and total > float(budget):
            pct = total / float(budget) * 100
            print(f"[gate] WARNING: the AWS forecast (${total:,.2f}/mo) is {pct:.0f}% of this "
                  f"plan's own budget guardrail (${float(budget):,.2f}/mo aws_budgets_budget). "
                  "Raise monthly_budget_usd and re-plan, or approve knowingly — this warning "
                  "is recorded in the audit chain.", file=sys.stderr)
            _audit("approve", "WARN", reason="forecast_exceeds_budget", dir=dir_,
                   forecast_usd=total, budget_usd=float(budget), utilization_pct=round(pct))
    except Exception:
        pass


def _reject_if_not_asserted_role(posture, role_arn):
    """Refuse to approve unless the ambient session IS the role the operator asserted.

    `--role-arn` states which role this approval is being made under. It is an ASSERTION the
    gate verifies, never a role the gate assumes: this process handles no credentials, and
    minting a session here would put secrets in a tool whose whole contract is that it has
    none. Same shape as _reject_if_wrong_team_role, which verifies the same posture against
    the state key.

    There is deliberately no `--mfa-arn` companion. `sts get-caller-identity` returns
    Account, Arn and UserId -- no MFA claim -- so the gate cannot verify one. MFA is enforced
    by the deploy role's trust policy (`aws:MultiFactorAuthPresent`) at AssumeRole time,
    upstream of anything this process can see. A flag accepted and never checked would be an
    unverified assertion sitting in an audit record, which is worse than no flag at all.

    Fails closed on an unreadable identity: if we cannot tell whose session this is, we
    cannot tell it is the asserted role.
    """
    if not role_arn:
        return False
    arn = (posture or {}).get("arn")
    if not arn:
        print("[gate] refusing approve: --role-arn was asserted but the active session "
              "identity could not be read. Run `aws sts get-caller-identity` to check your "
              "credentials.", file=sys.stderr)
        return True
    if team_resolver.role_matches(arn, role_arn):
        return False
    print(f"[gate] refusing approve: --role-arn asserted {role_arn}, but this session is "
          f"{arn}. Assume the right role, or drop the assertion.", file=sys.stderr)
    return True


def gate_status(dir_):
    """Recorded gate state for a directory, read from disk only (PRD v6 FR-04).

    Deliberately does NOT re-hash the plan. Doing so shells out to `terraform show` on every
    call, which needs an initialised directory and turns a status check into a slow,
    credential-shaped operation -- and nobody runs a status command that takes ten seconds.
    What this reports is what the gate RECORDED: the pending plan hash, whether an approval
    exists bound to that exact hash, and the verdicts carried alongside it.

    An approval for a different hash is not an approval. That is the whole point of the hash
    binding, and reporting it as approved here would undo it in the one place an operator
    looks before running apply.
    """
    state = _state_dir(dir_)
    pending = {}
    pending_path = _pending_path(dir_)
    if os.path.exists(pending_path):
        try:
            pending = json.load(open(pending_path, encoding="utf-8")) or {}
        except Exception:
            pending = {}

    plan_hash = pending.get("plan_hash")
    approval = {}
    if plan_hash and os.path.exists(_approved_path(dir_, plan_hash)):
        try:
            approval = json.load(open(_approved_path(dir_, plan_hash), encoding="utf-8")) or {}
        except Exception:
            approval = {}

    drift = pending.get("cloud_drift") or {}
    g9 = pending.get("g9_result") or {}
    churn = pending.get("address_churn") or {}
    return {
        "dir": dir_,
        "state_dir": state,
        "planned": bool(plan_hash),
        "plan_hash": plan_hash,
        "planned_at": pending.get("planned_at") or pending.get("created_at"),
        "approved": bool(approval),
        "approver": approval.get("approver"),
        "approved_at": approval.get("approved_at"),
        "approval_mode": approval.get("approval_mode"),
        "drift_count": drift.get("drift_count"),
        "reverts_out_of_band_changes": bool(drift.get("reverts_out_of_band_changes")),
        "telemetry_available": bool(drift.get("telemetry_available")),
        "g9": g9.get("reason") or g9.get("detail"),
        "address_churn_blocked": bool(churn.get("blocked")),
        "next": _status_next(bool(plan_hash), bool(approval)),
    }


def _status_next(planned, approved):
    if not planned:
        return "minusctl gate verify, then minusctl gate plan"
    if not approved:
        return "minusctl gate approve"
    return "minusctl gate apply"


def format_status(status):
    """ASCII only (NFR-01). Two columns, because this is read at a glance before an apply."""
    rows = [
        ("directory", status["dir"]),
        ("planned", "yes" if status["planned"] else "no"),
        ("plan hash", (status["plan_hash"] or "-")[:16] + ("..." if status["plan_hash"] else "")),
        ("approved", "yes" if status["approved"] else "no"),
        ("approver", status["approver"] or "-"),
        ("approved at", status["approved_at"] or "-"),
        ("cloud drift", "-" if status["drift_count"] is None else str(status["drift_count"])),
        ("reverts out-of-band", "YES" if status["reverts_out_of_band_changes"] else "no"),
        ("G9", status["g9"] or "-"),
        ("next", status["next"]),
    ]
    width = max(len(label) for label, _ in rows)
    lines = ["== gate status =="]
    lines.extend(f"  {label.ljust(width)}  {value}" for label, value in rows)
    return "\n".join(lines)


def stage_approve(dir_, mode="gatekeeper", policy_mode=None, role_arn=None):
    policy_mode = _policy_mode(policy_mode)
    print("== approve ==")
    h, herr = _plan_hash(dir_)
    if not h:
        print(f"[gate] no valid plan to approve ({herr}). Run `plan` first.", file=sys.stderr)
        return False
    _warn_if_over_budget(dir_, h)

    # Before recording any approval, prove this session may write this team's state. Placed
    # here rather than at apply so an unauthorised operator never produces an approval record
    # at all -- an approval that exists is one somebody can later act on.
    posture = _credential_posture()
    if _reject_if_wrong_team_role(dir_, posture, mode):
        return False
    # Checked here, alongside the team-role check and for the same reason: an approval that
    # exists is one somebody can later act on, so an unauthorised session must never produce
    # one.
    if _reject_if_not_asserted_role(posture, role_arn):
        _audit("approve", "REJECTED", reason="asserted_role_mismatch", dir=dir_,
               asserted_role=role_arn)
        return False

    pending = {}
    pending_path = _pending_path(dir_)
    if os.path.exists(pending_path):
        try:
            pending = json.load(open(pending_path, encoding="utf-8"))
        except Exception:
            pending = {}
    if (pending.get("plan_hash") != h
            or pending.get("canonical_dir") != _canonical_dir(dir_)):
        print("[gate] current plan does not match the last recorded plan. Re-run `plan`.", file=sys.stderr)
        _audit("approve", "REJECTED", reason="stale_plan", dir=dir_)
        return False
    if _reject_if_source_stale("approve", dir_, h):
        return False

    # RBAC: enforce the approver allowlist (if configured) before recording approval.
    # Same AWS-STS-verified preference as the planner identity above: an approver
    # can't just set MINUS_OPERATOR to satisfy the two-person rule against a planner
    # who was themselves recorded with a real, cryptographically-authenticated identity.
    approver_verified_identity = authz.verified_operator()
    approver = approver_verified_identity or authz.operator()
    allowed, authz_mode, authz_reason = authz.authorize(approver, workspace=WORKSPACE)
    if not allowed:
        print(f"[gate] {approver} is not an authorized approver ({authz_reason}). Refusing.", file=sys.stderr)
        _audit("approve", "DENIED_NOT_AUTHORIZED", plan_hash=h, dir=dir_, approver=approver, authz_mode=authz_mode)
        return False

    if not _enforce_production_approval(dir_, policy_mode, approver, authz_mode, pending, h):
        return False

    account, connected = _identity()
    print(f"  plan_hash : {h}")
    print(f"  dir       : {dir_}")
    print(f"  identity  : {account if connected else 'NOT AUTHENTICATED'}")
    print(f"  approver  : {approver} ({authz_mode})")
    print(f"  mode      : {mode}")
    _print_impact(pending)
    if not connected:
        print("[gate] WARNING: no active cloud session. Authenticate before apply "
              "(`aws sso login`, or assume the MFA-gated deploy role).")

    destroy = pending.get("destroy", False)
    if mode == "auto-approve" and destroy:
        print("[gate] REFUSING auto-approve — teardowns and destroy plans cannot be auto-approved by any agent or harness.", file=sys.stderr)
        print("[gate] Teardowns require interactive human review. Run `approve` interactively with --mode gatekeeper.", file=sys.stderr)
        _audit("approve", "REJECTED", reason="destroy_auto_approve_forbidden", dir=dir_, destroy=True)
        return False

    if mode == "gatekeeper":
        ans = _timed_input(f"Approve this exact plan? [y/N] ({CONFIRM_TIMEOUT}s): ", CONFIRM_TIMEOUT)
        if ans is None or ans.lower() not in ("y", "yes"):
            print("[gate] approval declined.")
            _audit("approve", "DENIED", plan_hash=h, dir=dir_)
            return False

    record = {
        "plan_hash": h,
        "dir": dir_,
        "canonical_dir": _canonical_dir(dir_),
        "identity": account,
        "cloud": get_provider().name,
        "approved_by": getpass.getuser(),
        "approver": approver,
        "approval_mode": mode,
        # Set ONLY when the approver came from a real AWS-STS identity, never the env-var
        # fallback. That lets apply time distinguish "verify this matches" from "nothing to
        # verify", instead of comparing two fallback strings and calling it a security check.
        "approver_verified_identity": approver_verified_identity,
        "authz_mode": authz_mode,
        "approved_at": _now(),
        # Carried forward from the plan-stage pending record so the apply-stage audit record
        # says whether this was a teardown. Without it a reviewer reading only the apply trail
        # cannot tell create from destroy without cross-referencing the plan record.
        "destroy": pending.get("destroy", False),
        # Carried forward the same way: stage_apply's auto-approve enforcement reads this
        # rather than re-running an expensive ephemeral-apply cycle.
        "g9_result": pending.get("g9_result"),
        # Same carry-forward: the reviewer approved a plan whose out-of-band-revert status was
        # known at plan time; apply enforces against that recorded verdict.
        "cloud_drift": pending.get("cloud_drift"),
    }
    os.makedirs(_approval_dir(dir_), exist_ok=True)
    _write_json_atomic(_approved_path(dir_, h), record)
    print("[gate] approved — bound to this plan hash. (No credentials stored.)")
    _audit("approve", "APPROVED", plan_hash=h, dir=dir_, identity=account, approver=approver, authz_mode=authz_mode)
    return True


def _reject_if_audit_chain_tampered():
    """Refuse to apply when the audit trail has been edited, reordered, or truncated.

    This call is what makes audit_chain.verify() load-bearing rather than opt-in: without it,
    tamper-evidence exists but nothing in the deploy path ever consults it."""
    audit_path = os.path.join(LOG_DIR, "audit.jsonl")
    ok, errors = audit_chain.verify(audit_path)
    if ok:
        return False
    print("[gate] REFUSING TO APPLY — the audit trail has been tampered with:", file=sys.stderr)
    for err in errors[:5]:
        print(f"  - {err}", file=sys.stderr)
    print(f"[gate] Investigate {audit_path} before proceeding — do not delete/reset it to "
          "bypass this check.", file=sys.stderr)
    _audit("apply", "REJECTED", reason="audit_chain_tampered", errors=errors[:5])
    return True


def _reject_if_promoted_policy_violated(dir_, rego_result, destroy, registry_path=None):
    """Block the plan on a policy rule a HUMAN promoted to blocking.

    This is what makes agent-authored rules safe to accept. An agent can add a
    rule to the Rego and it changes nothing about what ships: every rule defaults to
    warn-only, and only an attributable human promotion in policy/rule_stages.json gives one
    teeth. Warn-stage findings are still printed and still counted in verification coverage;
    only their ability to stop an apply is withheld.

    A failed EVALUATION is not a violation. OPA is optional, and treating "opa not
    installed" as a policy breach would make an optional tool a hard dependency of every
    plan. plan_gate logs that case separately via the shadow reporting.
    """
    if rego_result.get("evaluation_failed"):
        return False
    split = rule_stages.partition(rego_result.get("findings") or [], registry_path=registry_path)
    if not split["blocking"]:
        if split["warning"]:
            print(f"[gate] policy: {split['warning_count']} warn-stage finding(s) "
                  f"(not promoted to blocking -- reported, not enforced)")
        return False
    print("[gate] REFUSING plan -- promoted policy rules are violated:", file=sys.stderr)
    for f in split["blocking"]:
        print(f"  - {f.get('id')}: {f.get('title') or ''} [{f.get('resource') or '-'}]",
              file=sys.stderr)
    print("[gate] These rules were explicitly promoted to blocking in policy/rule_stages.json. "
          "Fix the plan, or demote the rule with a recorded reason if it is wrong.",
          file=sys.stderr)
    _audit("plan", "REJECTED", reason="promoted_policy_violation", dir=dir_, destroy=destroy,
           blocking_findings=split["blocking"])
    return True


def _reject_if_reverts_out_of_band_and_auto_approve(dir_, mode, drift_result, destroy):
    """Same hard, non-overridable shape as the destructive check below, and for the same
    reason: auto-approve means nobody looks. A plan that silently undoes a change someone
    made directly in the account -- an emergency permission fix, a hand-widened security
    group -- is exactly the case a human must see. Terraform renders it as an ordinary
    `update`, so without this it sails through unreviewed.

    Drift the plan does NOT revert is advisory only; nothing is being undone, so it prints
    but never blocks.
    """
    if mode != "auto-approve" or not drift_result.get("reverts_out_of_band_changes"):
        return False
    print("[gate] REFUSING auto-approve apply — this plan reverts changes made outside "
          "Terraform:", file=sys.stderr)
    for row in drift_result["reverted"]:
        print(f"  - {row['address']}: {', '.join(row['attributes'])}", file=sys.stderr)
    print("[gate] Someone changed these directly in the account. Re-run with --mode gatekeeper "
          "so a human can confirm the revert is intended. There is no bypass flag.",
          file=sys.stderr)
    _audit("apply", "REJECTED", reason="plan_reverts_out_of_band_changes", dir=dir_,
           destroy=destroy, cloud_drift=drift_result)
    return True


def _reject_if_destructive_and_auto_approve(dir_, mode, classification, destroy):
    """Hard, non-overridable gate -- unlike every other _reject_if_* in this file, there is
    deliberately no MINUS_ALLOW_* env var or policy-mode carve-out for this one, in dev or
    production alike. mode="auto-approve" means no human ever reviews this plan before it
    applies; a plan that isn't create-only, non-stateful, non-IAM, and non-Databricks (see
    destructive_change_gate.py) must not be allowed to slip through unreviewed. mode=
    "gatekeeper" already puts a human in the loop (the y/N prompt at approve time) -- that IS
    the staged/guarded path this routes to, so a gatekeeper-mode apply is never blocked here
    regardless of what the plan contains; only the credential-free autonomous path is."""
    if mode != "auto-approve" or (classification.get("autonomous_eligible", False) and not destroy):
        return False
    print("[gate] REFUSING auto-approve apply — this plan is not autonomous-eligible or is a destroy plan:", file=sys.stderr)
    _print_classification(classification)
    print("[gate] Re-run with --mode gatekeeper for human review. There is no bypass flag "
          "for this check.", file=sys.stderr)
    _audit("apply", "REJECTED", reason="destructive_change_not_autonomous_eligible", dir=dir_,
           destroy=destroy, destructive_classification=classification)
    return True


def _reject_if_g9_not_clean_and_auto_approve(dir_, mode, g9_result, destroy):
    """Same hard, non-overridable shape as _reject_if_destructive_and_auto_approve above, for
    the same reason: mode="auto-approve" means no human reviews this plan before it applies, so
    an unproven-at-apply-time plan must not slip through. `g9_result` is the verdict recorded at
    plan time (see _g9_eval), carried through the approval record -- never recomputed here.

    Covers every non-clean shape the same way, no special case for "not configured": coverage
    "none"/destroy-skip (evaluation_failed=False) always passes; anything with
    evaluation_failed=True blocks, whether the reason is a real apply-time failure
    (resource_type_unverified, negative_fidelity_unverified, a genuine apply error) or the
    disclosed environment gap (g9_not_configured -- no LocalStack token, and both free
    emulators fail IAM/KMS/S3 negative fidelity). The present consequence is deliberate: an
    AWS-touching auto-approve plan stages rather than auto-ships until a fidelity-proven
    emulator is actually configured."""
    if mode != "auto-approve" or destroy:
        return False
    if g9_result is None or g9_result.get("evaluation_failed"):
        print("[gate] REFUSING auto-approve apply — G9 (ephemeral apply) did not return a clean "
              "verdict for this plan:", file=sys.stderr)
        _print_g9_result(g9_result or {"evaluation_failed": True, "reason": "g9_result_missing"})
        print("[gate] Re-run with --mode gatekeeper for human review. There is no bypass flag "
              "for this check.", file=sys.stderr)
        _audit("apply", "REJECTED", reason="g9_not_clean", dir=dir_, destroy=destroy, g9_result=g9_result)
        return True
    return False


def stage_apply(dir_, mode="gatekeeper", policy_mode=None):
    policy_mode = _policy_mode(policy_mode)
    print("== apply ==")
    if _reject_if_audit_chain_tampered():
        return False
    current, herr = _plan_hash(dir_)
    if not current:
        print(f"[gate] cannot read current plan ({herr}).", file=sys.stderr)
        return False
    if _reject_if_source_stale("apply", dir_, current):
        return False

    # SHADOW visibility on every apply, same as stage_plan -- printed + audited regardless of
    # mode, so a gatekeeper-mode operator sees it too even though it can't block their path.
    classification = _classify_plan(dir_)
    _print_classification(classification)
    approval_path = _approved_path(dir_, current)
    if not os.path.exists(approval_path):
        # Name the exact command, not just the stage: an agent reading "run approve first"
        # has to reconstruct the --dir argument it already had.
        print(cli_diagnostics.format_agent_error(
            "`apply` needs step 5 (Approval), which has not run for this plan.",
            f"no approval record for plan {current[:12]}... in {dir_}",
            f"minusctl gate approve --dir {dir_}"),
            file=sys.stderr)
        print("[gate] no approval on record for this directory and plan hash. Run `approve` first.",
              file=sys.stderr)
        _audit("apply", "REJECTED", reason="no_matching_approval", dir=dir_)
        _clear_approvals(dir_)
        return False
    try:
        approval = json.load(open(approval_path, encoding="utf-8"))
    except Exception:
        print("[gate] approval record unreadable. Re-run `approve`.", file=sys.stderr)
        _clear_approvals(dir_, current)
        return False
    # Same shadow-visibility principle as classification above: a gatekeeper-mode operator sees
    # the recorded G9 verdict too, even though only auto-approve mode can be blocked by it.
    _print_g9_result(approval.get("g9_result") or {"evaluation_failed": True, "reason": "g9_result_missing"})
    # destroy rides along on the approval record (see stage_approve) so every apply-stage audit
    # entry self-describes -- otherwise a reviewer reading only this record cannot tell
    # create/modify from teardown without cross-referencing the plan-stage record.
    destroy = approval.get("destroy", False)
    if approval.get("canonical_dir") != _canonical_dir(dir_):
        print("[gate] approval was recorded for a different Terraform directory.", file=sys.stderr)
        _audit("apply", "REJECTED", reason="dir_mismatch", dir=dir_, destroy=destroy)
        _clear_approvals(dir_, current)
        return False
    if current != approval.get("plan_hash"):
        print("[gate] PLAN CHANGED since approval — refusing to apply. Re-run plan + approve.",
              file=sys.stderr)
        _audit("apply", "REJECTED", reason="hash_mismatch", dir=dir_, destroy=destroy)
        _clear_approvals(dir_, current)
        return False

    account, connected = _identity()
    if not connected:
        print("[gate] no active cloud session — cannot apply. Authenticate "
              "(`aws sso login` / assume the MFA-gated deploy role), then re-run apply.",
              file=sys.stderr)
        _audit("apply", "BLOCKED", reason="no_session", dir=dir_, destroy=destroy)
        return False  # approval kept so you can authenticate and retry

    if _reject_if_weak_credentials(dir_, _credential_posture(), policy_mode, destroy=destroy):
        return False  # approval kept; re-auth with a temporary session and retry

    if _reject_if_nonsandbox_dev(dir_, account, policy_mode, destroy=destroy):
        return False  # approval kept; re-run with --policy-mode production

    if _reject_if_apply_identity_mismatches_approver(dir_, approval, policy_mode, destroy=destroy):
        return False  # approval kept; apply as the identity that actually approved this

    effective_mode = "auto-approve" if (mode == "auto-approve" or approval.get("approval_mode") == "auto-approve") else "gatekeeper"
    if _reject_if_destructive_and_auto_approve(dir_, effective_mode, classification, destroy):
        return False  # approval kept; re-run apply with --mode gatekeeper for human review

    # Computed from the approved plan JSON, not re-read from the cloud -- same "decided once
    # at plan, enforced at apply" shape the other checks use.
    if _reject_if_reverts_out_of_band_and_auto_approve(
            dir_, effective_mode, approval.get("cloud_drift") or {}, destroy):
        return False  # approval kept; re-run apply with --mode gatekeeper for human review

    if _reject_if_g9_not_clean_and_auto_approve(dir_, effective_mode, approval.get("g9_result"), destroy):
        return False  # approval kept; re-run apply with --mode gatekeeper for human review

    print(f"[gate] applying approved plan (hash {current[:16]}...) as {account} ...")
    # applied/failed/errors are created HERE, not inside _apply_with_json_capture, and passed
    # in to be mutated in place; status starts pessimistic. Ctrl+C during the apply raises
    # KeyboardInterrupt out of _apply_with_json_capture before it can return, which would skip
    # the _audit() call entirely and leave NO record of a real, possibly-partial apply. The
    # `finally` guarantees an audit entry with whatever partial data was gathered, and status
    # stays "INTERRUPTED" unless a clean return upgrades it. The interrupt is not swallowed --
    # it keeps propagating after `finally`.
    # Accepted gap: this covers Ctrl+C and any other exception, not a bare `kill` (no SIGTERM
    # handler is installed) or `kill -9` (SIGKILL is uncatchable on any platform).
    applied, failed, errors = [], [], {}
    status = "INTERRUPTED"
    try:
        rc = _apply_with_json_capture(dir_, applied, failed, errors)
        status = "OK" if rc == 0 else "FAILED"
    finally:
        _audit("apply", status, plan_hash=current, dir=dir_, identity=account,
               resources_applied=applied, resources_failed=failed, resource_errors=errors,
               destroy=destroy)
    print("[gate] apply complete." if status == "OK" else f"[gate] apply {status}.")
    if status != "INTERRUPTED":
        _clear_approvals(dir_, current)  # one-shot: the approval is consumed
    return status == "OK"


def stage_run(dir_, mode, policy_mode=None, destroy=False, with_telemetry=False,
              role_arn=None):
    return (stage_verify(dir_, policy_mode)
            and stage_plan(dir_, policy_mode, destroy=destroy, with_telemetry=with_telemetry,
                           impact=impact)
            and stage_approve(dir_, mode, policy_mode, role_arn=role_arn)
            and stage_apply(dir_, mode, policy_mode))


# ---------------------------------------------------------------------------
def _build_parser():
    p = argparse.ArgumentParser(description="Plan-bound Terraform deploy gate (uses the CLI credential chain)")
    p.add_argument("stage", choices=["verify", "plan", "approve", "apply", "run"])
    p.add_argument("--dir", required=True, help="Terraform directory to deploy (no default — this is a generic engine)")
    p.add_argument("--mode", default="gatekeeper", choices=["gatekeeper", "auto-approve"])
    p.add_argument("--policy-mode", choices=["dev", "production"],
                   default=os.environ.get("MINUS_POLICY_MODE", "dev"),
                   help="dev blocks native SEC-* only; production also requires external policy scanner evidence")
    p.add_argument("--destroy", action="store_true",
                   help="plan a teardown (terraform plan -destroy) instead of a create/modify plan; "
                        "approve/apply are unchanged -- same hash-bind, RBAC, and audit chain as any plan")
    p.add_argument("--role-arn", default=None,
                   help="assert the active session is this deploy role; approve refuses if "
                        "it is not. An assertion the gate VERIFIES -- it never assumes a "
                        "role and never handles credentials. (No --mfa-arn: an MFA claim is "
                        "not visible to sts get-caller-identity, so the gate cannot check "
                        "one; MFA is enforced by the role's trust policy at AssumeRole.)")
    p.add_argument("--impact", default=None,
                   help="What this change breaks, and for whom. REQUIRED when the plan is "
                        "not autonomous-eligible (stateful, IAM or unreviewed resource "
                        "types). The approver reads this sentence rather than the raw "
                        "finding list, which is what makes the y/N a review instead of a "
                        "rubber stamp.")
    p.add_argument("--with-telemetry", action="store_true",
                   help="on detected cloud drift, ask CloudTrail who changed the resource and "
                        "Glue what failed first (read-only, advisory, fail-open). Off by "
                        f"default; ${TELEMETRY_ENV}=1 also enables it")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)

    if args.stage == "verify":
        ok = stage_verify(args.dir, args.policy_mode)
    elif args.stage == "plan":
        ok = stage_plan(args.dir, args.policy_mode, destroy=args.destroy,
                        with_telemetry=args.with_telemetry, impact=args.impact)
    elif args.stage == "approve":
        ok = stage_approve(args.dir, args.mode, args.policy_mode, role_arn=args.role_arn)
    elif args.stage == "apply":
        ok = stage_apply(args.dir, args.mode, args.policy_mode)
    else:
        ok = stage_run(args.dir, args.mode, args.policy_mode, destroy=args.destroy,
                       impact=args.impact, with_telemetry=args.with_telemetry,
                       role_arn=args.role_arn)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
