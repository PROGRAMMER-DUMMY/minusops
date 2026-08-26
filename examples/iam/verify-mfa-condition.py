"""Does an MFA condition in a trust policy work with your sign-in method?

Run before setting RequireMfaOnApply=true. `aws:MultiFactorAuthPresent` is absent or false
for IAM Identity Center, SAML and OIDC sessions, and for any session on long-lived access
keys, so a trust policy requiring it denies those operators outright.

    python examples/iam/verify-mfa-condition.py             # read-only: reports sign-in method
    python examples/iam/verify-mfa-condition.py --live      # creates and DELETES one role

The --live pass creates one throwaway role carrying the condition, attempts to assume it,
reports the result, and deletes the role. It grants no permissions and touches nothing else.
If it cannot delete the role it says so, with the name to remove.

Depends on: the `aws` CLI
Shells out to: sts get-caller-identity, iam create-role / delete-role, sts assume-role
"""
import argparse
import json
import subprocess
import sys
import time

TEST_ROLE = "minusops-mfa-probe"

TRUST = {
    "Version": "2012-10-17",
    "Statement": [{
        "Sid": "AssumeOnlyWithMfa",
        "Effect": "Allow",
        "Principal": {"AWS": "arn:aws:iam::{account}:root"},
        "Action": "sts:AssumeRole",
        "Condition": {"Bool": {"aws:MultiFactorAuthPresent": "true"}},
    }],
}


def aws(*args, check=False):
    """Returns (returncode, stdout, stderr). Never raises on a non-zero exit."""
    done = subprocess.run(["aws", *args], capture_output=True, text=True)
    if check and done.returncode != 0:
        raise SystemExit(f"aws {' '.join(args)} failed:\n{done.stderr.strip()}")
    return done.returncode, done.stdout, done.stderr


def identity():
    _rc, out, err = aws("sts", "get-caller-identity", "--output", "json", check=True)
    return json.loads(out)


def classify(arn):
    """Which sign-in method, from the ARN shape. This is what decides the answer."""
    if ":user/" in arn:
        return ("IAM user", "MFA can populate, via sts:GetSessionToken with a TOTP or an "
                            "AssumeRole carrying MFA parameters")
    if "assumed-role/AWSReservedSSO" in arn:
        return ("IAM Identity Center (SSO)", "the condition is expected to DENY: STS receives "
                                             "no MFA assertion from the IdP. Documented, not "
                                             "measured -- run --live and report the result")
    if ":assumed-role/" in arn:
        return ("assumed role", "depends on how the ORIGINAL session authenticated -- the flag "
                                "propagates through the chain if it was set")
    if ":root" in arn:
        return ("root user", "do not deploy as root, whatever this test says")
    return ("unrecognised", "check manually")


def live_probe(account, method):
    trust = json.dumps(TRUST).replace("{account}", account)
    print(f"\n[live] creating {TEST_ROLE} (no permissions attached) ...")
    rc, _out, err = aws("iam", "create-role",
                        "--role-name", TEST_ROLE,
                        "--assume-role-policy-document", trust,
                        "--description", "MinusOps MFA condition probe. Safe to delete.",
                        "--max-session-duration", "3600")
    if rc != 0:
        if "EntityAlreadyExists" in err:
            print(f"[live] {TEST_ROLE} already exists -- reusing it.")
        else:
            raise SystemExit(f"[live] could not create the probe role:\n{err.strip()}")

    # IAM is eventually consistent: a role is not immediately assumable after creation.
    print("[live] waiting for IAM to converge ...")
    time.sleep(10)

    arn = f"arn:aws:iam::{account}:role/{TEST_ROLE}"
    print(f"[live] attempting sts:AssumeRole on {arn} ...")
    rc, _out, err = aws("sts", "assume-role", "--role-arn", arn,
                        "--role-session-name", "minusops-mfa-probe")

    try:
        if rc == 0:
            verdict = "SATISFIED"
            meaning = ("your session satisfies aws:MultiFactorAuthPresent. RequireMfaOnApply "
                       "=true will work for you -- but read the note above: the flag "
                       "propagates through role chaining, so an agent in this same shell "
                       "would also satisfy it.")
        elif "AccessDenied" in err or "not authorized" in err:
            verdict = "DENIED"
            meaning = ("this session does NOT satisfy the condition. Leave RequireMfaOnApply "
                       "at false: turning it on locks this session out of the apply role "
                       "entirely.")
            if method == "IAM user":
                # Access keys never carry the flag, so a DENIED here cannot distinguish an
                # unenrolled user from an unelevated session.
                meaning += """

You are an IAM user, so this is the UNELEVATED case and not the SSO one: long-lived
access keys carry no MFA flag at all. If a device is enrolled, elevate and re-run to
get the answer that actually decides the setting:

    aws sts get-session-token --serial-number <mfa-arn> --token-code <code>

then export the three returned values and run --live again. If that says SATISFIED,
true is available to you -- at the cost of every unelevated session, CI included."""
        else:
            verdict = "INCONCLUSIVE"
            meaning = f"the call failed for another reason:\n{err.strip()}"
        return verdict, meaning
    finally:
        print(f"[live] deleting {TEST_ROLE} ...")
        rc, _out, err = aws("iam", "delete-role", "--role-name", TEST_ROLE)
        if rc != 0:
            print(f"\n  !! COULD NOT DELETE {TEST_ROLE}. Remove it by hand:\n"
                  f"     aws iam delete-role --role-name {TEST_ROLE}\n"
                  f"     {err.strip()}", file=sys.stderr)
        else:
            print("[live] deleted.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--live", action="store_true",
                        help="Create and delete one throwaway role to get a definitive "
                             "answer. Grants no permissions; touches nothing else.")
    args = parser.parse_args(argv)

    who = identity()
    arn = who["Arn"]
    method, expectation = classify(arn)

    print("=" * 78)
    print("  MFA condition probe")
    print("=" * 78)
    print(f"  account       {who['Account']}")
    print(f"  identity      {arn}")
    print(f"  sign-in       {method}")
    print(f"  expectation   {expectation}")

    if not args.live:
        print("\n  Read-only pass. Re-run with --live for a definitive answer:")
        print("      python examples/iam/verify-mfa-condition.py --live")
        print("\n  That creates one role with the MFA condition, tries to assume it, and")
        print("  deletes it. No permissions are attached to it.")
        return 0

    verdict, meaning = live_probe(who["Account"], method)
    print()
    print("=" * 78)
    print(f"  VERDICT: {verdict}")
    print("=" * 78)
    for line in meaning.split("\n"):
        print(f"  {line}")
    print()
    print("  Set RequireMfaOnApply in examples/iam/onboarding-template.yaml accordingly.")
    print("  Either answer is useful; the default (false) is correct if this said DENIED.")
    return 0 if verdict != "INCONCLUSIVE" else 2


if __name__ == "__main__":
    sys.exit(main())
