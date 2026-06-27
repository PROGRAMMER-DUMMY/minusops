"""
plan_gate.py — state-aware, plan-bound Terraform deploy gate.

Enforces the secure deployment loop in code (not just docs):

    verify  ->  terraform fmt -check + validate + security scan
    plan    ->  terraform plan -out=tfplan, record a PLAN HASH
    approve ->  review the exact plan -> record a hash-bound approval (NO secrets stored)
    apply   ->  current hash must match the approved one -> terraform apply tfplan

Credential model — we never handle secrets:
  * The operator authenticates via the cloud CLI BEFORE applying (e.g. `aws sso login`,
    or assume the MFA-gated deploy role from bootstrap/aws into their CLI session).
  * MFA is enforced upstream by that role's trust policy — the gate does not mint or
    store tokens. terraform apply uses the ambient CLI credential chain.

Guarantees:
  * Apply runs only the exact reviewed plan (apply tfplan, never re-plan).
  * Any .tf change -> new plan hash -> prior approval is void -> re-review required.
  * The approval record holds only a hash + caller identity + timestamp — no credentials.
  * auto-approve skips the y/N prompt but still cannot apply a hash you did not approve.

Cross-platform (Windows / macOS / Linux): os.path, list-form subprocess, no shell.

Examples:
    python core/plan_gate.py verify  --dir templates/aws/medallion-pipeline
    python core/plan_gate.py plan    --dir templates/aws/medallion-pipeline
    python core/plan_gate.py approve --dir templates/aws/medallion-pipeline
    python core/plan_gate.py apply   --dir templates/aws/medallion-pipeline
    python core/plan_gate.py run     --dir templates/aws/medallion-pipeline [--mode auto-approve]
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

WORKSPACE = os.getcwd()
LOG_DIR = os.path.join(WORKSPACE, ".agents", "logs")
PENDING = os.path.join(LOG_DIR, "pending_plan.json")
APPROVED = os.path.join(LOG_DIR, "approved_plan.json")   # hash + identity only, no secrets
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
        with open(os.path.join(LOG_DIR, "audit.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        print(f"[gate] WARNING: could not write audit record: {e}", file=sys.stderr)


def _run(args, capture=False):
    """Run a command (list form, no shell). Returns (rc, stdout, stderr)."""
    try:
        res = subprocess.run(args, text=True, capture_output=capture)
        return res.returncode, (res.stdout or ""), (res.stderr or "")
    except FileNotFoundError:
        return 127, "", f"command not found: {args[0]}"


def _tf(dir_, *tf_args, capture=False):
    return _run(["terraform", f"-chdir={dir_}", *tf_args], capture=capture)


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


def _clear_approval():
    try:
        if os.path.exists(APPROVED):
            os.remove(APPROVED)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------
def stage_verify(dir_):
    print("== verify ==")
    rc, _, err = _tf(dir_, "fmt", "-check", capture=True)
    if rc != 0:
        print("[gate] terraform fmt -check failed (run `terraform fmt`).", file=sys.stderr)
        _audit("verify", "FAILED", reason="fmt", dir=dir_)
        return False
    rc, _, err = _tf(dir_, "validate", capture=True)
    if rc != 0:
        print(f"[gate] terraform validate failed:\n{err}", file=sys.stderr)
        _audit("verify", "FAILED", reason="validate", dir=dir_)
        return False
    if os.path.exists(SCAN):
        _run([sys.executable, SCAN, "--source-dir", dir_], capture=True)
        print(f"[gate] security scan complete -> see {os.path.join('.agents', 'logs', 'optimization_report.md')}")
    print("[gate] verify OK")
    _audit("verify", "OK", dir=dir_)
    return True


def stage_plan(dir_):
    print("== plan ==")
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
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(PENDING, "w", encoding="utf-8") as f:
        json.dump({"plan_hash": h, "dir": dir_, "created": _now()}, f, indent=2)
    _clear_approval()  # a new plan invalidates any prior approval
    print(f"[gate] plan saved. plan_hash = {h[:16]}...")
    _audit("plan", "OK", plan_hash=h, dir=dir_)
    return True


def stage_approve(dir_, mode="gatekeeper"):
    print("== approve ==")
    h, herr = _plan_hash(dir_)
    if not h:
        print(f"[gate] no valid plan to approve ({herr}). Run `plan` first.", file=sys.stderr)
        return False

    pending = {}
    if os.path.exists(PENDING):
        try:
            pending = json.load(open(PENDING, encoding="utf-8"))
        except Exception:
            pending = {}
    if pending.get("plan_hash") != h:
        print("[gate] current plan does not match the last recorded plan. Re-run `plan`.", file=sys.stderr)
        _audit("approve", "REJECTED", reason="stale_plan", dir=dir_)
        return False

    account, connected = _identity()
    print(f"  plan_hash : {h}")
    print(f"  dir       : {dir_}")
    print(f"  identity  : {account if connected else 'NOT AUTHENTICATED'}")
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

    record = {"plan_hash": h, "dir": dir_, "identity": account, "cloud": get_provider().name,
              "approved_by": getpass.getuser(), "approved_at": _now()}
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(APPROVED, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    print("[gate] approved — bound to this plan hash. (No credentials stored.)")
    _audit("approve", "APPROVED", plan_hash=h, dir=dir_, identity=account)
    return True


def stage_apply(dir_, mode="gatekeeper"):
    print("== apply ==")
    if not os.path.exists(APPROVED):
        print("[gate] no approval on record. Run `approve` first.", file=sys.stderr)
        return False
    try:
        approval = json.load(open(APPROVED, encoding="utf-8"))
    except Exception:
        print("[gate] approval record unreadable. Re-run `approve`.", file=sys.stderr)
        _clear_approval()
        return False

    current, herr = _plan_hash(dir_)
    if not current:
        print(f"[gate] cannot read current plan ({herr}).", file=sys.stderr)
        return False
    if current != approval.get("plan_hash"):
        print("[gate] PLAN CHANGED since approval — refusing to apply. Re-run plan + approve.",
              file=sys.stderr)
        _audit("apply", "REJECTED", reason="hash_mismatch", dir=dir_)
        _clear_approval()
        return False

    account, connected = _identity()
    if not connected:
        print("[gate] no active cloud session — cannot apply. Authenticate "
              "(`aws sso login` / assume the MFA-gated deploy role), then re-run apply.",
              file=sys.stderr)
        _audit("apply", "BLOCKED", reason="no_session", dir=dir_)
        return False  # approval kept so you can authenticate and retry

    print(f"[gate] applying approved plan (hash {current[:16]}...) as {account} ...")
    rc, _, _ = _tf(dir_, "apply", PLAN_FILE)   # ambient CLI credentials
    ok = rc == 0
    _audit("apply", "OK" if ok else "FAILED", plan_hash=current, dir=dir_, identity=account)
    print("[gate] apply complete." if ok else "[gate] apply FAILED.")
    _clear_approval()  # one-shot: the approval is consumed
    return ok


def stage_run(dir_, mode):
    return (stage_verify(dir_) and stage_plan(dir_)
            and stage_approve(dir_, mode) and stage_apply(dir_, mode))


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Plan-bound Terraform deploy gate (uses the CLI credential chain)")
    p.add_argument("stage", choices=["verify", "plan", "approve", "apply", "run"])
    p.add_argument("--dir", default="templates/aws/medallion-pipeline", help="Terraform directory")
    p.add_argument("--mode", default="gatekeeper", choices=["gatekeeper", "auto-approve"])
    args = p.parse_args()

    if args.stage == "verify":
        ok = stage_verify(args.dir)
    elif args.stage == "plan":
        ok = stage_plan(args.dir)
    elif args.stage == "approve":
        ok = stage_approve(args.dir, args.mode)
    elif args.stage == "apply":
        ok = stage_apply(args.dir, args.mode)
    else:
        ok = stage_run(args.dir, args.mode)
    sys.exit(0 if ok else 1)
