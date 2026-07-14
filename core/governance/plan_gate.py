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

Destroy (2026-07-05): teardown goes through the exact same loop, not a raw `terraform
destroy` — pass --destroy to `plan`. approve/apply are unchanged: a destroy plan's
resource_changes are just actions=["delete"], which hash-binding, RBAC, and the audit
chain already handle like any other plan.

Examples (point --dir at any Terraform directory — the engine is workload-agnostic):
    python core/governance/plan_gate.py verify  --dir path/to/terraform
    python core/governance/plan_gate.py plan    --dir path/to/terraform
    python core/governance/plan_gate.py approve --dir path/to/terraform
    python core/governance/plan_gate.py apply   --dir path/to/terraform
    python core/governance/plan_gate.py run     --dir path/to/terraform [--mode auto-approve]

    python core/governance/plan_gate.py plan    --dir path/to/terraform --destroy   # governed teardown
    python core/governance/plan_gate.py approve --dir path/to/terraform
    python core/governance/plan_gate.py apply   --dir path/to/terraform
"""
import os
import sys
import json
import hashlib
import getpass
import argparse
import datetime
import threading
import subprocess

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
from providers.base import get_provider  # noqa: E402
import plan_inspector  # noqa: E402
import toolpath  # noqa: E402
import audit_chain  # noqa: E402
import authz  # noqa: E402
import destructive_change_gate  # noqa: E402
import optimize_analyzer  # noqa: E402
import rego_gate  # noqa: E402
import intent_assertions  # noqa: E402
import requirements as reqgate  # noqa: E402
import architecture_decision as adecision  # noqa: E402
import ephemeral_apply  # noqa: E402

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

    Audit finding 2026-07-04: the audit chain recorded FAILED/OK for an apply but nothing
    about WHICH resources succeeded before a failure -- a real partial-apply during a sandbox
    test had to be reconstructed by hand via `terraform state list` + AWS CLI. This is what
    actually closes that gap.

    audit finding 2026-07-05: applied/failed/errors used to be built as local variables here
    and only returned at the very end -- an interrupt (Ctrl+C) partway through this function
    unwound the stack before that return ever ran, losing the partial data and skipping the
    caller's audit write entirely. The caller now owns these mutable containers and passes
    them in; appending to them here mutates the SAME objects the caller already holds, so
    whatever happened before an interrupt survives regardless of how this function exits.

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
# SEC-06/SEC-07 (docs/g6_iam_extension_scope.md) have NO regex counterpart at all -- a real bug
# found running the extension's own parity pass, not anticipated in the scope doc: leaving a new
# rule ID out of this tuple doesn't just skip a comparison, it silently drops that rule's real
# (non-unresolved) findings from BOTH the divergence report AND the audit chain entirely, since
# _g6_shadow_eval's divergence loop only ever iterates this tuple. The one thing that WOULD still
# surface for an unlisted rule is its field_unresolved findings (a separate, unfiltered list) --
# meaning the uncertain case would have been visible while the confirmed-violation case silently
# wasn't, backwards from what the whole shadow mechanism exists to guarantee. Every new rule ID
# must be added here the moment it's added to rules.rego, not deferred.


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
# Explicit opt-in, not a default-on assumption: G9 (docs/phase6_step1_authoring_scope.md
# section 3) runs a real terraform init/plan/apply/destroy cycle against a real emulator
# container -- calling that unconditionally on every stage_plan() invocation would make the
# entire existing test suite (hundreds of calls, none of which run Docker) depend on emulator
# infrastructure that doesn't exist in most environments this gate runs in, including this
# session's own. Setting MINUS_G9_EMULATOR to a supported emulator name is what actually turns
# real ephemeral-apply invocation on; unset means "not configured" -- SYNCHRONOUS (docs/
# phase6_step1_authoring_scope.md section 3's own recorded decision), fail-CLOSED at the
# auto-approve enforcement boundary (see _reject_if_g9_not_clean_and_auto_approve below), never
# silently treated as "nothing to check, must be safe."


def _g9_eval(dir_, plan_json):
    """G9 (docs/phase5_scope.md Phase 5, wired into the real flow per docs/
    phase6_step1_authoring_scope.md section 3): real ephemeral-apply verdict for the current
    plan, computed once here at plan time and carried through the approval record to apply time
    (the same "computed once at plan, reused at apply" shape destroy already uses) rather than
    re-run at apply time, since a real init/apply/destroy cycle is genuinely expensive -- running
    it twice per deploy for no new information would be pure waste, not extra safety.

    Coverage "none" (no AWS content in the plan at all -- a Databricks-only or
    zero-cloud-footprint plan like the terraform_data-based e2e fixtures) means G9 has nothing
    to prove here and is skipped cleanly -- matches ephemeral_apply.py's own honest "none"
    verdict, never silently reported as if G9 ran and passed.

    No emulator configured (MINUS_G9_EMULATOR unset -- the disclosed, real, current-environment
    state: no LocalStack token is provisioned this session, and both evaluated free emulators
    (MiniStack, Floci) already failed IAM/KMS/S3 negative-fidelity -- docs/phase5_scope.md
    section 7.5/8.6) returns a synthetic, always-non-clean verdict in the same {evaluation_failed,
    reason, ...} shape ephemeral_apply.py's own _fail() produces for `terraform_not_found`, so
    downstream code (the enforcement check, the audit record) treats it identically to any other
    G9 failure -- never a special-cased "skip because it's not set up" path.
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
    """2026-07-07, Phase 1 item 2: the approval record proves WHO approved a plan hash, but
    until now nothing checked that the credentials actually running `apply` belong to that
    same approver -- two different people could satisfy "someone approved" and "someone
    applied" without ever being the same person. Compares the CURRENT (apply-time) verified
    AWS identity against the approver recorded at approval time.

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
    print("[gate] verify OK")
    _audit("verify", "OK", dir=dir_, policy_mode=policy_mode)
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


def stage_plan(dir_, policy_mode=None, destroy=False):
    """destroy=True governs teardown through the exact same hash-bind -> approve -> apply loop
    as create/modify (2026-07-05 audit finding: destroy was the one ungated path -- raw
    `terraform destroy`, no plan-hash binding, no RBAC, no audit chain). A destroy plan's
    resource_changes carry actions=["delete"]; _plan_hash/_apply_with_json_capture/stage_apply
    already handle that shape correctly (verified against real terraform: apply -json on a
    saved destroy-plan file emits the same apply_start/apply_complete stream, and _ACTION_VERB/
    _ACTION_DONE already render "delete" as Destroying/destruction) -- nothing downstream of
    this function needed to change."""
    print("== plan (destroy) ==" if destroy else "== plan ==")
    policy_mode = _policy_mode(policy_mode)
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
    with open(_pending_path(dir_), "w", encoding="utf-8") as f:
        json.dump({
            "plan_hash": h,
            "dir": dir_,
            "canonical_dir": _canonical_dir(dir_),
            # 2026-07-07: prefer the AWS-STS-verified identity (cannot be spoofed by
            # MINUS_OPERATOR) so the two-person production rule below compares real
            # authenticated principals, not two self-reported strings. Falls back to
            # operator() when no cloud session is active yet (e.g. dev mode planning
            # before credentials are configured).
            "planner": authz.verified_operator() or authz.operator(),
            "created": _now(),
            "destroy": destroy,
        }, f, indent=2)
    _clear_approvals(dir_)  # a new plan for this dir invalidates prior approvals
    print(f"[gate] plan saved. plan_hash = {h[:16]}...")

    # Destructive-change classification (core/governance/destructive_change_gate.py, Phase 1 of
    # the generation-time-authoring gate stack). SHADOW ONLY here: logged and printed, never
    # blocks stage_plan -- enforcement (refusing an auto-approve apply that isn't
    # autonomous-eligible) lives in stage_apply below, the actual mutation point.
    classification = _classify_plan(dir_)
    _print_classification(classification)

    # G6 (docs/g6_scope.md, Phase 3): SEC-*/COST-* rules over real plan JSON via OPA/Rego,
    # run alongside the existing regex-over-HCL scan (core/reporting/optimize_analyzer.py,
    # still invoked separately in stage_verify above -- unchanged). SHADOW ONLY: this never
    # blocks stage_plan and never enforces anything -- BLOCKING_PREFIXES / real enforcement
    # stays exactly where it already is (optimize_analyzer.py's own SEC- prefix check,
    # unchanged) until 16-module parity is proven and Phase 3 is explicitly closed for real,
    # same discipline as G2/G5.
    plan_json_for_g6, plan_json_err = _plan_json(dir_)
    g6_result = _g6_shadow_eval(dir_, plan_json_for_g6) if plan_json_for_g6 is not None else {
        "comparable": False, "regex_error": None,
        "rego_evaluation_failed": True, "rego_reason": "plan_unreadable", "rego_detail": plan_json_err,
    }
    _print_g6_shadow(g6_result)

    # G9 (docs/phase5_scope.md Phase 5, wired into the real flow per docs/
    # phase6_step1_authoring_scope.md section 3): unlike G6, this is NOT shadow-only -- a
    # not-clean verdict blocks the auto-approve enforcement path below (see
    # _reject_if_g9_not_clean_and_auto_approve in stage_apply). Skipped for destroy plans (same
    # reasoning as the cost-coverage skip below: G9 exists to catch CREATE-order apply-time
    # failures; a teardown has nothing new for it to check). Computed once here, carried through
    # the pending record -> approval record -> stage_apply, never re-run at apply time (a real
    # init/apply/destroy cycle is genuinely expensive; re-running it a second time per deploy for
    # no new information would be pure waste, not extra safety -- docs/
    # phase6_step1_authoring_scope.md section 3's own recorded sync/async decision).
    g9_result = _g9_eval(dir_, plan_json_for_g6) if not destroy else {
        "evaluation_failed": False, "reason": None, "detail": "destroy plan -- G9 not applicable",
        "coverage": None, "databricks_resources": [], "findings": [], "emulator": None,
    }
    _print_g9_result(g9_result)
    # Merge into the pending record already written above rather than recomputing it (planner
    # identity can be a real AWS STS call -- doing that twice per plan would be pure waste).
    try:
        pending_for_update = json.load(open(_pending_path(dir_), encoding="utf-8"))
    except Exception:
        pending_for_update = {}
    pending_for_update["g9_result"] = g9_result
    with open(_pending_path(dir_), "w", encoding="utf-8") as f:
        json.dump(pending_for_update, f, indent=2)

    # Phase 4 (docs/phase4_scope.md, G3/G4): intent-vs-reality advisory checks. ADVISORY ONLY --
    # never blocks stage_plan, same shadow discipline as G6. requirements.json/architecture_
    # decision.json are looked up in dir_'s parent (the run root, matching runs.new_run()'s own
    # terraform_dir = root/"terraform" convention) -- their absence just means this run isn't
    # part of the requirements-first workflow, not an error. check_controls (blueprint-specific)
    # is deliberately NOT wired here: the demo blueprint's own synthetic plan (demo.py's
    # synthetic_plan(), not a real terraform plan) has no `configuration` key at all, so the two
    # checks needing sibling-reference tracing (public access blocks, versioning/lifecycle)
    # would false-positive on every demo run regardless of real correctness -- a real limitation
    # discovered while wiring this, not silently papered over. check_module_presence and
    # check_numerics both work correctly here since they need only resource_changes.
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


def stage_approve(dir_, mode="gatekeeper", policy_mode=None):
    policy_mode = _policy_mode(policy_mode)
    print("== approve ==")
    h, herr = _plan_hash(dir_)
    if not h:
        print(f"[gate] no valid plan to approve ({herr}). Run `plan` first.", file=sys.stderr)
        return False
    _warn_if_over_budget(dir_, h)

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
    if not connected:
        print("[gate] WARNING: no active cloud session. Authenticate before apply "
              "(`aws sso login`, or assume the MFA-gated deploy role).")

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
        # 2026-07-07, Phase 1 item 2: set ONLY when approver came from a real AWS-STS
        # identity, not the env-var fallback -- lets apply-time distinguish "verify this
        # matches" from "nothing to verify," instead of comparing two fallback strings
        # and calling that a security check.
        "approver_verified_identity": approver_verified_identity,
        "authz_mode": authz_mode,
        "approved_at": _now(),
        # Carried forward from the plan-stage pending record (2026-07-06, Item 6 finding 1):
        # the plan-stage audit record already notes destroy=True/False, but the apply-stage
        # record didn't -- a reviewer reading only the apply audit trail couldn't tell create
        # from teardown without cross-referencing the earlier plan record. Threading it through
        # the approval record (the thing stage_apply actually reads) closes that gap.
        "destroy": pending.get("destroy", False),
        # Carried forward the same way, for the same reason (docs/
        # phase6_step1_authoring_scope.md section 3): stage_apply's auto-approve enforcement
        # reads this rather than re-running a real, expensive ephemeral-apply cycle.
        "g9_result": pending.get("g9_result"),
    }
    os.makedirs(_approval_dir(dir_), exist_ok=True)
    with open(_approved_path(dir_, h), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    print("[gate] approved — bound to this plan hash. (No credentials stored.)")
    _audit("approve", "APPROVED", plan_hash=h, dir=dir_, identity=account, approver=approver, authz_mode=authz_mode)
    return True


def _reject_if_audit_chain_tampered():
    """Audit finding 2026-07-03: audit_chain.verify() existed and worked, but nothing in the
    deploy path ever called it — tamper-evidence was real but opt-in, not load-bearing. This
    makes it load-bearing: apply refuses to proceed if the trail that's supposed to record every
    prior gate decision has itself been edited, reordered, or truncated out-of-band."""
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


def _reject_if_destructive_and_auto_approve(dir_, mode, classification, destroy):
    """Hard, non-overridable gate -- unlike every other _reject_if_* in this file, there is
    deliberately no MINUS_ALLOW_* env var or policy-mode carve-out for this one, in dev or
    production alike. mode="auto-approve" means no human ever reviews this plan before it
    applies; a plan that isn't create-only, non-stateful, non-IAM, and non-Databricks (see
    destructive_change_gate.py) must not be allowed to slip through unreviewed. mode=
    "gatekeeper" already puts a human in the loop (the y/N prompt at approve time) -- that IS
    the staged/guarded path this routes to, so a gatekeeper-mode apply is never blocked here
    regardless of what the plan contains; only the credential-free autonomous path is."""
    if mode != "auto-approve" or classification["autonomous_eligible"]:
        return False
    print("[gate] REFUSING auto-approve apply — this plan is not autonomous-eligible:", file=sys.stderr)
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
    disclosed current-environment gap (g9_not_configured -- no LocalStack token provisioned,
    both free emulators already failed IAM/KMS/S3 negative-fidelity this session). This is the
    real, present-tense consequence of wiring G9 in today: an AWS-touching auto-approve plan
    stages rather than auto-ships until a fidelity-proven emulator is actually configured."""
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
    # 2026-07-06, Item 6 finding 1: the apply-stage audit record used to say nothing about
    # direction -- a reviewer reading only this record couldn't tell create/modify from
    # teardown without cross-referencing the plan-stage record. destroy now rides along on
    # the approval record (see stage_approve) so every apply-stage entry self-describes.
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

    if _reject_if_destructive_and_auto_approve(dir_, mode, classification, destroy):
        return False  # approval kept; re-run apply with --mode gatekeeper for human review

    if _reject_if_g9_not_clean_and_auto_approve(dir_, mode, approval.get("g9_result"), destroy):
        return False  # approval kept; re-run apply with --mode gatekeeper for human review

    print(f"[gate] applying approved plan (hash {current[:16]}...) as {account} ...")
    # audit finding 2026-07-05: applied/failed/errors are created HERE (not inside
    # _apply_with_json_capture) and passed in to be mutated in place, and status starts
    # pessimistic. A hard interrupt (Ctrl+C) during the apply raises KeyboardInterrupt out of
    # _apply_with_json_capture before it would otherwise return -- previously that skipped the
    # _audit() call entirely, leaving NO record of a real, possibly-partial apply. The `finally`
    # now guarantees an audit entry always gets written, with whatever partial data was
    # gathered before the interrupt, and status stays "INTERRUPTED" unless a clean return
    # upgrades it to OK/FAILED. The interrupt itself is not swallowed: it keeps propagating
    # after `finally` runs, same as if this block weren't here.
    # Known, accepted gap (like authz.py's operator-spoofing gap): this covers Ctrl+C
    # (SIGINT/KeyboardInterrupt) and any other exception, not a bare `kill` (SIGTERM has no
    # handler installed) or `kill -9` (SIGKILL is uncatchable by any process, on any platform).
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


def stage_run(dir_, mode, policy_mode=None, destroy=False):
    return (stage_verify(dir_, policy_mode) and stage_plan(dir_, policy_mode, destroy=destroy)
            and stage_approve(dir_, mode, policy_mode) and stage_apply(dir_, mode, policy_mode))


# ---------------------------------------------------------------------------
def main(argv=None):
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
    args = p.parse_args(argv)

    if args.stage == "verify":
        ok = stage_verify(args.dir, args.policy_mode)
    elif args.stage == "plan":
        ok = stage_plan(args.dir, args.policy_mode, destroy=args.destroy)
    elif args.stage == "approve":
        ok = stage_approve(args.dir, args.mode, args.policy_mode)
    elif args.stage == "apply":
        ok = stage_apply(args.dir, args.mode, args.policy_mode)
    else:
        ok = stage_run(args.dir, args.mode, args.policy_mode, destroy=args.destroy)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
