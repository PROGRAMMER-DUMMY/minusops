"""
pricing_catalog.py — the single source of truth for "what AWS service prices a Terraform
resource type", replacing three formerly-independent hand-maintained tables that all shared
the same blind spots (bcm_pricing_calculator._SERVICE_CODE, plan_inspector.SERVICE_PREFIXES,
plan_inspector.FILE_HINTS — none of them knew about aws_mwaa_environment, aws_kinesis_stream,
or aws_sns_topic, because each list was written independently).

Two tiers of data:
  core/cost/pricing_data/aws_resource_map.json  — resource-type prefix -> serviceCode (+ display
      name, verification status). This is a REVIEWED, committed file, not invented at runtime.
  core/cost/pricing_data/free_resources.json    — resource-type prefixes confirmed to carry no
      billable SKU, ever. Also reviewed and committed.

Optional, read-only AWS Price List catalog lookups (list_service_codes / lookup_dimensions)
help a human resolve resource types that aren't in either file yet — they never invent a
serviceCode, usageType, or price; they only surface what AWS's own catalog says exists, for a
reviewer to fold into aws_resource_map.json. These calls are read-only (no cost, no resources
created), so — like identity()/cost_by_service()/anomalies() in providers/aws.py — they are NOT
routed through approval.py; only AWS-side WRITES (bcm_pricing_calculator.run) are gated.
"""
import json
import os
import subprocess
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
import toolpath  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pricing_data")
CACHE_DIR = os.path.join(os.getcwd(), ".agents", "cache")

_map_cache = None
_free_cache = None


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _sorted_prefixes(entries):
    """Longest prefix first, so a specific entry (aws_kinesis_firehose) is checked before a
    more general one (aws_kinesis) instead of depending on file insertion order."""
    return sorted(entries, key=lambda e: len(e["prefix"]), reverse=True)


def _resource_map():
    global _map_cache
    if _map_cache is None:
        doc = _load_json(os.path.join(DATA_DIR, "aws_resource_map.json"))
        _map_cache = _sorted_prefixes(doc["prefixes"])
    return _map_cache


def _free_registry():
    global _free_cache
    if _free_cache is None:
        doc = _load_json(os.path.join(DATA_DIR, "free_resources.json"))
        _free_cache = _sorted_prefixes(doc["prefixes"])
    return _free_cache


def resolve_resource_type(tf_type):
    """Return the reviewed mapping entry for a Terraform resource type, or None if unresolved.
    Never guesses: only prefixes present in aws_resource_map.json are matched."""
    for entry in _resource_map():
        if tf_type.startswith(entry["prefix"]):
            return entry
    return None


def confirmed_free(tf_type):
    """Return the free-resource entry for a Terraform resource type, or None."""
    for entry in _free_registry():
        if tf_type.startswith(entry["prefix"]):
            return entry
    return None


def entry_for_service_code(service_code):
    """Reverse lookup: the aws_resource_map.json entry for a serviceCode, or None. Used by
    bcm_pricing_calculator's amount dispatcher to read an entry's amount_model without
    threading the original Terraform resource type through separately. When multiple entries
    share a service_code (e.g. aws_cloudwatch_metric_alarm vs. the generic aws_cloudwatch),
    _resource_map() is already sorted longest-prefix-first, so this returns the most SPECIFIC
    one -- the same "most specific wins" rule resolve_resource_type() uses."""
    for entry in _resource_map():
        if entry["service_code"] == service_code:
            return entry
    return None


def rate_citation_for_service_code(service_code):
    """A reviewed, dated AWS Price List rate/free-tier fact for a not-estimated service, or
    None. Never a total (that would still require a usage count we don't have) -- just the
    catalog rate, citable the same way every other number in this system is."""
    entry = entry_for_service_code(service_code)
    return (entry or {}).get("rate_citation")


def service_display_name(tf_type):
    """Human-readable service name for reports, checking priced types then confirmed-free
    types, falling back to 'Other' — used by plan_inspector in place of its old static list."""
    priced = resolve_resource_type(tf_type)
    if priced:
        return priced["display_name"]
    free = confirmed_free(tf_type)
    if free:
        return free["display_name"]
    return "Other"


def file_hint(tf_type):
    priced = resolve_resource_type(tf_type)
    if priced and priced.get("file_hint"):
        return priced["file_hint"]
    return None


# ---------------------------------------------------------------------------
# Optional live AWS Price List catalog lookups (read-only, no approval gate).
# ---------------------------------------------------------------------------
def _aws_cli():
    exe = toolpath.find_tool("aws")
    if exe:
        return exe
    raise FileNotFoundError("aws CLI not found on PATH or in standard install locations")


def _run_json(cmd, timeout=30):
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip() or f"command failed: {cmd}")
    return json.loads(res.stdout or "{}")


def _write_cache(name, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _read_cache(name):
    path = os.path.join(CACHE_DIR, name)
    if os.path.exists(path):
        return _load_json(path)
    return None


def list_service_codes(refresh=False):
    """Every AWS Price List serviceCode + its attribute names, via `aws pricing
    describe-services`. Read-only, cached to .agents/cache/aws_service_codes.json. Used to help
    a human resolve a resource type that's missing from aws_resource_map.json — never trusted
    automatically as a mapping."""
    if not refresh:
        cached = _read_cache("aws_service_codes.json")
        if cached is not None:
            return cached
    aws = _aws_cli()
    services = []
    next_token = None
    while True:
        cmd = [aws, "pricing", "describe-services", "--output", "json", "--max-results", "100"]
        if next_token:
            cmd += ["--starting-token", next_token]
        page = _run_json(cmd)
        services.extend(page.get("Services", []))
        next_token = page.get("NextToken")
        if not next_token:
            break
    _write_cache("aws_service_codes.json", services)
    return services


def lookup_dimensions(service_code, region="us-east-1", refresh=False):
    """Valid usageType/operation values for a serviceCode, via `aws pricing
    get-attribute-values`. Read-only, cached per service+region. This speeds up the human
    catalog-verification step (filling REVIEW_REQUIRED fields in bcm-usage.json) — the output
    is always written for review, never submitted to a BCM estimate directly."""
    cache_name = f"pricing_dims_{service_code}_{region}.json"
    if not refresh:
        cached = _read_cache(cache_name)
        if cached is not None:
            return cached
    aws = _aws_cli()
    result = {}
    for attr in ("usagetype", "operation"):
        cmd = [aws, "pricing", "get-attribute-values", "--service-code", service_code,
               "--attribute-name", attr, "--output", "json"]
        try:
            page = _run_json(cmd)
        except RuntimeError as exc:
            result[attr] = {"error": str(exc)}
            continue
        # Full, unfiltered catalog values — a reviewer narrows by region/dimension manually.
        # (usageType strings embed region as an ambiguous short code, e.g. "USE1-", not the
        # region name, so any substring filter here would silently drop real matches.)
        result[attr] = [v.get("Value") for v in page.get("AttributeValues", []) if v.get("Value")]
    result["region_hint"] = region
    _write_cache(cache_name, result)
    return result


def main():
    import argparse
    ap = argparse.ArgumentParser(description="AWS pricing catalog lookups (read-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="show the reviewed mapping for a Terraform resource type")
    r.add_argument("resource_type")
    ls = sub.add_parser("list-services", help="list every AWS Price List serviceCode (live AWS call)")
    ls.add_argument("--refresh", action="store_true")
    d = sub.add_parser("dimensions", help="show usageType/operation values for a serviceCode (live AWS call)")
    d.add_argument("service_code")
    d.add_argument("--region", default="us-east-1")
    d.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    if args.cmd == "resolve":
        priced = resolve_resource_type(args.resource_type)
        free = confirmed_free(args.resource_type)
        print(json.dumps({"priced": priced, "confirmed_free": free}, indent=2))
        return 0
    if args.cmd == "list-services":
        print(json.dumps(list_service_codes(refresh=args.refresh), indent=2))
        return 0
    if args.cmd == "dimensions":
        print(json.dumps(lookup_dimensions(args.service_code, args.region, refresh=args.refresh), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
