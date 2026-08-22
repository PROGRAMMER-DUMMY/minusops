"""
`minusctl gate {verify|plan|approve|apply}` -- the deploy gate, reached by name instead of by
script path.

This is a thin front for core/governance/plan_gate.py and it must stay thin. The gate's
stages ARE the governance contract: the plan hash binding, the destructive-change classifier
and the fail-closed policy checks all live there, and a wrapper that reordered, renamed or
short-circuited a stage would change what is enforced while looking like a usability change.
So the stage is passed through verbatim and the only thing added is `--dir`, resolved from
the active run when the operator did not type it.

`--dir` is never guessed. With no active run and no flag this refuses, because the
alternative -- defaulting to the newest run -- plans against infrastructure nobody named.

Depends on: core/cli/context.py, core/governance/plan_gate.py (imported lazily inside
    `_delegate`, so importing this module offline stays free of side effects)
Shells out to: nothing directly; plan_gate runs `terraform` and the `aws` CLI
Used by: core/cli/main.py
"""
from .. import context as cli_context

STAGES = ("verify", "plan", "approve", "apply", "run")


def add_parser(sub):
    parser = sub.add_parser("gate", help="deploy gate: verify, plan, approve, apply")
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--dir", help="Terraform directory (defaults to the active run)")
    parser.add_argument("--run", help="run id (defaults to the active run)")
    parser.add_argument("--mode", default="gatekeeper",
                        choices=["gatekeeper", "auto-approve"])
    parser.add_argument("--policy-mode", choices=["dev", "production"])
    parser.add_argument("--destroy", action="store_true",
                        help="plan a teardown; governed exactly like create/modify")
    return parser


def _delegate(argv):
    """Seam: the single point where control leaves this module for the gate engine."""
    import plan_gate
    return plan_gate.main(argv)


def run(args):
    tf_dir = args.dir
    if not tf_dir:
        try:
            tf_dir = cli_context.resolve_run(args.run)["terraform_dir"]
        except cli_context.ContextError as exc:
            print(f"[ERR] {exc}")
            return 1

    argv = [args.stage, "--dir", tf_dir, "--mode", args.mode]
    if args.policy_mode:
        argv += ["--policy-mode", args.policy_mode]
    if args.destroy:
        argv.append("--destroy")
    return _delegate(argv)
