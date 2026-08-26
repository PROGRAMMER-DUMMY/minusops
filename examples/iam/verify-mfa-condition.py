"""Kept so the path documented in earlier guides keeps working.

The probe now lives in core/governance/mfa_probe.py and is reached as
`minusctl iam mfa-probe`. This forwards to it unchanged.

Depends on: core/governance/mfa_probe.py
Shells out to: nothing directly
Used by: nothing (compatibility entry point)
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "core", "governance"))

import mfa_probe  # noqa: E402

if __name__ == "__main__":
    print("[note] this path is kept for compatibility; use `minusctl iam mfa-probe`.",
          file=sys.stderr)
    sys.exit(mfa_probe.main())
