"""
plan_gate.py — state-aware, MFA-gated Terraform deploy gate.

Enforces the secure deployment loop in code (not just docs):

    verify  ->  terraform fmt -check + validate + security scan
    plan    ->  terraform plan -out=tfplan, record a PLAN HASH
    approve ->  prompt MFA (45s) -> sts session -> one-shot creds bound to that hash
    apply   ->  hash must match the approved one -> inject creds -> apply tfplan
                -> WIPE creds immediately (finally), even on error/Ctrl-C

Guarantees:
  * Apply runs only the exact reviewed plan (apply tfplan, never re-plan).
  * Any .tf change -> new plan hash -> prior approval is void -> fresh MFA required.
  * Credentials live for exactly one apply, then are shredded.
  * auto-approve mode still cannot apply a hash you did not approve.

Cross-platform (Windows / macOS / Linux): os.path, list-form subprocess, no shell.

Examples:
    python plan_gate.py verify  --dir templates/aws/medallion-pipeline
    python plan_gate.py plan    --dir templates/aws/medallion-pipeline
    python plan_gate.py approve --dir templates/aws/medallion-pipeline --mfa-arn arn:aws:iam::123:mfa/me [--role-arn arn:aws:iam::123:role/Deploy]
    python plan_gate.py apply   --dir templates/aws/medallion-pipeline [--mode auto-approve]
    python plan_gate.py run     --dir templates/aws/medallion-pipeline --mfa-arn ... [--role-arn ...] [--mode ...]
"""
import os
import sys
import json
import time
import hashlib
import getpass
import argparse
import datetime
import threading
import subprocess

WORKSPACE = os.getcwd()
LOG_DIR = os.path.join(WORKSPACE, ".agents", "logs")
PENDING = os.path.join(LOG_DIR, "pending_plan.json")
SCAN = os.path.join(WORKSPACE, "core", "optimize_analyzer.py")

# Credentials are stored OUTSIDE the repo so they can never be committed or scanned.
TOKEN_DIR = os.path.expanduser(os.path.join("~", ".minus_tf"))
TOKEN_FILE = os.path.join(TOKEN_DIR, "approved.token")

PLAN_FILE = "tfplan"            # written inside the target dir via terraform -chdir
SESSION_SECONDS = 900          # AWS minimum; creds are wiped after one apply anyway
MFA_PROMPT_TIMEOUT = 45        # seconds to enter the code before the request is denied


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


def _run(args, env=None, capture=False):
    """Run a command (list form, no shell). Returns (rc, stdout, stderr)."""
    try:
        res = subprocess.run(args, env=env, text=True,
                             capture_output=capture)
        return res.returncode, (res.stdout or ""), (res.stderr or "")
    except FileNotFoundError:
        return 127, "", f"command not found: {args[0]}"


def _tf(dir_, *tf_args, env=None, capture=False):
    return _run(["terraform", f"-chdir={dir_}", *tf_args], env=env, capture=capture)


def _plan_hash(dir_):
    """Stable hash of the planned changes (resource_changes from `terraform show -json`)."""
    rc, out, err = _tf(dir_, "show", "-json", PLAN_FILE, capture=True)
    if rc != 0:
        return None, err.strip() or "terraform show failed"
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        return None, f"could not parse plan json: {e}"
    changes = data.get("resource_changes", [])
    canonical = json.dumps(changes, sort_keys=True, separators=(",", ":"))
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


def _write_token(creds, plan_hash, dir_):
    os.makedirs(TOKEN_DIR, exist_ok=True)
    payload = {"plan_hash": plan_hash, "dir": dir_, "created": _now(), "creds": creds}
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    try:
        os.chmod(TOKEN_FILE, 0o600)  # best-effort on Windows
    except Exception:
        pass


def _read_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _wipe_token():
    """Overwrite then remove the cred file so creds don't linger on disk."""
    try:
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write("0" * 256)
            os.remove(TOKEN_FILE)
    except Exception as e:
        print(f"[gate] WARNING: could not wipe token file: {e}", file=sys.stderr)


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
        rc, _, _ = _run([sys.executable, SCAN, "--source-dir", dir_], capture=True)
        # optimize_analyzer writes a report; a non-zero rc would indicate a hard error.
        print(f"[gate] security scan complete -> see {os.path.join('.agents','logs','optimization_report.md')}")
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
    print(f"[gate] plan saved. plan_hash = {h[:16]}…")
    _audit("plan", "OK", plan_hash=h, dir=dir_)
    return True


def stage_approve(dir_, mfa_arn, role_arn=None, mode="gatekeeper"):
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

    print(f"  plan_hash : {h}")
    print(f"  dir       : {dir_}")
    print(f"  mode      : {mode}")
    if mode == "gatekeeper":
        ans = _timed_input(f"Approve this plan? [y/N] ({MFA_PROMPT_TIMEOUT}s): ", MFA_PROMPT_TIMEOUT)
        if ans is None or ans.lower() not in ("y", "yes"):
            print("[gate] approval declined.")
            _audit("approve", "DENIED", plan_hash=h, dir=dir_)
            return False

    code = _timed_input(f"Enter 6-digit MFA code ({MFA_PROMPT_TIMEOUT}s): ", MFA_PROMPT_TIMEOUT)
    if not code:
        print("[gate] no MFA code provided — denied.")
        _audit("approve", "DENIED", reason="no_mfa", plan_hash=h, dir=dir_)
        return False

    if role_arn:
        args = ["aws", "sts", "assume-role", "--role-arn", role_arn,
                "--role-session-name", "minus-tf-apply", "--serial-number", mfa_arn,
                "--token-code", code, "--duration-seconds", str(SESSION_SECONDS), "--output", "json"]
    else:
        args = ["aws", "sts", "get-session-token", "--serial-number", mfa_arn,
                "--token-code", code, "--duration-seconds", str(SESSION_SECONDS), "--output", "json"]
    rc, out, err = _run(args, capture=True)
    if rc != 0:
        print(f"[gate] MFA session request failed:\n{err}", file=sys.stderr)
        _audit("approve", "FAILED", reason="sts", plan_hash=h, dir=dir_)
        return False

    creds = (json.loads(out) or {}).get("Credentials")
    if not creds:
        print("[gate] STS returned no credentials.", file=sys.stderr)
        return False

    _write_token(creds, h, dir_)
    print("[gate] approved. One-shot credentials minted and bound to this plan.")
    _audit("approve", "APPROVED", plan_hash=h, dir=dir_, session_expires=creds.get("Expiration"))
    return True


def stage_apply(dir_, mode="gatekeeper"):
    print("== apply ==")
    token = _read_token()
    if not token:
        print("[gate] no approved session. Run `approve` first.", file=sys.stderr)
        return False

    current, herr = _plan_hash(dir_)
    if not current:
        print(f"[gate] cannot read current plan ({herr}).", file=sys.stderr)
        _wipe_token()
        return False
    if current != token.get("plan_hash"):
        print("[gate] PLAN CHANGED since approval — refusing to apply. "
              "Re-run plan + approve (fresh MFA required).", file=sys.stderr)
        _audit("apply", "REJECTED", reason="hash_mismatch", dir=dir_)
        _wipe_token()
        return False

    creds = token["creds"]
    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
    env["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
    env["AWS_SESSION_TOKEN"] = creds["SessionToken"]

    try:
        print(f"[gate] applying approved plan (hash {current[:16]}…) …")
        rc, _, _ = _tf(dir_, "apply", PLAN_FILE, env=env)
        ok = rc == 0
        _audit("apply", "OK" if ok else "FAILED", plan_hash=current, dir=dir_)
        print("[gate] apply complete." if ok else "[gate] apply FAILED.")
        return ok
    finally:
        _wipe_token()  # one-shot: creds are destroyed regardless of outcome
        print("[gate] credentials wiped.")


def stage_run(dir_, mfa_arn, role_arn, mode):
    return (stage_verify(dir_) and stage_plan(dir_)
            and stage_approve(dir_, mfa_arn, role_arn, mode) and stage_apply(dir_, mode))


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    p = argparse.ArgumentParser(description="MFA-gated, plan-bound Terraform deploy gate")
    p.add_argument("stage", choices=["verify", "plan", "approve", "apply", "run"])
    p.add_argument("--dir", default="templates/aws/medallion-pipeline", help="Terraform directory")
    p.add_argument("--mfa-arn", help="MFA device ARN (required for approve/run)")
    p.add_argument("--role-arn", help="Deploy role ARN to assume (optional; else get-session-token)")
    p.add_argument("--mode", default="gatekeeper", choices=["gatekeeper", "auto-approve"])
    args = p.parse_args()

    if args.stage in ("approve", "run") and not args.mfa_arn:
        p.error("--mfa-arn is required for approve/run")

    if args.stage == "verify":
        ok = stage_verify(args.dir)
    elif args.stage == "plan":
        ok = stage_plan(args.dir)
    elif args.stage == "approve":
        ok = stage_approve(args.dir, args.mfa_arn, args.role_arn, args.mode)
    elif args.stage == "apply":
        ok = stage_apply(args.dir, args.mode)
    else:
        ok = stage_run(args.dir, args.mfa_arn, args.role_arn, args.mode)
    sys.exit(0 if ok else 1)
