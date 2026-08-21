"""
Cost guidance — reportable totals come only from the AWS BCM Pricing Calculator API.

This module deliberately does NOT compute, estimate, or hardcode cost totals, and it does not
invent SKU prices. That absence is the feature: the dispatcher's BUDGET intent has to answer
something, and the honest answer is a pointer plus the exact commands, not a number this
process made up. Reportable enterprise cost evidence is produced solely by
core/cost/bcm_pricing_calculator.py against the AWS BCM Pricing Calculator API. Anyone
"finishing" this file by adding a rate table or an arithmetic estimate has reintroduced the
fabricated total it exists to refuse.

Depends on: nothing (stdlib only — argparse/json/os/sys). BCM_COMMANDS names
    core/cost/bcm_pricing_calculator.py as text; it is never imported or invoked.
Shells out to: nothing. It prints aws-CLI-backed commands for an operator to run; it runs none
    of them, so this module makes no AWS call and costs nothing.
Used by: tests/test_budget_calculator.py. No in-repo module imports it — it is reached as a CLI
    for the dispatcher's BUDGET intent, and writes .agents/logs/budget_estimation.json.
"""
import argparse
import json
import os
import sys

BCM_COMMANDS = [
    "python core/cost/bcm_pricing_calculator.py prepare --report-dir <report-dir> --account-id <account-id>",
    "# review bcm-usage.json (resolve REVIEW_REQUIRED) or pass --usage-profile examples/bcm-usage-profile.example.json",
    "python core/cost/bcm_pricing_calculator.py run --report-dir <report-dir> --mode gatekeeper",
]


def cost_guidance():
    """Honest cost record: no total, BCM API required, with the exact commands."""
    return {
        "reportable": False,
        "pricing_source": "AWS BCM Pricing Calculator API required for reportable totals",
        "bcm_pricing_calculator_required": True,
        "note": (
            "MinusOps does not compute or hardcode cost totals. Generate reportable cost "
            "evidence via the gated AWS BCM Pricing Calculator workflow below."
        ),
        "commands": BCM_COMMANDS,
    }


def unit_economics(total_usd=None, source=None, gb_processed=None, runs=None):
    """
    Derive unit economics ratios from an evidenced AWS BCM total.

    Refuses without an evidenced total and explicit source provenance, preserving
    the doctrine that MinusOps never fabricates cost figures.
    """
    if total_usd is None or not source:
        return {
            "reportable": False,
            "source": source,
            "cost_per_gb": None,
            "cost_per_run": None,
            "note": "Unit economics require evidenced BCM cost totals.",
            "commands": BCM_COMMANDS,
        }

    cost_per_gb = None
    if gb_processed is not None and gb_processed > 0:
        cost_per_gb = round(total_usd / gb_processed, 4)

    cost_per_run = None
    if runs is not None and runs > 0:
        cost_per_run = round(total_usd / runs, 4)

    return {
        "reportable": True,
        "source": source,
        "total_usd": total_usd,
        "cost_per_gb": cost_per_gb,
        "cost_per_run": cost_per_run,
    }


def unit_economics_curve(points, source=None):
    """
    Derive unit economics ratios for a multi-point scale curve priced by BCM.
    Never extrapolates beyond measured points.
    """
    curve = []
    for pt in points or []:
        ratio = unit_economics(
            total_usd=pt.get("total_usd"),
            source=source,
            gb_processed=pt.get("gb_processed"),
            runs=pt.get("runs"),
        )
        ratio["factor"] = pt.get("factor", 1)
        curve.append(ratio)
    return curve



def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Cost guidance (reportable totals require the AWS BCM Pricing Calculator API)")
    parser.add_argument("--log-dir", default=os.path.join(os.getcwd(), ".agents", "logs"))
    parser.add_argument("--json", action="store_true")
    # Tolerate legacy sizing flags (--service/--scale/...) so older callers don't crash.
    args, _unknown = parser.parse_known_args(argv)

    record = cost_guidance()
    os.makedirs(args.log_dir, exist_ok=True)
    with open(os.path.join(args.log_dir, "budget_estimation.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)

    if args.json:
        print(json.dumps(record, indent=2))
    else:
        print("=" * 60)
        print("COST GUIDANCE")
        print("=" * 60)
        print(record["note"])
        print("-" * 60)
        print("Reportable totals require AWS BCM Pricing Calculator API evidence:")
        for cmd in record["commands"]:
            print(f"  {cmd}")
        print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
