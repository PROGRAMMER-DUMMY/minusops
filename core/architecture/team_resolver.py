"""
Team directory resolver (MINUS-153).

`--owner` used to be a free-text string that landed in a tag and nowhere else. Once state keys
and deploy roles are scoped per team, that string starts deciding where state lives and which
role may apply it -- so it needs a canonical form and, optionally, a directory behind it.

**The directory is optional and always has been.** With no `configs/teams.yaml`, `resolve()`
returns a team carrying just the id, and everything downstream behaves exactly as before. A
missing config is a machine that has not opted in, not an error: making it fatal would break
every existing run and every fresh clone for a feature nobody asked for yet.

What the id reaches, and why it is validated:
  - the state key      teams/<team_id>/<workload>/terraform.tfstate
  - the deploy role    minusops-deploy-<team_id>

Both are security boundaries. An id containing `/` or `..` would walk out of its own state
prefix, and one containing a wildcard would widen the role pattern it is substituted into, so
the charset is enforced here rather than trusted from a config file or a CLI flag.
"""
import os
import re
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_CORE_DIR)

CONFIG_ENV = "MINUS_TEAMS_CONFIG"
DEFAULT_CONFIG = os.path.join(_REPO_ROOT, "configs", "teams.yaml")
EXAMPLE_CONFIG = os.path.join(_REPO_ROOT, "configs", "teams.yaml.example")

# Lowercase, digits, hyphens. Not a style preference: this string is interpolated into an S3
# key prefix and an IAM role ARN, so `.` and `/` and `*` are the characters that would let a
# team id escape its own path or widen a role pattern.
_TEAM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

DEFAULT_ROLE_TEMPLATE = "arn:aws:iam::*:role/minusops-deploy-{team_id}"

_KNOWN_FIELDS = ("name", "lead_email", "team_dl", "slack_channel", "teams_webhook_secret",
                 "cost_center", "deploy_role_pattern")


class InvalidTeamId(ValueError):
    """The id cannot be used in a state key or a role ARN."""


def config_path():
    """The directory file this process would read, whether or not it exists."""
    return os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG


def validate_team_id(team_id):
    team_id = (team_id or "").strip()
    if not _TEAM_ID_RE.match(team_id):
        raise InvalidTeamId(
            f"team id {team_id!r} must be lowercase letters, digits and hyphens (max 63). "
            "It becomes an S3 key prefix and an IAM role name, so slashes, dots and "
            "wildcards are refused rather than escaped.")
    return team_id


def load_directory(path=None):
    """{team_id: {...}} from the YAML directory, or {} when there is no usable file.

    Never raises for a missing file or missing PyYAML -- both mean "not configured", and the
    caller's job is to keep working without a directory. A malformed file DOES raise: that is
    a config someone wrote and got wrong, and silently ignoring it would hide the mistake
    behind behaviour that looks like "no teams configured".
    """
    path = path or config_path()
    if not os.path.exists(path):
        return {}
    try:
        import yaml
    except ImportError:
        print(f"[teams] PyYAML not installed; ignoring {path}", file=sys.stderr)
        return {}
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    teams = data.get("teams")
    if teams is None:
        return {}
    if not isinstance(teams, dict):
        raise ValueError(f"{path}: `teams` must be a mapping of team_id -> fields")
    return {str(k): (v or {}) for k, v in teams.items()}


def resolve(team_id, path=None):
    """Resolve one owner into a team record.

    Returns {"team_id", "configured", "source", <known fields>}. `configured` is False when
    no directory names this team -- the caller still gets a usable record, and can decide
    whether the absence matters. Nothing here refuses to resolve an unknown team: a squad
    that has not been added to the directory yet must still be able to plan.
    """
    team_id = validate_team_id(team_id)
    path = path or config_path()
    directory = load_directory(path)
    entry = directory.get(team_id)

    record = {"team_id": team_id, "configured": entry is not None,
              "source": path if entry is not None else None}
    for field in _KNOWN_FIELDS:
        record[field] = (entry or {}).get(field)
    if not record["deploy_role_pattern"]:
        record["deploy_role_pattern"] = DEFAULT_ROLE_TEMPLATE.format(team_id=team_id)
    return record


def state_key(team_id, workload_id):
    """`teams/<team_id>/<workload_id>/terraform.tfstate` (MINUS-141).

    Both segments are validated, not just the team: a workload id is equally operator-supplied
    and lands in the same key, so a traversal there escapes the team prefix just as effectively.
    """
    team_id = validate_team_id(team_id)
    workload_id = validate_team_id(workload_id)
    return f"teams/{team_id}/{workload_id}/terraform.tfstate"


def role_matches(arn, pattern):
    """Does an active session ARN satisfy a team's deploy-role pattern?

    Only `*` is a wildcard, and it does NOT cross a `:` -- an account-id wildcard must not
    also swallow the role name. Every other regex metacharacter in the pattern is escaped, so
    a `.` in a pattern matches a literal dot rather than any character.
    """
    if not arn or not pattern:
        return False
    regex = "".join(r"[^:]*" if part == "*" else re.escape(part)
                    for part in re.split(r"(\*)", pattern))
    if re.fullmatch(regex, arn):
        return True
    # An assumed-role session ARN is sts::assumed-role/<RoleName>/<session>, not the iam::role
    # ARN the pattern names. Compare the role NAME too, or every real assumed session fails.
    assumed = re.match(r"^arn:aws[a-z-]*:sts::(\d+):assumed-role/([^/]+)/", arn)
    if assumed:
        rebuilt = f"arn:aws:iam::{assumed.group(1)}:role/{assumed.group(2)}"
        return bool(re.fullmatch(regex, rebuilt))
    return False


def list_teams(path=None):
    """Ids in the directory, sorted. Empty when unconfigured."""
    return sorted(load_directory(path))


def main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Resolve an owner against the team directory.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("resolve")
    r.add_argument("team_id")
    k = sub.add_parser("state-key")
    k.add_argument("team_id")
    k.add_argument("workload_id")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        teams = list_teams()
        if not teams:
            print(f"no team directory at {config_path()} "
                  f"(copy {os.path.relpath(EXAMPLE_CONFIG, _REPO_ROOT)} to opt in)")
            return 0
        for team_id in teams:
            print(team_id)
        return 0
    if args.cmd == "resolve":
        print(json.dumps(resolve(args.team_id), indent=2))
        return 0
    print(state_key(args.team_id, args.workload_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
