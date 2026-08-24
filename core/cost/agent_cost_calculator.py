"""
Agent token economics -- parses transcript.jsonl, applies the model pricing matrix (PRD v14 FR-01).

The rate table below is NOT the hardcoded-cost-data this repo forbids elsewhere. budget_calculator.py
refuses to hold AWS SKU prices because AWS is the only authority on what an AWS resource costs, and
a guessed SKU rate is unfalsifiable. Model inference rates are different in kind: they are contract
terms stated in PRD v14 FR-01, fixed per million tokens, and the token counts they multiply come
from the transcript rather than from an estimate. Nothing here invents a quantity.

The property that matters more than the arithmetic is absence. Three situations look alike in a
naive reader and must not:

  * the transcript does not exist            -> available False, every total None
  * a step carries no token_usage            -> token_usage["present"] False, cost unavailable
  * a stdlib step really did cost nothing    -> present True, total_usd 0.0

Only the third is a zero. A dashboard that renders the first two as "$0.0000" has reported a number
that no evidence supports, which is the failure mode this whole module is shaped around.

Malformed lines are counted, never dropped silently: this sits on a reporting path, so a bad line
must not abort the run, but a run that quietly parsed nine of ten steps has understated its own cost.

Depends on: nothing (standard library only -- PRD v14 acceptance invariant 5)
Shells out to: nothing. Reads transcript.jsonl; makes no network call and spends nothing.
Used by: app/console_app.py (COST -> AGENTS COST), tests/test_agent_cost.py
"""
import datetime
import json
import os
import re

# FR-01, verbatim. USD per one million tokens.
PRICING = {
    "pro": {"input": 1.25, "output": 10.00, "cached": 0.30},
    "flash": {"input": 0.10, "output": 0.40, "cached": 0.025},
    "stdlib": {"input": 0.0, "output": 0.0, "cached": 0.0},
}

# Model strings seen in transcripts, mapped onto the three FR-01 tiers. Anything absent from
# this map is left unpriced rather than guessed onto a neighbouring tier -- see _tier_of.
TIER_ALIASES = {
    "pro": "pro",
    "flash": "flash",
    "haiku": "flash",
    "stdlib": "stdlib",
    "local": "stdlib",
}

# FR-01's capacity gauge is specified against a 1M-token ceiling and nothing in the PRD gives a
# per-model figure, so there is one ceiling and it is a parameter. Adding a per-model table would
# mean inventing ceilings for tiers the spec never sized.
DEFAULT_CONTEXT_CEILING = 1_000_000

# Acceptance criterion 2: "alerts if context exceeds 80% of model limits".
CONTEXT_ALERT_FRACTION = 0.80

# Money rounds at the step, and the run total is the sum of those rounded steps. Summing the raw
# floats instead would give a total that does not equal the ledger column above it, and an
# accordion whose rows do not add up reads as a bug even when the total is the more precise one.
USD_PRECISION = 6


def _tier_of(model):
    """Normalise a transcript model string onto an FR-01 tier, or None if it is not one of them."""
    if not isinstance(model, str):
        return None
    return TIER_ALIASES.get(model.strip().lower())


def price_step(tier, prompt_tokens, completion_tokens, cached_tokens):
    """Apply the FR-01 matrix. Returns an unavailable result for a tier with no published rate."""
    rates = PRICING.get(_tier_of(tier) or tier)
    if rates is None:
        return {"available": False, "reason": f"no published rate for model {tier!r}",
                "input_usd": None, "output_usd": None, "cached_usd": None, "total_usd": None}
    parts = {
        "input_usd": round(prompt_tokens / 1e6 * rates["input"], USD_PRECISION),
        "output_usd": round(completion_tokens / 1e6 * rates["output"], USD_PRECISION),
        "cached_usd": round(cached_tokens / 1e6 * rates["cached"], USD_PRECISION),
    }
    parts["total_usd"] = round(sum(parts.values()), USD_PRECISION)
    parts["available"] = True
    parts["reason"] = None
    return parts


def _parse_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        # Trailing Z is valid ISO 8601 but only fromisoformat 3.11+ accepts it directly.
        return datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


# --- FR-03 / FR-07 secret redaction -----------------------------------------------------
#
# Two independent signals, because either one alone leaks. Shape catches a credential pasted
# into free-form reasoning text where no key name surrounds it; key name catches a passphrase
# that looks like ordinary prose and no pattern would ever match. Scrubbing happens at parse
# time rather than at render time so there is no unredacted copy for a later caller to reach
# for -- FR-03 says "before rendering", and the cheapest way to guarantee that is never to
# hold the raw string in the first place.

REDACTION = "[REDACTED_SECRET]"

_SECRET_PATTERNS = (
    # Whole PEM block, and it must come first: the base64 body would otherwise survive the
    # narrower patterns untouched.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
               re.DOTALL),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/-]+=*", re.IGNORECASE),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    # AWS key ids are a fixed 4-character prefix plus exactly 16 uppercase alphanumerics.
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
)

# key = value / "key": "value" written inline in a log line or a tool argument string.
_SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    ( \b (?: password | passwd | pwd | secret | api[_-]?key | access[_-]?key
            | auth[_-]?token | session[_-]?token | token | credentials? )
      \b \s* ["']? \s* [:=] \s* ["']? )
    ( [^\s"',;}\]]+ )
    """)

# Matched against a dict key with its separators stripped, as a suffix: `db_password` is a
# secret, `token_usage` is not, and a substring test cannot tell those apart.
_SECRET_KEY_SUFFIXES = ("password", "passwd", "pwd", "secret", "token", "apikey",
                        "accesskey", "secretkey", "privatekey", "credential", "credentials",
                        "authorization", "auth")


def _is_secret_key(key):
    if not isinstance(key, str):
        return False
    normalised = re.sub(r"[^a-z0-9]", "", key.lower())
    return normalised.endswith(_SECRET_KEY_SUFFIXES)


def _redact_text(text):
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTION, text)
    return _SECRET_ASSIGNMENT.sub(lambda m: m.group(1) + REDACTION, text)


def redact(value):
    """Scrub credentials out of a string or an arbitrarily nested JSON structure.

    Returns a new structure; the caller's input is left alone. Non-string scalars pass
    through untouched, which is what keeps the token counts and dollar figures intact.
    """
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {k: REDACTION if _is_secret_key(k) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "cached_tokens")


def _extract_usage(record):
    """Pull token_usage out of a record, marking `present` only when all three counts are real.

    A partially reported usage block keeps the counts it does have -- they were measured and
    are worth showing -- but does not get priced, because the missing field would have to be
    assumed zero to produce a total, and an assumed zero in a dollar column is a made-up number.
    """
    raw = record.get("token_usage")
    if not isinstance(raw, dict):
        return dict.fromkeys(USAGE_FIELDS), False
    usage = {}
    for field in USAGE_FIELDS:
        value = raw.get(field)
        # bool is an int subclass, and `True` in a token count means the writer was confused.
        usage[field] = value if isinstance(value, int) and not isinstance(value, bool) else None
    return usage, all(usage[field] is not None for field in USAGE_FIELDS)


def _unavailable_cost(reason):
    return {"available": False, "reason": reason, "input_usd": None, "output_usd": None,
            "cached_usd": None, "total_usd": None}


def _parse_step(record):
    usage, present = _extract_usage(record)
    tier = _tier_of(record.get("model"))
    if not present:
        cost = _unavailable_cost("step reports no usable token_usage")
        context_tokens = None
    else:
        cost = price_step(tier, usage["prompt_tokens"], usage["completion_tokens"],
                          usage["cached_tokens"])
        # Cached tokens are counted as occupying the window alongside the prompt. Providers
        # differ on whether the cached count is a subset of the prompt count; summing is the
        # direction that trips the pressure alert early rather than late.
        context_tokens = usage["prompt_tokens"] + usage["cached_tokens"]
    usage["present"] = present
    return {
        "step_index": record.get("step_index"),
        "source": record.get("source"),
        "type": record.get("type"),
        "created_at": record.get("created_at"),
        "thinking": record.get("thinking"),
        "tool_calls": record.get("tool_calls") or [],
        "model": record.get("model"),
        "tier": tier,
        "token_usage": usage,
        "cost": cost,
        "context_tokens": context_tokens,
        "latency_seconds": None,
        "raw": record,
    }


def _apply_latencies(steps):
    previous = None
    for step in steps:
        current = _parse_timestamp(step["created_at"])
        if previous is not None and current is not None:
            step["latency_seconds"] = (current - previous).total_seconds()
        previous = current


def _summarise(steps, context_ceiling, malformed_lines):
    """Aggregate the run. Every total is None unless at least one step actually supports it."""
    priced = [s for s in steps if s["cost"]["available"]]
    totals = {
        "steps_total": len(steps),
        "steps_priced": len(priced),
        "steps_missing_usage": sum(1 for s in steps if not s["token_usage"]["present"]),
        "steps_unpriced_model": sum(1 for s in steps
                                    if s["token_usage"]["present"] and not s["cost"]["available"]),
        "malformed_lines": malformed_lines,
    }
    if priced:
        totals["input_tokens"] = sum(s["token_usage"]["prompt_tokens"] for s in priced)
        totals["output_tokens"] = sum(s["token_usage"]["completion_tokens"] for s in priced)
        totals["cached_tokens"] = sum(s["token_usage"]["cached_tokens"] for s in priced)
        totals["total_usd"] = round(sum(s["cost"]["total_usd"] for s in priced), USD_PRECISION)
        peak = max(s["context_tokens"] for s in priced)
    else:
        # Not zero. Nothing in this run reported a token count, so there is no total to give.
        totals.update(dict.fromkeys(
            ("input_tokens", "output_tokens", "cached_tokens", "total_usd")))
        peak = None
    latencies = [s["latency_seconds"] for s in steps if s["latency_seconds"] is not None]
    totals["total_latency_seconds"] = round(sum(latencies), 3) if latencies else None
    totals["peak_context_tokens"] = peak
    totals["context_ceiling"] = context_ceiling
    totals["peak_context_fraction"] = None if peak is None else peak / context_ceiling
    totals["context_alert"] = (None if peak is None
                               else totals["peak_context_fraction"] > CONTEXT_ALERT_FRACTION)
    return totals


def _absent(path, reason, context_ceiling):
    return {"available": False, "reason": reason, "path": path, "steps": [],
            "summary": _summarise([], context_ceiling, 0)}


def analyse_run(transcript_path, context_ceiling=DEFAULT_CONTEXT_CEILING):
    """Parse one transcript.jsonl into per-step cost records plus a run summary.

    Fail-soft by design: this feeds a console view, so an unreadable transcript returns an
    explicitly-absent result instead of raising. `available` is the flag a caller checks
    before rendering any figure at all.
    """
    path = os.path.abspath(transcript_path)
    steps = []
    malformed = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    malformed += 1
                    continue
                if not isinstance(record, dict):
                    malformed += 1
                    continue
                # Redacted here, at the only door the raw line comes through.
                steps.append(_parse_step(redact(record)))
    except OSError as exc:
        return _absent(path, f"transcript not readable: {exc.strerror or exc}", context_ceiling)
    _apply_latencies(steps)
    return {
        "available": True,
        "reason": None,
        "path": path,
        "steps": steps,
        "summary": _summarise(steps, context_ceiling, malformed),
    }
