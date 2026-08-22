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

# `status` is a CLI-side read of recorded gate state, not a sixth stage. Forwarding it would
# hand plan_gate a stage it does not implement.
ACTIONS = STAGES + ("status",)


def add_parser(sub):
    parser = sub.add_parser("gate", help="deploy gate: verify, plan, approve, apply, status")
    parser.add_argument("stage", choices=ACTIONS)
    parser.add_argument("--dir", help="Terraform directory (defaults to the active run)")
    parser.add_argument("--run", help="run id (defaults to the active run)")
    parser.add_argument("--mode", default="gatekeeper",
                        choices=["gatekeeper", "auto-approve"])
    parser.add_argument("--policy-mode", choices=["dev", "production"])
    parser.add_argument("--destroy", action="store_true",
                        help="plan a teardown; governed exactly like create/modify")
    parser.add_argument("--role-arn",
                        help="assert the active session is this deploy role; `approve` "
                             "refuses if it is not")
    parser.add_argument("--with-telemetry", action="store_true",
                        help="correlate detected drift with CloudTrail identity and Glue "
                             "failure signatures (read-only, advisory, off by default)")
    return parser


def _delegate(argv):
    """Seam: the single point where control leaves this module for the gate engine."""
    import plan_gate
    return plan_gate.main(argv)


def _status(tf_dir):
    """Seam, for the same reason `_delegate` is one. Reads recorded state from disk; never
    invokes terraform, so `gate status` stays instant and credential-free."""
    import plan_gate
    return plan_gate.gate_status(tf_dir), plan_gate


def run(args):
    tf_dir = args.dir
    if not tf_dir:
        try:
            tf_dir = cli_context.resolve_run(args.run)["terraform_dir"]
        except cli_context.ContextError as exc:
            print(f"[ERR] {exc}")
            return 1

    if args.stage == "status":
        status, engine = _status(tf_dir)
        print(engine.format_status(status))
        return 0

    argv = [args.stage, "--dir", tf_dir, "--mode", args.mode]
    if args.policy_mode:
        argv += ["--policy-mode", args.policy_mode]
    if args.destroy:
        argv.append("--destroy")
    if getattr(args, "with_telemetry", False):
        argv.append("--with-telemetry")
    if getattr(args, "role_arn", None):
        argv += ["--role-arn", args.role_arn]
    return _delegate(argv)
