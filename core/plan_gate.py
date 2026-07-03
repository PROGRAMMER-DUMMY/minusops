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

Examples (point --dir at any Terraform directory — the engine is workload-agnostic):
    python core/plan_gate.py verify  --dir path/to/terraform
    python core/plan_gate.py plan    --dir path/to/terraform
    python core/plan_gate.py approve --dir path/to/terraform
    python core/plan_gate.py apply   --dir path/to/terraform
    python core/plan_gate.py run     --dir path/to/terraform [--mode auto-approve]
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from providers.base import get_provider  # noqa: E402
import plan_inspector  # noqa: E402
import toolpath  # noqa: E402
import audit_chain  # noqa: E402
import authz  # noqa: E402

WORKSPACE = os.getcwd()
LOG_DIR = os.path.join(WORKSPACE, ".agents", "logs")
SCAN = os.path.join(WORKSPACE, "core", "optimize_analyzer.py")

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


def _reject_if_weak_credentials(dir_, posture, policy_mode="dev"):
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
        _audit("apply", "WARN", reason="weak_credentials_override", dir=dir_, cred_type=ctype)
        return False
    # Production: the override is not honored — a temporary MFA-gated session is required.
    if allow and policy_mode == "production":
        print("[gate] refusing apply (production): MINUS_ALLOW_STATIC_CREDS is not honored in "
              "production. Use a temporary MFA-gated session (`aws sso login` or assume your "
              "deploy role).", file=sys.stderr)
        _audit("apply", "REJECTED", reason="static_creds_override_denied_in_production",
               dir=dir_, cred_type=ctype)
        return True
    print(f"[gate] refusing apply: this session uses {ctype} credentials. The MFA-gated "
          "deploy guarantee requires a temporary session — authenticate with `aws sso login` "
          "or assume your MFA-gated deploy role. (Override: MINUS_ALLOW_STATIC_CREDS=1, audited "
          "and honored in dev only.)", file=sys.stderr)
    _audit("apply", "REJECTED", reason="weak_credentials", dir=dir_, cred_type=ctype)
    return True


def _reject_if_nonsandbox_dev(dir_, account, policy_mode):
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
        _audit("apply", "WARN", reason="dev_mode_sandbox_unverified", dir=dir_, account=str(account))
        return False
    sandboxes = {a.strip() for a in raw.split(",") if a.strip()}
    if str(account) in sandboxes:
        return False
    print(f"[gate] refusing apply: dev policy mode targets account {account}, which is not in "
          "MINUS_SANDBOX_ACCOUNTS. Governed accounts require --policy-mode production.",
          file=sys.stderr)
    _audit("apply", "REJECTED", reason="dev_mode_nonsandbox_account", dir=dir_, account=str(account))
    return True


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
              "Add these to core/pricing_data/aws_resource_map.json (priced) or "
              "core/pricing_data/free_resources.json (confirmed free) after checking the AWS "
              "Price List catalog — never guess.", file=sys.stderr)
        _audit("plan", "REJECTED", reason="unresolved_cost_coverage", dir=dir_,
               plan_hash=plan_hash_, unresolved=names)
        return False
    print(f"[gate] WARNING: unresolved cost coverage for: {names} — these resource types have "
          "no known AWS service mapping, so they are silently excluded from the cost report. "
          "Run `python core/coverage_audit.py audit --report-dir {}`.".format(report_dir),
          file=sys.stderr)
    _audit("plan", "WARN", reason="unresolved_cost_coverage", dir=dir_,
           plan_hash=plan_hash_, unresolved=names)
    return True


def stage_plan(dir_, policy_mode=None):
    print("== plan ==")
    policy_mode = _policy_mode(policy_mode)
    rc, _, err = _tf(dir_, "plan", f"-out={PLAN_FILE}")
    if rc != 0:
        print(f"[gate] terraform plan failed:\n{err}", file=sys.stderr)
        _audit("plan", "FAILED", dir=dir_)
        return False
    h, herr = _plan_hash(dir_)
    if not h:
        print(f"[gate] could not hash plan: {herr}", file=sys.stderr)
        _audit("plan", "FAILED", reason="hash", dir=dir_)
        return False
    os.makedirs(_state_dir(dir_), exist_ok=True)
    with open(_pending_path(dir_), "w", encoding="utf-8") as f:
        json.dump({
            "plan_hash": h,
            "dir": dir_,
            "canonical_dir": _canonical_dir(dir_),
            "planner": authz.operator(),
            "created": _now(),
        }, f, indent=2)
    _clear_approvals(dir_)  # a new plan for this dir invalidates prior approvals
    print(f"[gate] plan saved. plan_hash = {h[:16]}...")
    _audit("plan", "OK", plan_hash=h, dir=dir_)

    # Auto-generate the versioned deploy report (plan + cost + architecture).
    # Informational — a report failure must never fail the plan.
    try:
        import reporter
        reporter.generate(dir_)
    except Exception as e:
        print(f"[gate] (report skipped: {e})", file=sys.stderr)
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
    approver = authz.operator()
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
        "authz_mode": authz_mode,
        "approved_at": _now(),
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
    if approval.get("canonical_dir") != _canonical_dir(dir_):
        print("[gate] approval was recorded for a different Terraform directory.", file=sys.stderr)
        _audit("apply", "REJECTED", reason="dir_mismatch", dir=dir_)
        _clear_approvals(dir_, current)
        return False
    if current != approval.get("plan_hash"):
        print("[gate] PLAN CHANGED since approval — refusing to apply. Re-run plan + approve.",
              file=sys.stderr)
        _audit("apply", "REJECTED", reason="hash_mismatch", dir=dir_)
        _clear_approvals(dir_, current)
        return False

    account, connected = _identity()
    if not connected:
        print("[gate] no active cloud session — cannot apply. Authenticate "
              "(`aws sso login` / assume the MFA-gated deploy role), then re-run apply.",
              file=sys.stderr)
        _audit("apply", "BLOCKED", reason="no_session", dir=dir_)
        return False  # approval kept so you can authenticate and retry

    if _reject_if_weak_credentials(dir_, _credential_posture(), policy_mode):
        return False  # approval kept; re-auth with a temporary session and retry

    if _reject_if_nonsandbox_dev(dir_, account, policy_mode):
        return False  # approval kept; re-run with --policy-mode production

    print(f"[gate] applying approved plan (hash {current[:16]}...) as {account} ...")
    rc, _, _ = _tf(dir_, "apply", PLAN_FILE)   # ambient CLI credentials
    ok = rc == 0
    _audit("apply", "OK" if ok else "FAILED", plan_hash=current, dir=dir_, identity=account)
    print("[gate] apply complete." if ok else "[gate] apply FAILED.")
    _clear_approvals(dir_, current)  # one-shot: the approval is consumed
    return ok


def stage_run(dir_, mode, policy_mode=None):
    return (stage_verify(dir_, policy_mode) and stage_plan(dir_, policy_mode)
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
    args = p.parse_args(argv)

    if args.stage == "verify":
        ok = stage_verify(args.dir, args.policy_mode)
    elif args.stage == "plan":
        ok = stage_plan(args.dir, args.policy_mode)
    elif args.stage == "approve":
        ok = stage_approve(args.dir, args.mode, args.policy_mode)
    elif args.stage == "apply":
        ok = stage_apply(args.dir, args.mode, args.policy_mode)
    else:
        ok = stage_run(args.dir, args.mode, args.policy_mode)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
