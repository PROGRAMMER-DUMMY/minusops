"""`minusctl iam mfa-probe` -- measure whether an MFA trust-policy condition works here.

A thin front for core/governance/mfa_probe.py. The answer decides RequireMfaOnApply in
examples/iam/onboarding-template.yaml, and it depends on the operator's sign-in method
rather than on anything this repository can determine.

The command creates and deletes IAM objects, which no other minusctl command does. It is
scoped to one role named `minusops-mfa-probe`, it is the only thing this module can touch,
and nothing here reads or writes gate state.

Depends on: core/governance/mfa_probe.py (imported lazily inside `_delegate`)
Shells out to: nothing directly; mfa_probe runs the `aws` CLI
Used by: core/cli/main.py
"""
import os
import sys

ACTIONS = ("mfa-probe",)


def add_parser(sub):
    parser = sub.add_parser("iam", help="IAM checks: mfa-probe")
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--live", action="store_true",
                        help="Create and delete one throwaway role to get a definitive "
                             "answer, instead of only reporting the sign-in method.")
    parser.add_argument("--chain", action="store_true",
                        help="Also test whether the MFA flag survives role chaining. "
                             "Requires --live and a session that satisfies the condition.")
    parser.add_argument("--mfa-code",
                        help="TOTP code. Elevates via sts:GetSessionToken first, so the "
                             "probe runs against an MFA-carrying session.")
    parser.add_argument("--mfa-serial",
                        help="MFA device ARN. Defaults to the calling user's first device.")
    return parser


def _delegate(argv):
    """Seam: the single point where control leaves this module for the probe."""
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "governance"))
    import mfa_probe
    return mfa_probe.main(argv)


def run(args):
    if args.chain and not args.live:
        print("[iam] --chain needs --live: there is no role to chain through without it.",
              file=sys.stderr)
        return 2

    argv = []
    if args.live:
        argv.append("--live")
    if args.chain:
        argv.append("--chain")
    if args.mfa_code:
        argv += ["--mfa-code", args.mfa_code]
    if args.mfa_serial:
        argv += ["--mfa-serial", args.mfa_serial]
    return _delegate(argv)
