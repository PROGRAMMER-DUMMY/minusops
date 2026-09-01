"""
Agent token economics & telemetry profiler -- parses transcript.jsonl, applies model pricing matrix.

Tracks:
  - Token economics across multi-agent sessions (Input, Output, Thinking, Cached tokens)
  - Subagent task lifecycles & invocation hierarchy
  - Execution bottlenecks (step latencies vs LLM inference time)
  - Context window pressure (alerting at >80% of ceiling)
"""
import datetime
import json
import os
import re
import urllib.request
import urllib.error

# Verbatim FR-01 pricing matrix. USD per one million tokens.
PRICING = {
    "pro": {"input": 1.25, "output": 10.00, "cached": 0.30},
    "flash": {"input": 0.10, "output": 0.40, "cached": 0.025},
    "stdlib": {"input": 0.0, "output": 0.0, "cached": 0.0},
    # Extended Multi-Model Provider Rates
    "claude-3-7-sonnet": {"input": 3.00, "output": 15.00, "cached": 0.30},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00, "cached": 0.30},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00, "cached": 0.08},
    "gpt-4o": {"input": 2.50, "output": 10.00, "cached": 1.25},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cached": 0.075},
    "o3-mini": {"input": 1.10, "output": 4.40, "cached": 0.55},
}

TIER_ALIASES = {
    "pro": "pro",
    "flash": "flash",
    "haiku": "flash",
    "sonnet": "claude-3-7-sonnet",
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
    "stdlib": "stdlib",
    "local": "stdlib",
}

CACHE_DIR = os.path.join(".agents", "cache")
MODEL_PRICING_CACHE = "model_pricing_catalog.json"
LIVE_PRICING_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"

DEFAULT_CONTEXT_CEILING = 1_000_000
CONTEXT_ALERT_FRACTION = 0.80
USD_PRECISION = 6


def fetch_live_pricing(refresh=False):
    """Fetch model pricing from the public multi-provider registry, caching it to disk.

    Always returns {"models": {...}, "source": str, "fetched_at": str|None, "reason": str|None}.
    `reason` is the point: this used to swallow every failure and return an empty dict, so a
    network outage, a malformed response and "the registry genuinely lists nothing" were the
    same value. A caller that cannot tell a failed fetch from an empty one cannot report
    honestly on where its rates came from.
    """
    cache_path = os.path.join(CACHE_DIR, MODEL_PRICING_CACHE)
    if not refresh and os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, dict) and cached.get("models"):
                return {"models": cached["models"], "source": "cache",
                        "fetched_at": cached.get("fetched_at"), "reason": None}
        except (OSError, ValueError) as exc:
            # Fall through to the network; the cache being unreadable is not fatal, but it is
            # reported if the network then fails too.
            cache_error = f"cache unreadable: {exc}"
        else:
            cache_error = "cache held no models"
    else:
        cache_error = None

    try:
        req = urllib.request.Request(LIVE_PRICING_URL,
                                     headers={"User-Agent": "MinusOps-FinOps/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"models": {}, "source": "unavailable", "fetched_at": None,
                "reason": f"{exc}" + (f" ({cache_error})" if cache_error else "")}

    if not isinstance(data, dict):
        return {"models": {}, "source": "unavailable", "fetched_at": None,
                "reason": "registry response was not an object"}

    parsed = {}
    for name, entry in data.items():
        if (isinstance(entry, dict) and "input_cost_per_token" in entry
                and "output_cost_per_token" in entry):
            parsed[name.lower()] = {
                "input": round(entry["input_cost_per_token"] * 1e6, 4),
                "output": round(entry["output_cost_per_token"] * 1e6, 4),
                "cached": round(entry.get("cache_read_input_token_cost", 0) * 1e6, 4),
            }
    if not parsed:
        return {"models": {}, "source": "unavailable", "fetched_at": None,
                "reason": "registry listed no model with both an input and an output rate"}

    fetched_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": fetched_at, "models": parsed}, f, indent=2)
    except OSError:
        pass  # a cache we cannot write is not a reason to discard rates we did fetch
    return {"models": parsed, "source": LIVE_PRICING_URL, "fetched_at": fetched_at,
            "reason": None}


def _tier_of(model):
    """Normalise a transcript model string onto an FR-01 tier, or None if it is not one of them."""
    if not isinstance(model, str):
        return None
    cleaned = model.strip().lower()
    return TIER_ALIASES.get(cleaned)


def price_step(tier, prompt_tokens, completion_tokens, cached_tokens, live_models=None):
    """Apply the FR-01 matrix, or a live registry rate when one is supplied.

    `rate_source` on the result names which was used. Without it a figure priced from the
    built-in matrix and one priced from a third-party registry are indistinguishable, and this
    module's whole job is telling a reader where a number came from.
    """
    resolved = _tier_of(tier) or tier
    rates, source = PRICING.get(resolved), "matrix"
    if live_models:
        live = live_models.get(str(resolved).lower()) or live_models.get(str(tier).lower())
        if live:
            rates, source = live, "live-registry"
    if rates is None:
        return {"available": False, "reason": f"no published rate for model {tier!r}",
                "rate_source": None,
                "input_usd": None, "output_usd": None, "cached_usd": None, "total_usd": None}
    parts = {
        "input_usd": round(prompt_tokens / 1e6 * rates["input"], USD_PRECISION),
        "output_usd": round(completion_tokens / 1e6 * rates["output"], USD_PRECISION),
        "cached_usd": round(cached_tokens / 1e6 * rates["cached"], USD_PRECISION),
    }
    parts["total_usd"] = round(sum(parts.values()), USD_PRECISION)
    parts["available"] = True
    parts["reason"] = None
    parts["rate_source"] = source
    return parts


def _parse_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


# --- Secret Redaction --------------------------------------------------------
REDACTION = "[REDACTED_SECRET]"
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/-]+=*", re.IGNORECASE),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
)
_SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    ( \b (?: password | passwd | pwd | secret | api[_-]?key | access[_-]?key
            | auth[_-]?token | session[_-]?token | token | credentials? )
      \b \s* ["']? \s* [:=] \s* ["']? )
    ( [^\s"',;}\]]+ )
    """)
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
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {k: REDACTION if _is_secret_key(k) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "cached_tokens")


def _estimate_step_tokens(record):
    """Derive a token count from visible text when the provider counters are absent.

    Every number this returns is a guess, which is why the result carries `estimated` and why
    _summarise keeps it out of the measured totals. The ratios are the usual rough 4-chars-per
    -token for text the model produced, and 8 for a prompt this record only partially shows.

    The prompt of a step with no visible content contributes 0, not a number. It used to
    contribute 50, which had no basis in anything -- a step whose prompt cannot be seen has an
    unknown prompt, and 0 at least does not add invented tokens to a total someone reads.
    """
    source = record.get("source")
    stype = record.get("type")
    content = str(record.get("content") or "")
    thinking = str(record.get("thinking") or "")
    tool_calls = json.dumps(record.get("tool_calls") or [])

    if source == "USER_EXPLICIT" or stype == "USER_INPUT":
        prompt_tokens = max(1, len(content) // 4)
        completion_tokens = 0
    else:
        prompt_tokens = max(1, len(content) // 8) if content else 0
        comp_len = len(content) + len(thinking) + (len(tool_calls) if tool_calls != "[]" else 0)
        completion_tokens = max(1, comp_len // 4)

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": 0,
        "estimated": True,
    }


def _extract_usage(record, allow_estimation=False):
    """Pull token_usage out of a record, marking `present` only when all three counts are real."""
    raw = record.get("token_usage")
    if not isinstance(raw, dict):
        if allow_estimation and (record.get("content") or record.get("thinking") or record.get("tool_calls")):
            return _estimate_step_tokens(record), True
        return dict.fromkeys(USAGE_FIELDS), False

    usage = {}
    for field in USAGE_FIELDS:
        value = raw.get(field)
        usage[field] = value if isinstance(value, int) and not isinstance(value, bool) else None

    present = all(usage[field] is not None for field in USAGE_FIELDS)
    return usage, present


def _unavailable_cost(reason):
    return {"available": False, "reason": reason, "rate_source": None,
            "input_usd": None, "output_usd": None, "cached_usd": None, "total_usd": None}


def _parse_step(record, allow_estimation=False, default_model=None, live_models=None):
    usage, present = _extract_usage(record, allow_estimation=allow_estimation)
    # A record with no model of its own gets priced under `default_model`, which the transcript
    # never stated. That is recorded rather than hidden: pricing an unknown step as "pro" and a
    # measured "pro" step identically makes the two indistinguishable downstream, and the
    # cheapest tier here is 12x less on input and 25x less on output.
    stated_model = record.get("model")
    model_str = stated_model or (default_model if allow_estimation else None)
    model_assumed = bool(model_str) and not stated_model
    tier = _tier_of(model_str)
    if not present:
        cost = _unavailable_cost("step reports no usable token_usage")
        context_tokens = None
    else:
        cost = price_step(tier, usage["prompt_tokens"], usage["completion_tokens"],
                          usage.get("cached_tokens", 0) or 0, live_models=live_models)
        context_tokens = (usage.get("prompt_tokens") or 0) + (usage.get("cached_tokens") or 0)

    usage["present"] = present
    tool_calls = record.get("tool_calls") or []
    subagents = [tc for tc in tool_calls if isinstance(tc, dict) and tc.get("name") in ("invoke_subagent", "define_subagent")]

    return {
        "step_index": record.get("step_index"),
        "source": record.get("source"),
        "type": record.get("type"),
        "created_at": record.get("created_at"),
        "thinking": redact(record.get("thinking")),
        "tool_calls": redact(tool_calls),
        "subagents_spawned": subagents,
        "model": model_str,
        "model_assumed": model_assumed,
        "tier": tier,
        "token_usage": usage,
        "cost": cost,
        "context_tokens": context_tokens,
        "latency_seconds": None,
        "raw": redact(record),
    }


def _apply_latencies(steps):
    previous = None
    for step in steps:
        current = _parse_timestamp(step["created_at"])
        if previous is not None and current is not None:
            step["latency_seconds"] = (current - previous).total_seconds()
        previous = current


def _summarise(steps, context_ceiling, malformed_lines):
    """Totals, with measured and estimated kept apart.

    `total_usd` and the token totals count MEASURED steps only. An estimate derived from
    character counts is not a measurement, and summing the two produces a figure a reader
    cannot interpret -- the console renders this to four decimal places under the caption
    "N of M steps priced", which reads as precision the number does not have. Estimates get
    their own `estimated_usd` and `steps_estimated` so a caller can show both, or neither.

    A run with nothing measured therefore reports `total_usd: None`, which is the honest
    answer and the one this module's own callers already handle: "that is not the same as
    costing nothing."
    """
    priced = [s for s in steps
              if s["cost"]["available"] and not s["token_usage"].get("estimated")]
    estimated = [s for s in steps
                 if s["cost"]["available"] and s["token_usage"].get("estimated")]
    subagent_calls = sum(len(s.get("subagents_spawned", [])) for s in steps)
    tools_count = sum(len(s.get("tool_calls", [])) for s in steps)

    totals = {
        "steps_total": len(steps),
        "steps_priced": len(priced),
        "steps_estimated": len(estimated),
        "steps_assumed_model": sum(1 for s in steps if s.get("model_assumed")),
        "estimated_usd": (round(sum((s["cost"]["total_usd"] or 0) for s in estimated),
                                USD_PRECISION) if estimated else None),
        "estimated_prompt_tokens": (sum((s["token_usage"]["prompt_tokens"] or 0)
                                        for s in estimated) if estimated else None),
        "estimated_completion_tokens": (sum((s["token_usage"]["completion_tokens"] or 0)
                                            for s in estimated) if estimated else None),
        "steps_missing_usage": sum(1 for s in steps if not s["token_usage"]["present"]),
        "steps_unpriced_model": sum(1 for s in steps
                                    if s["token_usage"]["present"] and not s["cost"]["available"]),
        "malformed_lines": malformed_lines,
        "subagent_invocations": subagent_calls,
        "total_tool_calls": tools_count,
    }
    if priced:
        inp = sum((s["token_usage"]["prompt_tokens"] or 0) for s in priced)
        out = sum((s["token_usage"]["completion_tokens"] or 0) for s in priced)
        cac = sum((s["token_usage"]["cached_tokens"] or 0) for s in priced)
        totals["input_tokens"] = inp
        totals["total_prompt_tokens"] = inp
        totals["output_tokens"] = out
        totals["total_completion_tokens"] = out
        totals["cached_tokens"] = cac
        totals["total_cached_tokens"] = cac
        totals["total_usd"] = round(sum((s["cost"]["total_usd"] or 0) for s in priced), USD_PRECISION)
        peak = max((s["context_tokens"] or 0) for s in priced)
    else:
        totals.update(dict.fromkeys(("input_tokens", "total_prompt_tokens", "output_tokens", "total_completion_tokens", "cached_tokens", "total_cached_tokens", "total_usd")))
        peak = None

    latencies = [s["latency_seconds"] for s in steps if s["latency_seconds"] is not None]
    totals["total_latency_seconds"] = round(sum(latencies), 3) if latencies else None
    totals["peak_context_tokens"] = peak
    totals["context_ceiling"] = context_ceiling
    totals["peak_context_ceiling"] = context_ceiling
    totals["peak_context_fraction"] = None if peak is None else peak / context_ceiling
    totals["context_alert"] = (None if peak is None
                               else totals["peak_context_fraction"] > CONTEXT_ALERT_FRACTION)
    return totals


def analyse_run(transcript_path, context_ceiling=DEFAULT_CONTEXT_CEILING,
                allow_estimation=False, default_model="pro", use_live_pricing=False):
    """Parse a transcript into priced steps.

    `use_live_pricing` is opt-in and off by default: it reaches the network, and a rate that
    changes under you is not something to enable silently. The returned `pricing` block names
    the source actually used and carries the reason when a fetch failed, so a caller can say
    which rates produced its figures rather than implying they are canonical.
    """
    path = os.path.abspath(transcript_path)
    pricing = {"models": {}, "source": "matrix", "fetched_at": None, "reason": None}
    if use_live_pricing:
        pricing = fetch_live_pricing()
    live_models = pricing.get("models") or None
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
                steps.append(_parse_step(record, allow_estimation=allow_estimation,
                                          default_model=default_model,
                                          live_models=live_models))
    except (OSError, IOError) as exc:
        return {"available": False, "reason": str(exc), "path": path, "steps": [],
                "pricing": pricing, "summary": _summarise([], context_ceiling, 0)}

    _apply_latencies(steps)
    summary = _summarise(steps, context_ceiling, malformed)
    return {
        "available": True,
        "reason": None,
        "path": path,
        "pricing": pricing,
        "steps": steps,
        "summary": summary,
    }
