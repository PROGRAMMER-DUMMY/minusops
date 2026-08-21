"""
Render the PR reviewer's sticky comment (MINUS-144).

A pure renderer: it reads artifacts the action already produced and returns Markdown. No
network, no AWS, no git. That is what makes it testable, and it is also the point -- a comment
builder that can reach AWS is a comment builder that can be wrong about what it saw.

The one rule this file exists to enforce: **never invent a number**. Every section either
reports a value it read from a file, or says the evidence is absent and prints the command that
would produce it. A plausible-looking figure in a PR comment gets believed, and a wrong one is
worse than a blank.
"""
import argparse
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CORE = os.path.join(_REPO, "core")
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE, _sub))
sys.path.insert(0, _CORE)

# The nested-total lesson lives in the reflector; importing it means this renderer cannot
# drift from the gate's own reading of the same file. `create.totalCost` is the estimate
# BEFORE usage lines attach and reads 0.0 for every stack -- reporting that as the cost would
# be a $0 comment on a $430/mo plan.
try:
    from reflector import _estimate_total
except ImportError:  # running outside the repo layout
    def _estimate_total(doc):
        if not isinstance(doc, dict):
            return None
        for key in ("estimate", "create"):
            block = doc.get(key)
            if isinstance(block, dict) and block.get("totalCost") is not None:
                return float(block["totalCost"])
        return None

MARKER = "<!-- minusops-pr-reviewer -->"

_BADGE = {"pass": "PASS", "blocked": "BLOCKED", "unknown": "UNKNOWN"}


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return None


def _read_text(path, limit=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except (OSError, TypeError):
        return []
    return lines[-limit:] if limit else lines


def _fence(lines, empty="(no output)"):
    body = "\n".join(lines).strip() or empty
    return "```\n" + body + "\n```"


def gate_section(verify_lines, plan_lines):
    out = ["### Gate", _fence(verify_lines[-25:])]
    # The add/change/destroy line is what a reviewer looks for first, so it is lifted out of
    # the log rather than left for them to find.
    summary = [l for l in plan_lines if l.strip().startswith("Plan:") or "No changes" in l]
    out += ["", "### Planned changes", _fence(summary or plan_lines[-15:])]
    return out


def reflector_section(result):
    """The 5-gate badge. `unknown` is rendered distinctly from `pass` on purpose: a gate that
    could not run has not approved anything."""
    if not result:
        return ["### Reflector", "",
                "**not run** -- no reflector output for this plan.", "",
                "```", "python core/governance/reflector.py --run-root runs/<run-id>", "```"]
    verdict = "BLOCKED" if result.get("blocked") else "PASS"
    counts = result.get("summary") or {}
    head = (f"### Reflector: **{verdict}**  "
            f"({counts.get('pass', 0)} pass / {counts.get('blocked', 0)} blocked / "
            f"{counts.get('unknown', 0)} unknown)")
    rows = ["", "| Gate | Status | Detail |", "| :--- | :--- | :--- |"]
    for gate in result.get("gates") or []:
        detail = str(gate.get("detail", "")).replace("|", "\\|")
        rows.append(f"| `{gate.get('gate')}` | {_BADGE.get(gate.get('status'), '?')} "
                    f"| {detail} |")
    if counts.get("unknown"):
        rows += ["", "> `unknown` is not a pass -- those gates could not run."]
    return [head] + rows


def cost_section(estimate, budget_usd=None):
    total = _estimate_total(estimate)
    if total is None:
        return ["### Cost", "",
                "**unavailable** -- no AWS BCM Pricing Calculator evidence for this plan.", "",
                "```",
                "python core/cost/bcm_pricing_calculator.py prepare --report-dir <report>",
                "```"]
    lines = ["### Cost", "", "| | Monthly |", "| :--- | ---: |",
             f"| BCM forecast (BEFORE_DISCOUNTS) | **${total:,.2f}** |"]
    if budget_usd:
        delta = total - budget_usd
        verdict = "OVER" if delta > 0 else "under"
        lines.append(f"| Stated ceiling | ${budget_usd:,.2f} |")
        sign = "+" if delta > 0 else "-"
        lines.append(f"| Delta | **{sign}${abs(delta):,.2f}** ({verdict}) |")
    items = (estimate.get("batch_create_usage") or {}).get("items") or []
    if items:
        lines += ["", "<details><summary>Priced usage lines</summary>", "",
                  "| Cost | Service | Usage |", "| ---: | :--- | :--- |"]
        for item in sorted(items, key=lambda i: -(i.get("cost") or 0))[:12]:
            qty = item.get("quantity") or {}
            lines.append(f"| ${item.get('cost') or 0:,.2f} | {item.get('serviceCode', '?')} "
                         f"| {qty.get('amount', '')} {qty.get('unit', '')} |")
        lines += ["", "</details>"]
    return lines


def architecture_section(svg_path):
    if not svg_path or not os.path.exists(svg_path):
        return []
    return ["### Architecture", "",
            f"`{os.path.relpath(svg_path, _REPO)}` is attached to this workflow run's "
            "artifacts.", "",
            "> GitHub does not render SVG inside comments; download it from the run to get "
            "click-to-code topology."]


def render(verify_log=None, plan_log=None, reflector_json=None, bcm_json=None,
           plan_hash=None, budget_usd=None, svg_path=None, tf_dir=""):
    reflector = _read_json(reflector_json)
    estimate = _read_json(bcm_json)
    blocked = bool(reflector and reflector.get("blocked"))

    parts = [MARKER,
             f"## MinusOps review: {'BLOCKED' if blocked else 'passed'}",
             "", f"`{tf_dir}`" if tf_dir else "", ""]
    parts += gate_section(_read_text(verify_log), _read_text(plan_log))
    parts += [""] + reflector_section(reflector)
    parts += [""] + cost_section(estimate, budget_usd)
    section = architecture_section(svg_path)
    if section:
        parts += [""] + section

    parts += ["", "### Sign-off", ""]
    if plan_hash:
        # The full digest, not a prefix: this is what a reviewer signs off on and what the
        # merge gate re-computes, so a truncated value here would be unusable for either.
        parts += [f"Plan hash `{plan_hash}`", "",
                  "> Approving this PR signs off on **that exact hash**. The merge gate "
                  "recomputes it and refuses to apply if it has changed."]
    else:
        parts.append("No plan hash recorded -- nothing here is bound to a reviewable plan.")

    parts += ["", "---",
              "<sub>MinusOps PR reviewer. This is a plan, not an apply; applying still "
              "requires the environment gate and a hash-bound approval.</sub>"]
    return "\n".join(p for p in parts if p is not None)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render the MinusOps PR review comment.")
    ap.add_argument("--verify-log")
    ap.add_argument("--plan-log")
    ap.add_argument("--reflector-json")
    ap.add_argument("--bcm-json")
    ap.add_argument("--plan-hash")
    ap.add_argument("--budget-usd", type=float, default=None)
    ap.add_argument("--svg")
    ap.add_argument("--tf-dir", default="")
    ap.add_argument("--out", default="-")
    args = ap.parse_args(argv)

    body = render(verify_log=args.verify_log, plan_log=args.plan_log,
                  reflector_json=args.reflector_json, bcm_json=args.bcm_json,
                  plan_hash=args.plan_hash, budget_usd=args.budget_usd,
                  svg_path=args.svg, tf_dir=args.tf_dir)
    if args.out == "-":
        print(body)
    else:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(body + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
