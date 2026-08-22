"""
`minusctl cost {prepare|estimate}` -- the BCM Pricing Calculator workflow by name.

`prepare` builds the usage profile an operator reviews; `estimate` creates the workload
estimate against the live AWS BCM Pricing Calculator API. Naming matters: `estimate` maps to
the engine's `run` stage, and it is the ONLY thing in MinusOps that produces a reportable
cost total. Nothing here computes, interpolates, or defaults a number -- see
core/cost/budget_calculator.py for why that absence is the feature.

Depends on: core/cli/context.py, core/cost/bcm_pricing_calculator.py (lazily, inside
    `_delegate`)
Shells out to: nothing directly; the engine calls the `aws` CLI (read-only pricing APIs)
Used by: core/cli/main.py
"""
from .. import context as cli_context

# CLI verb -> engine stage. `estimate` reads better than `run` at the call site and `run`
# already means something else in this CLI.
_STAGES = {"prepare": "prepare", "estimate": "run", "scenario": "scenario",
           "actuals": "actuals", "scale-curve": "scale-curve"}


def add_parser(sub):
    parser = sub.add_parser("cost", help="AWS BCM Pricing Calculator: prepare, estimate")
    parser.add_argument("action", choices=sorted(_STAGES))
    parser.add_argument("--run", help="run id (defaults to the active run)")
    parser.add_argument("--report-dir", help="defaults to the active run's reports/")
    parser.add_argument("--account-id", default="")
    parser.add_argument("--mode", default="auto-approve",
                        choices=["gatekeeper", "auto-approve"])
    return parser


def _delegate(argv):
    """Seam: the single point where control leaves this module for the pricing engine."""
    import bcm_pricing_calculator
    return bcm_pricing_calculator.main(argv)


def run(args):
    report_dir = args.report_dir
    if not report_dir:
        try:
            report_dir = cli_context.resolve_run(args.run)["reports_dir"]
        except cli_context.ContextError as exc:
            print(f"[ERR] {exc}")
            return 1

    argv = [_STAGES[args.action], "--report-dir", report_dir]
    if args.action == "prepare" and args.account_id:
        argv += ["--account-id", args.account_id]
    if args.action != "prepare":
        argv += ["--mode", args.mode]
    return _delegate(argv)
