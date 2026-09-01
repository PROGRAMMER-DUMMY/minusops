"""
`minusctl derive` -- evaluate what stated architectural facts imply.
"""

def add_parser(sub):
    parser = sub.add_parser("derive", help="Evaluate worker sizing, partitioning, and compute engine from facts.")
    parser.add_argument("fact", nargs="*", help="key=value facts, e.g. daily_gb=100 partitions_per_day=24")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output")
    return parser

def run(args):
    import pillars
    argv = ["derive"]
    if args.json:
        argv.append("--json")
    if args.fact:
        argv.extend(args.fact)
    return pillars.main(argv)
