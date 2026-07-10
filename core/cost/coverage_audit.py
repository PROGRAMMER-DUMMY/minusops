"""
coverage_audit.py — the fail-closed cost-coverage gate.

For every resource type a Terraform plan is about to create, confirm it lands in one of four
explicit, auditable states instead of silently vanishing from the cost report:

  auto_priced                 — serviceCode + catalog fields + a derived amount are all known;
                                 auto_estimate() can price this today with no human step.
  catalog_mapped_needs_usage  — serviceCode is known (core/cost/pricing_data/aws_resource_map.json),
                                 but usageType/operation and/or the usage amount still need a
                                 reviewed usage profile before it prices.
  confirmed_free               — reviewed fact (core/cost/pricing_data/free_resources.json): this
                                 resource type carries no billable AWS Price List SKU.
  unresolved                   — nothing above matches. This is the state that used to be
                                 silent (a resource type just didn't appear anywhere) — now
                                 it's a visible, auditable gap plan_gate.py can warn or block on.

Reuses bcm_pricing_calculator._plan_inventory()/_amount_for() rather than re-deriving usage
logic, and classifies through the CloudProvider abstraction (providers.base.get_provider()) so
this file is genuinely cloud-agnostic — it never imports pricing_catalog.py directly. Audit
finding 2026-07-03: an earlier version of this file DID import pricing_catalog directly,
bypassing the provider contract entirely; that made "multi-cloud coverage" aspirational rather
than real despite what the provider docstrings implied. Fixed here: whichever cloud MINUS_CLOUD
selects, this file only ever calls through the provider's resolve_resource_type()/
confirmed_free() — for AWS that still reaches pricing_catalog.py underneath, for Azure/GCP it
honestly returns everything unresolved (their pricing methods aren't implemented yet) instead
of crashing or silently assuming AWS. This file only classifies, it never prices.

Usage:
  python core/cost/coverage_audit.py audit --report-dir artifacts/reports/<hash>
"""
import argparse
import datetime
import glob
import json
import os
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
import bcm_pricing_calculator as bcm  # noqa: E402
import providers.base as pb  # noqa: E402
import modules as module_registry  # noqa: E402


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def classify(plan, provider=None):
    """Classify every resource type discovered in the plan. Never prices anything — only
    reports which of the four coverage states each resource type is in.

    `provider` defaults to providers.base.get_provider() (whichever cloud MINUS_CLOUD selects).
    Goes through the CloudProvider contract exclusively — for a cloud whose pricing discovery
    isn't implemented yet, resolve_resource_type()/confirmed_free() honestly return None for
    everything, so every resource type lands in `unresolved` rather than this function crashing
    or silently assuming AWS."""
    provider = provider or pb.get_provider()
    inventory = bcm._plan_inventory(plan)
    inputs = {k: (v or {}).get("value") for k, v in (plan.get("variables") or {}).items()}
    assumptions = dict(bcm.DEFAULT_ASSUMPTIONS)

    auto_priced, needs_usage, free, unresolved = [], [], [], []
    for rtype, info in inventory.items():
        free_entry = provider.confirmed_free(rtype)
        if free_entry:
            free.append({
                "resource_type": rtype, "count": info["count"],
                "service": free_entry["display_name"], "reason": free_entry["note"],
            })
            continue
        mapped = provider.resolve_resource_type(rtype)
        if not mapped:
            unresolved.append({
                "resource_type": rtype, "count": info["count"],
                "addresses": info["addresses"][:5],
            })
            continue
        service_code = mapped["service_code"]
        # Amount derivation (bcm._amount_for) is AWS BCM-specific usage-quantity math; only
        # meaningful when the resolved mapping actually came from AWS's pricing catalog.
        amount = bcm._amount_for(service_code, inputs, assumptions, plan) if provider.name == "aws" else None
        detail = {
            "resource_type": rtype, "count": info["count"],
            "service": mapped["display_name"], "service_code": service_code,
            "catalog_verified": bool(mapped.get("verified")),
            "amount_derivable": amount is not None,
        }
        if mapped.get("verified") and amount is not None:
            auto_priced.append(detail)
        else:
            needs_usage.append(detail)

    return {
        "provider": {"cloud": provider.name, "status": provider.status},
        "auto_priced": sorted(auto_priced, key=lambda d: d["resource_type"]),
        "catalog_mapped_needs_usage": sorted(needs_usage, key=lambda d: d["resource_type"]),
        "confirmed_free": sorted(free, key=lambda d: d["resource_type"]),
        "unresolved": sorted(unresolved, key=lambda d: d["resource_type"]),
    }


def _latest_schema_watch_report(repo_root=None, tracked_provider="aws"):
    """Best-effort, read-only lookup of the schema_watch.py's most recent diff report for the
    given Terraform provider (see core/generation/schema_watch.py). Informational only — never
    raises, never affects the auto_priced/unresolved/etc. classification above. Returns None if
    the watch hasn't run yet, matching this being a purely additive field.

    `repo_root` resolves `module_registry.output_root()` at call time (not as a default-parameter
    value bound at import time) so tests can monkeypatch it — and so this looks in the same
    wheel-safe location schema_watch.py actually writes to, never a raw dirname-derived path
    that could point inside site-packages under an installed wheel."""
    repo_root = repo_root or module_registry.output_root()
    provider_dir = os.path.join(repo_root, "recent-changes", tracked_provider)
    if not os.path.isdir(provider_dir):
        return None
    try:
        reports = sorted(
            p for p in glob.glob(os.path.join(provider_dir, "*.json"))
            if os.path.basename(p) != "schema-snapshot.json"
        )
        if not reports:
            return None
        with open(reports[-1], encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return {
        "provider": report.get("provider"),
        "resolved_version": report.get("resolved_version"),
        "generated_at": report.get("generated_at"),
        "findings_count": len(report.get("findings") or []),
    }


def audit(report_dir, provider=None):
    plan_path = os.path.join(report_dir, "plan.json")
    if not os.path.exists(plan_path):
        raise FileNotFoundError(f"missing plan.json: {plan_path}")
    plan = _load_json(plan_path)
    coverage = classify(plan, provider=provider)
    coverage["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    coverage["schema_watch_status"] = _latest_schema_watch_report()
    coverage["summary"] = {k: len(v) for k, v in coverage.items()
                           if isinstance(v, list)}
    out_path = os.path.join(report_dir, "bcm-coverage.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(coverage, f, indent=2)
        f.write("\n")
    return coverage


def _print_human(coverage):
    print("=" * 60)
    print("COST COVERAGE AUDIT")
    print("=" * 60)
    provider = coverage.get("provider") or {}
    if provider.get("status") and provider["status"] != "implemented":
        print(f"[coverage_audit] NOTE: pricing discovery is not implemented for "
              f"'{provider.get('cloud')}' yet (status: {provider['status']}) — every resource "
              "type below is unresolved for that reason, not because it was individually "
              "checked and found unmapped.", file=sys.stderr)
    for key, label in (
        ("auto_priced", "Auto-priced"),
        ("catalog_mapped_needs_usage", "Mapped, needs reviewed usage profile"),
        ("confirmed_free", "Confirmed free"),
        ("unresolved", "UNRESOLVED (no serviceCode mapping at all)"),
    ):
        rows = coverage[key]
        print(f"\n{label} ({len(rows)}):")
        for row in rows:
            extra = row.get("service", row.get("reason", ""))
            print(f"  - {row['resource_type']} x{row['count']}  [{extra}]")
    if coverage["unresolved"]:
        print("\n[coverage_audit] WARNING: unresolved resource types are NOT in the cost "
              "report at all. Add them to core/cost/pricing_data/aws_resource_map.json (priced) "
              "or core/cost/pricing_data/free_resources.json (confirmed free) after checking the "
              "AWS Price List catalog — never guess.", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Cost-coverage audit (never prices, only classifies)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit", help="classify every resource type in a report's plan.json")
    a.add_argument("--report-dir", required=True)
    a.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "audit":
        coverage = audit(args.report_dir)
        if args.json:
            print(json.dumps(coverage, indent=2))
        else:
            _print_human(coverage)
        return 1 if coverage["unresolved"] else 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
