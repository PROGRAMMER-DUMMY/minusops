"""
`minusctl author` -- submit agent-authored HCL for a novel resource.
"""

def add_parser(sub):
    parser = sub.add_parser("author", help="Submit authored HCL for novel resource composition.")
    parser.add_argument("resource_type", help="e.g. aws_s3_bucket")
    parser.add_argument("--file", default=None, help="path to .tf file with HCL or - for stdin")
    parser.add_argument("--content", default=None, help="authored HCL inline")
    parser.add_argument("--run", default=None, help="existing run id")
    parser.add_argument("--decision-file", default=None, help="path to architecture_decision.json")
    parser.add_argument("--allow-incomplete", action="store_true", help="author directly without pre-built decision")
    parser.add_argument("--justification", default=None, help="required with --allow-incomplete")
    parser.add_argument("--requirements-file", default=None)
    parser.add_argument("--owner", default="data-platform")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser

def run(args):
    import synthesizer
    argv = ["author", args.resource_type]
    if args.file:
        argv.extend(["--file", args.file])
    if args.content:
        argv.extend(["--content", args.content])
    if args.run:
        argv.extend(["--run", args.run])
    if args.decision_file:
        argv.extend(["--decision-file", args.decision_file])
    if args.allow_incomplete:
        argv.append("--allow-incomplete")
    if args.justification:
        argv.extend(["--justification", args.justification])
    if args.requirements_file:
        argv.extend(["--requirements-file", args.requirements_file])
    if args.owner:
        argv.extend(["--owner", args.owner])
    if args.json:
        argv.append("--json")
    return synthesizer.main(argv)
