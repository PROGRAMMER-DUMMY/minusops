"""
One module per `minusctl` subcommand.

Each module exposes `add_parser(subparsers)` and `run(args, ctx)`. Modules that front an
existing engine keep a `_delegate(argv)` seam so a test can assert what was passed through
without reaching AWS or Terraform.

Depends on: core/cli/context.py, core/cli/formatters.py, and the engine each command fronts
Shells out to: nothing directly
Used by: core/cli/main.py
"""
