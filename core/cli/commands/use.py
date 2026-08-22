"""
`minusctl use <run-id>` -- select the run every later command defaults to.

Depends on: core/cli/context.py
Shells out to: nothing
Used by: core/cli/main.py
"""
from .. import context as cli_context


def add_parser(sub):
    parser = sub.add_parser("use", help="select the active run workspace")
    parser.add_argument("run_id", help="run id from `minusctl runs list`")
    return parser


def run(args):
    try:
        record = cli_context.set_active_run(args.run_id)
    except cli_context.ContextError as exc:
        print(f"[ERR] {exc}")
        return 1
    print(f"active run: {record['run_id']}")
    print(f"  terraform: {record['terraform_dir']}")
    print(f"  reports  : {record['reports_dir']}")
    return 0
