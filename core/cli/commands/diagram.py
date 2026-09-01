"""
`minusctl diagram` -- the Draw.io architecture canvas for a run's Terraform plan.

Resolves a plan from an explicit directory, a named run, or the active context, then writes
the diagram XML, the 1-click diagrams.net URL, and the declared-hop ledger. Every artifact
is derived from `plan.json`; nothing here reaches AWS or runs Terraform.

Depends on: core/cli/context.py, core/reporting/drawio_generator.py (imported lazily inside
    `_generate`, so importing this module offline stays free of side effects)
Shells out to: nothing
Used by: core/cli/main.py
"""
import json
import os

from .. import context as cli_context

_ARTIFACTS = {
    "drawio": ("architecture.drawio", "xml"),
    "url": ("architecture_url.txt", "url"),
    "ledger": ("architecture_ledger.md", "ledger_markdown"),
}


def add_parser(subparsers):
    parser = subparsers.add_parser(
        "diagram", help="Generate Draw.io architecture diagrams from Terraform plan")
    parser.add_argument("--run", help="Target run workspace (defaults to active context)")
    parser.add_argument("--dir", help="Explicit Terraform directory")
    parser.add_argument("--format", choices=["all", "drawio", "url", "ledger"], default="all",
                        help="Output artifacts. This engine emits Draw.io XML; "
                             "reporter.py renders architecture.svg")
    parser.add_argument("--out-dir", help="Target output directory")
    parser.add_argument("--json", action="store_true",
                        help="Structured JSON output with file paths and the 1-click URL")
    parser.add_argument("--check", action="store_true",
                        help="Verify the generated canvas -- dangling edges, escaped "
                             "containment, overlapping cells -- and exit non-zero on FAIL")
    parser.set_defaults(func=run)


def _generate(plan_json, requirements=None):
    from ...reporting import drawio_generator
    return drawio_generator.generate_drawio_from_plan(plan_json, requirements=requirements)


def _check(xml_text):
    from ...reporting import diagram_check
    return diagram_check.check(xml_text), diagram_check.format_report


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _find_plan(root):
    """Locate a plan.json for a run root, preferring the most recent report bundle."""
    if not root:
        return {}

    for candidate in (os.path.join(root, "plan.json"),
                      os.path.join(root, "reports", "plan.json")):
        plan = _load(candidate) if os.path.isfile(candidate) else None
        if plan is not None:
            return plan

    reports = os.path.join(root, "reports")
    search = reports if os.path.isdir(reports) else root
    if not os.path.isdir(search):
        return {}

    bundles = [os.path.join(search, name) for name in os.listdir(search)
               if os.path.isdir(os.path.join(search, name))]
    for bundle in sorted(bundles, key=os.path.getmtime, reverse=True):
        candidate = os.path.join(bundle, "plan.json")
        plan = _load(candidate) if os.path.isfile(candidate) else None
        if plan is not None:
            return plan
    return {}


def _find_requirements(root):
    """The interview record for this run, when there is one.

    It supplies only what a plan cannot state -- who sends data in from outside the account.
    Its absence is not an error: the canvas then draws no external sender rather than a
    generic box captioned "Source".
    """
    if not root:
        return None
    for candidate in (os.path.join(root, "requirements.json"),
                      os.path.join(root, "reports", "requirements.json")):
        if os.path.isfile(candidate):
            return _load(candidate)
    return None


def _resolve_root(args):
    if args.dir:
        return args.dir
    if args.run:
        return os.path.join("runs", args.run)
    try:
        active = cli_context.active_run()
        if isinstance(active, dict):
            active = active.get("run_id") or active.get("run")
    except Exception:
        return None
    return os.path.join("runs", str(active)) if active else None


def run(args):
    root = _resolve_root(args)
    result = _generate(_find_plan(root), _find_requirements(root))

    out_dir = args.out_dir or "."
    os.makedirs(out_dir, exist_ok=True)

    written = {}
    for name, (filename, key) in _ARTIFACTS.items():
        if args.format not in ("all", name):
            continue
        path = os.path.join(out_dir, filename)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(result[key] if key != "ledger_markdown"
                         else result[key] + "\n")
        written[name] = path

    verdict = None
    if args.check:
        verdict, render = _check(result["xml"])

    if args.json:
        payload = {"url": result["url"], "ledger": result["ledger"], "files": written}
        if verdict:
            payload["check"] = verdict
        print(json.dumps(payload))
    else:
        print("Generated Diagram URL:")
        print(result["url"])
        if verdict:
            print()
            print(render(verdict))

    return 1 if verdict and verdict["verdict"] == "FAIL" else 0
