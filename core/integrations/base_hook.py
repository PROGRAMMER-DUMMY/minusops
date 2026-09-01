"""
Shared HTTP, credential resolution, and approval plumbing for every integration hook.

Three invariants the hooks above this file rely on and must not work around:
  1. No credential is ever a function parameter. A caller names an env var (and optionally a
     Secrets Manager ARN); `resolve_secret()` reads it at send time. A token passed as an
     argument ends up in a repr, a traceback, or a plan file — that is failure mode FM-02.
  2. Every outbound send goes through `gated()`, which calls `approval.request_approval()`
     before the sender runs. Denied means the network call is never attempted, not that the
     result is discarded afterwards.
  3. Nothing raises. Transport, DNS, and HTTP errors all come back as
     `{"ok": False, "status": int, "error": str}` so a notification can never abort a deploy.

Implemented as module functions rather than the `BaseIntegrationHook` class sketched in the
implementation plan: there is exactly one implementation and no state to carry, so a class
would add a `self` nobody uses.

Depends on: core/governance/approval.py (request_approval), core/providers/aws.py (run_aws —
    lazily imported, and only when a Secrets Manager ARN is actually in play, so importing
    this module offline stays free of provider side effects)
Shells out to: HTTPS endpoints supplied by the calling hook (Slack/Teams webhooks, Confluence
    and Jira Cloud REST APIs) and `aws secretsmanager get-secret-value` for ARN-backed secrets
Used by: core/integrations/slack_hook.py, core/integrations/teams_hook.py,
    core/integrations/outlook_hook.py, core/integrations/confluence_hook.py,
    core/integrations/jira_hook.py, tests/test_integrations.py
"""
import os
import sys
import collections
import hashlib
import time
import json
import base64
import socket
import urllib.error
import urllib.request

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
from approval import request_approval  # noqa: E402

DEFAULT_TIMEOUT = 10


def resolve_secret(env_var, secret_arn=None):
    """
    Return the secret value for `env_var`, or None if it is not configured.

    Order: the environment variable itself, then a Secrets Manager ARN (the `secret_arn`
    argument or `<ENV_VAR>_SECRET_ARN`). An ARN is an identifier, not a credential — it is
    safe in a plan, in a log line, and in an audit record. The secret value is not.
    """
    value = os.environ.get(env_var)
    if value:
        return value.strip()
    arn = secret_arn or os.environ.get(env_var + "_SECRET_ARN")
    if not arn:
        return None
    from providers.aws import run_aws  # lazy: keeps offline imports provider-free
    ok, out, _err = run_aws([
        "secretsmanager", "get-secret-value", "--secret-id", arn,
        "--query", "SecretString", "--output", "text",
    ])
    if not ok or not out:
        return None
    if isinstance(out, dict):
        # A JSON secret blob: pick the key named after the env var, either case.
        return out.get(env_var) or out.get(env_var.lower()) or None
    return str(out).strip()


def basic_auth_header(user_env, token_env, secret_arn=None):
    """Build an Atlassian-style Basic auth header, or None when either half is unconfigured."""
    user = (os.environ.get(user_env) or "").strip()
    token = resolve_secret(token_env, secret_arn)
    if not user or not token:
        return None
    encoded = base64.b64encode(f"{user}:{token}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def request_json(url, payload=None, headers=None, method="POST", timeout=DEFAULT_TIMEOUT):
    """
    Send a JSON request and return a result dict. Never raises.

    Returns {"ok": True, "status": int, "body": str} or
            {"ok": False, "status": int, "error": str}.
    Status conventions on the failure side: the real HTTP code for an HTTPError, 504 for a
    timeout, 502 for a transport/DNS failure, 500 for anything else.
    """
    data = None
    hdrs = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    hdrs.setdefault("Accept", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            body = resp.read()
            return {"ok": True, "status": status,
                    "body": body.decode("utf-8", "replace") if body else ""}
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            detail = str(e)
        return {"ok": False, "status": e.code, "error": detail}
    except (socket.timeout, TimeoutError):
        return {"ok": False, "status": 504, "error": f"timed out after {timeout}s"}
    except urllib.error.URLError as e:
        # A urlopen timeout surfaces here as URLError(socket.timeout) on some platforms.
        if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
            return {"ok": False, "status": 504, "error": f"timed out after {timeout}s"}
        return {"ok": False, "status": 502, "error": str(e.reason or e)}
    except Exception as e:
        return {"ok": False, "status": 500, "error": str(e)}


def post_json(url, payload, headers=None, timeout=DEFAULT_TIMEOUT):
    """POST a JSON body. Thin alias over request_json for the webhook hooks."""
    return request_json(url, payload=payload, headers=headers, method="POST", timeout=timeout)


def not_configured(what):
    """
    Result for an approved send whose destination is not wired up.

    ok=True with sent=False: the operator authorised the action and nothing failed — the
    endpoint simply is not configured. Callers distinguish this from a delivery failure by
    `sent`, never by `ok`.
    """
    return {"ok": True, "status": 0, "sent": False, "reason": "not_configured",
            "error": f"{what} is not configured"}


DEDUP_WINDOW_SECONDS = 300

# (timestamp, fingerprint) of alerts actually delivered, oldest first. A deque because the
# only operations are append-right and expire-left, and both are O(1) -- the window stays
# bounded by elapsed time rather than by message volume, so a 50-alert burst costs 50 short
# tuples for five minutes and nothing after.
_recent_sends = collections.deque()


def _now():
    """Monotonic clock. Wall time can step backwards (NTP correction, DST on some
    platforms) and a backwards step would silently extend a cooldown."""
    return time.monotonic()


def _fingerprint(action, details):
    """Identity of an alert. Hashed rather than stored verbatim: `details` can carry a stack
    trace, and the window has no reason to hold message bodies in memory."""
    digest = hashlib.sha256()
    digest.update(action.encode("utf-8"))
    digest.update(bytes(1))   # NUL separator: "ab"+"c" must not collide with "a"+"bc"
    digest.update(details.encode("utf-8"))
    return digest.hexdigest()


def _seen_recently(fingerprint, window):
    # Every expired entry is dropped, not just the ones at the front. Popping from the left
    # until the first live entry assumes the deque is time-ordered; that holds for a
    # monotonic clock, but one out-of-order entry would then pin everything behind it in
    # memory and keep a stale fingerprint suppressing real alerts. The scan is O(n) either
    # way -- n is "alerts delivered in the last five minutes", not total volume.
    cutoff = _now() - window
    live = [item for item in _recent_sends if item[0] >= cutoff]
    _recent_sends.clear()
    _recent_sends.extend(live)
    return any(item[1] == fingerprint for item in live)


def gated(action, details, approval_mode, sender, dedup_window=DEDUP_WINDOW_SECONDS):
    """
    Run `sender()` only if the alert is not a recent duplicate AND approval allows it.

    `sender` is a zero-argument callable returning a result dict. On denial the callable is
    never invoked and {"ok": False, "status": 403, "sent": False, "reason": "not_authorized"}
    comes back, so a caller can tell "refused" from "failed" without parsing a message.

    Deduplication runs BEFORE the approval gate, deliberately: the point of a cooldown is
    that a human is not prompted fifty times for one failing job, and a check that ran after
    the prompt would leave that intact. Suppression returns ok=True with sent=False -- a
    working cooldown is not a broken integration, and callers already distinguish the two by
    `sent` rather than `ok`.

    Only a delivered alert opens a window. A denial or a failed send does not, because
    suppressing the next five minutes on the strength of a message that never arrived is how
    an outage goes unreported. Pass dedup_window=0 to disable.
    """
    fingerprint = _fingerprint(action, details)
    if dedup_window and _seen_recently(fingerprint, dedup_window):
        return {"ok": True, "status": 0, "sent": False, "reason": "deduplicated",
                "error": f"identical {action} suppressed within {dedup_window}s"}

    if not request_approval(action, details, approval_mode):
        return {"ok": False, "status": 403, "sent": False, "reason": "not_authorized",
                "error": f"approval denied for {action}"}
    result = sender()
    result.setdefault("sent", bool(result.get("ok")))
    if result.get("sent"):
        _recent_sends.append((_now(), fingerprint))
    return result
