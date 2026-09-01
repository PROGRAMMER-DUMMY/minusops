"""
`minusctl pattern` -- approved architecture-pattern registry.
"""

def add_parser(sub):
    parser = sub.add_parser("pattern", help="Approved architecture-pattern registry: list, match, capture.")
    subparsers = parser.add_subparsers(dest="cmd", required=True)
    subparsers.add_parser("list", help="List stored approved patterns")
    m = subparsers.add_parser("match", help="Match requirements to approved patterns")
    m.add_argument("requirements", help="Requirements string to match against")
    c = subparsers.add_parser("capture", help="Capture an approved composition")
    c.add_argument("requirements")
    c.add_argument("--module", action="append", default=[], required=True)
    c.add_argument("--name", default=None)
    c.add_argument("--plan-hash", default=None)
    c.add_argument("--approver", default=None)
    p = subparsers.add_parser("promote", help="Promote an approved, proven architecture run via Git PR")
    p.add_argument("--name", required=True, help="Pattern identifier name")
    p.add_argument("--run", help="Target run workspace (defaults to active run context)")
    p.add_argument("--description", default="", help="Pattern business rationale")
    p.add_argument("--skip-proof", action="store_true", help="Bypass UAT proving verification")
    return parser

def run(args):
    import patterns
    import os
    import core.cli.context as context
    argv = [args.cmd]
    if args.cmd == "match":
        argv.append(args.requirements)
    elif args.cmd == "capture":
        argv.append(args.requirements)
        for m in args.module:
            argv.extend(["--module", m])
        if args.name:
            argv.extend(["--name", args.name])
        if args.plan_hash:
            argv.extend(["--plan-hash", args.plan_hash])
        if args.approver:
            argv.extend(["--approver", args.approver])
    elif args.cmd == "promote":
        argv.extend(["--name", args.name])
        if args.description:
            argv.extend(["--description", args.description])
        if args.skip_proof:
            argv.append("--skip-proof")
        run_root = None
        if args.run:
            run_root = os.path.join("runs", args.run)
        else:
            try:
                active_id = context.active_run_id()
                if not active_id:
                    import runs as runs_mod
                    lr = runs_mod.latest_run()
                    if lr:
                        active_id = lr.get("run_id")
                if active_id:
                    run_root = os.path.join("runs", active_id)
            except Exception:
                pass
        if run_root:
            argv.extend(["--run-root", run_root])
    return patterns.main(argv)
