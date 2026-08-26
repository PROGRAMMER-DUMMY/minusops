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
import os
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


def aws(*args, check=False, env=None):
    """Returns (returncode, stdout, stderr). Never raises on a non-zero exit.

    `env` overlays credential variables onto the environment for this call only.
    """
    done = subprocess.run(["aws", *args], capture_output=True, text=True,
                          env={**os.environ, **env} if env else None)
    if check and done.returncode != 0:
        raise SystemExit(f"aws {' '.join(args)} failed:\n{done.stderr.strip()}")
    return done.returncode, done.stdout, done.stderr


def identity(env=None):
    _rc, out, _err = aws("sts", "get-caller-identity", "--output", "json", check=True, env=env)
    return json.loads(out)


def mfa_serial():
    """The calling user's first enrolled MFA device, or None."""
    rc, out, _err = aws("iam", "list-mfa-devices", "--output", "json")
    devices = json.loads(out).get("MFADevices", []) if rc == 0 else []
    return devices[0]["SerialNumber"] if devices else None


def elevate(serial, code):
    """Exchange a TOTP code for MFA-carrying credentials. Returns an env overlay."""
    rc, out, err = aws("sts", "get-session-token", "--serial-number", serial,
                       "--token-code", code, "--output", "json")
    if rc != 0:
        raise SystemExit(f"[mfa] get-session-token failed:\n{err.strip()}")
    creds = json.loads(out)["Credentials"]
    return {"AWS_ACCESS_KEY_ID": creds["AccessKeyId"],
            "AWS_SECRET_ACCESS_KEY": creds["SecretAccessKey"],
            "AWS_SESSION_TOKEN": creds["SessionToken"]}


def _creds_env(assume_role_output):
    creds = json.loads(assume_role_output)["Credentials"]
    return {"AWS_ACCESS_KEY_ID": creds["AccessKeyId"],
            "AWS_SECRET_ACCESS_KEY": creds["SecretAccessKey"],
            "AWS_SESSION_TOKEN": creds["SessionToken"]}


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


def chain_probe(arn, session_env):
    """Does the MFA flag survive role chaining? Re-assumes the role from a session that was
    itself reached with MFA."""
    print(f"[chain] re-assuming {TEST_ROLE} from the assumed-role session ...")
    rc, _out, err = aws("sts", "assume-role", "--role-arn", arn,
                        "--role-session-name", "minusops-mfa-chain", env=session_env)
    if rc == 0:
        return ("PROPAGATES",
                "the second assume succeeded from a session holding no TOTP of its own. The "
                "flag is carried by the credential, so anything running in an MFA-elevated "
                "shell inherits it. MFA at assume time is not per-action consent.")
    if "AccessDenied" in err or "not authorized" in err:
        return ("DOES NOT PROPAGATE",
                "the chained session was denied. The condition re-checks per assume rather "
                "than being inherited, which makes it a stronger control than assumed here.")
    return ("INCONCLUSIVE", f"the chained call failed for another reason:\n{err.strip()}")


def live_probe(account, method, env=None, chain=False):
    trust = json.dumps(TRUST).replace("{account}", account)
    print(f"\n[live] creating {TEST_ROLE} (no permissions attached) ...")
    rc, _out, err = aws("iam", "create-role",
                        "--role-name", TEST_ROLE,
                        "--assume-role-policy-document", trust,
                        "--description", "MinusOps MFA condition probe. Safe to delete.",
                        "--max-session-duration", "3600", env=env)
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
    rc, out, err = aws("sts", "assume-role", "--role-arn", arn,
                       "--role-session-name", "minusops-mfa-probe", env=env)

    try:
        if rc == 0:
            verdict = "SATISFIED"
            meaning = ("this session satisfies aws:MultiFactorAuthPresent, so "
                       "RequireMfaOnApply=true works for it. Every session that has not "
                       "elevated is denied by the same condition.")
            if chain:
                chain_verdict, chain_meaning = chain_probe(arn, _creds_env(out))
                meaning += f"\n\nCHAINING: {chain_verdict}\n{chain_meaning}"
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
        rc, _out, err = aws("iam", "delete-role", "--role-name", TEST_ROLE, env=env)
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
    parser.add_argument("--mfa-code",
                        help="TOTP code. Elevates via sts:GetSessionToken first, so the probe "
                             "runs against an MFA-carrying session.")
    parser.add_argument("--mfa-serial",
                        help="MFA device ARN. Defaults to the calling user's first device.")
    parser.add_argument("--chain", action="store_true",
                        help="Also test whether the flag survives role chaining. Requires "
                             "--live and a session that satisfies the condition.")
    args = parser.parse_args(argv)

    env = None
    serial = None
    if args.mfa_code:
        serial = args.mfa_serial or mfa_serial()
        if not serial:
            print("[mfa] no MFA device enrolled for this user; pass --mfa-serial",
                  file=sys.stderr)
            return 2
        print(f"[mfa] elevating with {serial} ...")
        env = elevate(serial, args.mfa_code)

    who = identity(env)
    arn = who["Arn"]
    method, expectation = classify(arn)

    print("=" * 78)
    print("  MFA condition probe")
    print("=" * 78)
    print(f"  account       {who['Account']}")
    print(f"  identity      {arn}")
    print(f"  sign-in       {method}")
    print(f"  elevated      {'yes, ' + serial if env else 'no'}")
    print(f"  expectation   {expectation}")

    if not args.live:
        print("\n  Read-only pass. Re-run with --live for a definitive answer:")
        print("      python examples/iam/verify-mfa-condition.py --live")
        print("\n  That creates one role with the MFA condition, tries to assume it, and")
        print("  deletes it. No permissions are attached to it.")
        return 0

    verdict, meaning = live_probe(who["Account"], method, env=env, chain=args.chain)
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
