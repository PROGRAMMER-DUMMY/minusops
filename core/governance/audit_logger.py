"""Write one audit event to the local hash chain, then ship it to optional remote sinks.

The local chain is tamper-EVIDENT, not tamper-PROOF: anyone with the workstation can delete
audit.jsonl outright, and a chain that no longer exists proves nothing. Shipping each event
to a second, append-only destination (S3 or CloudWatch Logs) is what makes deletion
detectable rather than silent. Both sinks are opt-in via env var and OFF by default.

Depends on: audit_chain, toolpath (both imported via the core/ sys.path shim below)
Shells out to: aws CLI — `s3 cp`, `logs create-log-stream`, `logs put-log-events`
Used by: core/generation/schema_watch.py, tests/test_team_isolation.py
"""
import os
import sys
import datetime
import argparse
import getpass
import json
import subprocess

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
import audit_chain  # noqa: E402
import toolpath  # noqa: E402

# Unset means "no remote sink". Enabling one unasked would make every local test and
# offline run try to reach AWS.
S3_BUCKET_ENV = "MINUS_AUDIT_S3_BUCKET"
S3_PREFIX_ENV = "MINUS_AUDIT_S3_PREFIX"
CW_GROUP_ENV = "MINUS_AUDIT_CW_GROUP"
CW_STREAM_ENV = "MINUS_AUDIT_CW_STREAM"


def _aws(args, stdin=None, timeout=20):
    binary = toolpath.find_tool("aws")
    if not binary:
        return False, "aws CLI not found on PATH"
    try:
        result = subprocess.run([binary, *args], input=stdin, capture_output=True,
                                text=True, timeout=timeout)
    except Exception as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "").strip()[:300]
    return True, ""


def _ship_s3(event, line):
    """One immutable object per event, keyed by timestamp and chain hash.

    One object per event rather than an append: S3 has no append, and rewriting a single
    growing object under Object Lock would either be refused or silently retain every old
    version. A per-event key is also what makes GOVERNANCE-mode Object Lock meaningful --
    each object is written once and cannot be replaced for its retention window.
    """
    bucket = os.environ.get(S3_BUCKET_ENV)
    if not bucket:
        return None
    prefix = os.environ.get(S3_PREFIX_ENV, "audit").strip("/")
    stamp = event["timestamp"].replace(":", "").replace("-", "")
    # entry_hash is what audit_chain.append actually stores; a wrong key name here would
    # silently name every object "nohash-..." and collide them.
    key = f"{prefix}/{stamp}-{event.get('entry_hash', 'nohash')[:16]}.json"
    ok, err = _aws(["s3", "cp", "-", f"s3://{bucket}/{key}",
                    "--content-type", "application/json"], stdin=line)
    return {"target": f"s3://{bucket}/{key}", "ok": ok, "error": err or None}


def _ship_cloudwatch(event, line):
    group = os.environ.get(CW_GROUP_ENV)
    if not group:
        return None
    stream = os.environ.get(CW_STREAM_ENV, "minusops-audit")
    # Best-effort stream creation: it already existing is the normal case, not an error.
    _aws(["logs", "create-log-stream", "--log-group-name", group,
          "--log-stream-name", stream])
    millis = int(datetime.datetime.fromisoformat(event["timestamp"]).timestamp() * 1000)
    ok, err = _aws(["logs", "put-log-events", "--log-group-name", group,
                    "--log-stream-name", stream,
                    "--log-events", json.dumps([{"timestamp": millis, "message": line}])])
    return {"target": f"cloudwatch:{group}/{stream}", "ok": ok, "error": err or None}


def ship_event(event):
    """Emit one already-chained event to every configured remote sink.

    Returns a list of per-sink results, empty when nothing is configured. Never raises: the
    local chain is the system of record, and losing an event locally because a remote sink
    was unreachable would be strictly worse than the gap this feature exists to close.
    """
    line = json.dumps(event, sort_keys=True)
    return [r for r in (_ship_s3(event, line), _ship_cloudwatch(event, line)) if r]


def log_audit_event(action, details, log_dir):
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "audit.jsonl")

    event = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "operator": getpass.getuser(),
        "action": action,
        "details": details,
        "status": "RECORDED",
    }

    try:
        # Append through the tamper-evident hash chain (single shared chain).
        recorded = audit_chain.append(log_file, event)
        print(f"[AUDIT] Event successfully logged: {action}")
    except Exception as e:
        print(f"[ERR] Failed to write to audit log: {e}", file=sys.stderr)
        return False

    # Ship AFTER the local append, and only what the chain actually recorded, so the remote
    # copy carries the same chain_hash a verifier will recompute. Shipping first would emit
    # events that never made it into the chain.
    for result in ship_event(recorded if isinstance(recorded, dict) else event):
        if result["ok"]:
            print(f"[AUDIT] shipped to {result['target']}")
        else:
            # Loud, and non-fatal. A silent shipping failure is the same blind spot as no
            # shipper at all, but refusing the local write would lose the event entirely.
            print(f"[WARN] audit shipping FAILED to {result['target']}: {result['error']}",
                  file=sys.stderr)
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit Logger for Agentic DevOps")
    parser.add_argument("--action", required=True, help="Action name being executed")
    parser.add_argument("--details", required=True, help="Detailed description of the action")
    parser.add_argument("--log-dir", default=os.path.join(os.getcwd(), ".agents", "logs"), help="Path to audit logs directory")

    args = parser.parse_args()
    success = log_audit_event(args.action, args.details, args.log_dir)
    sys.exit(0 if success else 1)
