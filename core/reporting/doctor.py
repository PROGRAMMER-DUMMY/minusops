"""
Cross-platform environment diagnostics (`minusctl doctor`).

Replaces `tools/doctor.ps1`, which only ran under Windows PowerShell and so was useless
in CI containers, on macOS, and on Linux — the three places a governed deploy is most
likely to run unattended.

Every check reports one of three statuses:
  ok    — present and usable
  warn  — a feature degrades but the core governed-deploy loop still works
          (rego gate without `opa`, dashboard without `dash`, production policy mode
          without an external scanner)
  error — the core loop cannot run at all (no terraform, no cloud CLI, no credentials)

Discovery goes through `toolpath.find_tool` (which refreshes PATH from the Windows
registry first), and the credential probe goes through the provider abstraction rather
than shelling out to `aws` directly, per AGENTS.md §1.
"""
import argparse
import importlib.util
import time
import re
import socket
import urllib.parse
import json
import os
import platform
import subprocess
import sys

try:
    from core.governance import ephemeral_apply, plan_gate, toolpath
    from core.architecture import team_resolver
    from core.providers.base import get_provider
    from core.reporting.optimize_analyzer import EXTERNAL_SCANNERS
except ImportError:
    _CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
        sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
    sys.path.insert(0, _CORE_DIR)

    import ephemeral_apply  # noqa: E402
    import team_resolver  # noqa: E402
    import plan_gate  # noqa: E402
    import toolpath  # noqa: E402
    from providers.base import get_provider  # noqa: E402
    from optimize_analyzer import EXTERNAL_SCANNERS  # noqa: E402


def _port_open(host, port, timeout=0.4):
    """Is anything accepting TCP on host:port? Short timeout -- doctor is a pre-flight, and a
    stalled probe against a dead emulator must not hold the whole report."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _check(name, status, detail, fix=""):
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def _version(path, args):
    """First line of the tool's own version output, or a short failure note."""
    try:
        result = subprocess.run([path, *args], capture_output=True, text=True, timeout=15)
    except Exception as exc:  # tool exists but won't execute (wrong arch, broken install)
        return f"(version probe failed: {exc})"
    text = (result.stdout or result.stderr or "").strip()
    return text.splitlines()[0] if text else "(no version output)"


_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def _parse_version(text):
    """First dotted version in the tool's own output, as a tuple, or None.

    Deliberately forgiving: `terraform version` prints "Terraform v1.15.7", the AWS CLI prints
    "aws-cli/2.35.11 Python/3.14.5 ...". Taking the FIRST match is what makes both work, and
    is also why the AWS CLI's own version has to be read before Python's.
    """
    match = _VERSION_RE.search(text or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups(default="0"))


def _cli_check(name, tool, version_args, required, fix, min_version=None):
    path = toolpath.find_tool(tool)
    if not path:
        return _check(name, "error" if required else "warn", f"`{tool}` not found on PATH", fix)
    reported = _version(path, version_args)
    detail = f"{reported}  [{path}]"
    if min_version:
        found = _parse_version(reported)
        if found is None:
            # Present and runnable but unreadable version output. Warn rather than error: the
            # tool works, we just cannot prove the floor, and blocking on a parse would be
            # worse than saying so.
            return _check(name, "warn", f"{detail} -- could not read a version number",
                          f"MinusOps needs {tool} >= {'.'.join(str(v) for v in min_version)}.")
        if found[:len(min_version)] < tuple(min_version):
            return _check(name, "error" if required else "warn",
                          f"{detail} -- below the required "
                          f"{'.'.join(str(v) for v in min_version)}", fix)
    return _check(name, "ok", detail)


def _lockfile_check():
    """The seeded dependency lock file (MINUS-138). Without it every fresh run workspace
    re-downloads ~855 MB per provider instead of using the shared plugin cache, because with
    no lock entry Terraform must reach the registry for official checksums."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lock = os.path.join(os.path.dirname(root), ".agents", "terraform.lock.hcl")
    cache = os.environ.get("TF_PLUGIN_CACHE_DIR", "")
    if not os.path.exists(lock):
        return _check("terraform lock seed", "warn",
                      "no .agents/terraform.lock.hcl -- new runs will resolve providers from "
                      "the registry on every init",
                      "Copy a good .terraform.lock.hcl from an initialized run into "
                      ".agents/terraform.lock.hcl.")
    providers = sum(1 for line in open(lock, encoding="utf-8") if line.startswith("provider "))
    cached = "shared cache set" if cache and os.path.isdir(cache) else "NO plugin cache"
    return _check("terraform lock seed", "ok",
                  f"{providers} provider(s) pinned, {cached}")


def _python_check():
    version = platform.python_version()
    detail = f"Python {version}  [{sys.executable}]"
    if sys.version_info < (3, 10):
        return _check("python", "warn", detail, "MinusOps targets Python 3.10+.")
    return _check("python", "ok", detail)


def _packages_check():
    missing = [pkg for pkg in ("dash", "plotly") if importlib.util.find_spec(pkg) is None]
    if missing:
        return _check("python packages", "warn", f"missing: {', '.join(missing)}",
                      "pip install -r requirements.txt (needed only for `app/dashboard_app.py`)")
    return _check("python packages", "ok", "dash, plotly importable")


def _credentials_check():
    """Connected + credential posture. Long-term keys are a warn, not an ok: an
    unattended auto-approve run holding static keys can apply real infrastructure."""
    if not toolpath.find_tool("aws"):
        return _check("cloud credentials", "error", "skipped: AWS CLI not available",
                      "Install the AWS CLI, then run `aws sso login`.")
    try:
        posture = get_provider().credential_posture()
    except Exception as exc:
        return _check("cloud credentials", "error", f"credential probe failed: {exc}",
                      "Run `aws sts get-caller-identity` and resolve the error it reports.")
    if not posture.get("connected"):
        return _check("cloud credentials", "error",
                      posture.get("error") or "no valid credentials found",
                      "Run `aws sso login` (preferred) or `aws configure`.")
    kind = posture.get("type", "unknown")
    detail = f"account {posture.get('account')} as {posture.get('arn')} ({kind})"
    if kind in ("long_term", "root"):
        return _check("cloud credentials", "warn", detail,
                      "Prefer SSO / an assumed MFA-gated role; static or root credentials "
                      "let an unattended apply mutate real infrastructure.")
    return _check("cloud credentials", "ok", detail)


def _scanner_check():
    found = [s for s in EXTERNAL_SCANNERS if toolpath.find_tool(s)]
    if found:
        return _check("policy scanners", "ok", f"available: {', '.join(found)}")
    return _check("policy scanners", "warn",
                  f"none of {', '.join(EXTERNAL_SCANNERS)} on PATH",
                  "MINUS_POLICY_MODE=production requires one of these; the native SEC scan "
                  "still runs without them.")


def _teams_check():
    """Team directory (MINUS-153). Absent is `ok`, not a warning: the directory is opt-in and
    a machine without one generates exactly as it did before. Reporting it as a problem would
    make every clean environment look degraded."""
    path = team_resolver.config_path()
    if not os.path.exists(path):
        return _check("team directory", "ok",
                      "not configured -- --owner resolves to a bare team id (optional)")
    try:
        teams = team_resolver.list_teams(path)
    except Exception as exc:
        # A file someone wrote and got wrong. Silently ignoring it would look identical to
        # "no teams configured", hiding the mistake behind plausible behaviour.
        return _check("team directory", "error", f"{path} is unreadable: {exc}",
                      "Fix the YAML, or unset MINUS_TEAMS_CONFIG to run without a directory.")
    if not teams:
        return _check("team directory", "warn", f"{path} declares no teams",
                      "Add entries under `teams:`, or remove the file.")
    return _check("team directory", "ok", f"{len(teams)} team(s): {', '.join(teams[:6])}")


def _emulator_check():
    """G9 ephemeral-apply readiness: is an emulator selected, and is anything listening?

    Two separate facts, because they fail independently and the fix differs. A selected
    emulator with nothing behind it is the worse of the two -- the gate looks configured and
    then fails on every plan -- so it is reported distinctly rather than folded into "not
    ready". Warn, never error: G9 is an assurance layer, and a machine without Docker must
    still be able to plan.
    """
    name = (os.environ.get(plan_gate.G9_EMULATOR_ENV) or "").strip().lower()
    endpoint = os.environ.get(ephemeral_apply.LOCALSTACK_ENDPOINT_ENV,
                              ephemeral_apply.DEFAULT_LOCALSTACK_ENDPOINT)
    parsed = urllib.parse.urlparse(endpoint)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 4566
    listening = _port_open(host, port)
    where = f"{host}:{port}"
    supported = ", ".join(ephemeral_apply.SUPPORTED_EMULATORS)
    start = (f'docker run -d --name localstack -p {port}:{port} localstack/localstack  '
             f'&&  set {plan_gate.G9_EMULATOR_ENV}=localstack')

    if name and name not in ephemeral_apply.SUPPORTED_EMULATORS:
        # ephemeral_apply BLOCKS on an unrecognized name rather than guessing, so a typo here
        # disables G9 on every plan without ever saying why.
        return _check("g9 emulator", "warn",
                      f"{plan_gate.G9_EMULATOR_ENV}={name!r} is not a supported emulator",
                      f"Set it to one of: {supported}.")
    if name and listening:
        return _check("g9 emulator", "ok", f"{name} selected, {where} listening")
    if name:
        return _check("g9 emulator", "warn",
                      f"{name} selected but nothing is listening on {where} -- G9 will fail "
                      "on every plan", f"Start it: {start}")
    if listening:
        return _check("g9 emulator", "warn",
                      f"something is listening on {where} but {plan_gate.G9_EMULATOR_ENV} is "
                      "unset, so G9 stays off",
                      f"Name it: set {plan_gate.G9_EMULATOR_ENV}=localstack (or {supported}).")
    return _check("g9 emulator", "warn",
                  f"no emulator configured and nothing on {where} -- G9 ephemeral apply is "
                  "skipped", f"Start one: {start}")


# MINUS-154. Docker's CLI can hang indefinitely when the daemon is wedged -- observed on this
# project 2026-08-18, where every Docker Desktop process was alive, the named pipe existed, the
# WSL distro was Running, and `docker version` never returned. Every docker call here therefore
# carries a hard timeout, and a timeout is reported as "unresponsive", never as "not installed":
# they need completely different fixes and conflating them sends people to reinstall a working
# Docker.
_DOCKER_TIMEOUT_SECONDS = 20
_LOCALSTACK_START_TIMEOUT_SECONDS = 90


def _docker(args, timeout=_DOCKER_TIMEOUT_SECONDS):
    """(ok, stdout, error). `error` is "unresponsive" when the CLI itself never returns."""
    binary = toolpath.find_tool("docker")
    if not binary:
        return False, "", "docker not found on PATH"
    try:
        result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "", f"unresponsive: `docker {args[0]}` did not return in {timeout}s"
    except Exception as exc:
        return False, "", str(exc)
    if result.returncode != 0:
        return False, result.stdout or "", (result.stderr or "").strip()[:300]
    return True, (result.stdout or "").strip(), ""


def start_local_emulator(port=4566, container="localstack", image="localstack/localstack"):
    """Bring up LocalStack and report what happened. Returns {"ok", "action", "detail"}.

    Never raises, and never restarts Docker Desktop: a restart kills every other container on
    the machine, and this command was asked to fix an emulator, not to take over the host.
    """
    if _port_open("127.0.0.1", port):
        return {"ok": True, "action": "already-listening",
                "detail": f"something already answers on 127.0.0.1:{port}"}

    ok, _, err = _docker(["info", "--format", "{{.ServerVersion}}"])
    if not ok:
        return {"ok": False, "action": "docker-unavailable",
                "detail": f"{err}. Start Docker Desktop (or fix the daemon) and re-run; "
                          "this command will not restart it for you, because that would kill "
                          "every other container on this machine."}

    # An existing stopped container is started rather than recreated: recreating silently
    # discards whatever state a previous session left in it.
    ok, existing, _ = _docker(["ps", "-aq", "--filter", f"name=^{container}$"])
    if ok and existing:
        ok, _, err = _docker(["start", container], timeout=_LOCALSTACK_START_TIMEOUT_SECONDS)
        if not ok:
            return {"ok": False, "action": "start-failed", "detail": err}
    else:
        ok, _, err = _docker(["run", "-d", "--name", container, "-p", f"{port}:{port}", image],
                             timeout=_LOCALSTACK_START_TIMEOUT_SECONDS)
        if not ok:
            return {"ok": False, "action": "run-failed", "detail": err}

    # The container being up is not the same as the port answering: LocalStack takes seconds to
    # bind, and reporting success on `docker run` alone is how a green fix leaves a red gate.
    deadline = time.time() + _LOCALSTACK_START_TIMEOUT_SECONDS
    while time.time() < deadline:
        if _port_open("127.0.0.1", port):
            return {"ok": True, "action": "started",
                    "detail": f"{container} is listening on 127.0.0.1:{port}"}
        time.sleep(2)
    return {"ok": False, "action": "started-not-listening",
            "detail": f"{container} started but nothing answers on {port} after "
                      f"{_LOCALSTACK_START_TIMEOUT_SECONDS}s -- check `docker logs {container}`"}


def fix(checks):
    """Attempt the repairs doctor knows how to make. Returns a list of result dicts.

    Only the G9 emulator is auto-fixable today. The other warnings need a package install or a
    credential decision -- things this command must not make on someone's behalf.
    """
    results = []
    emulator = next((c for c in checks if c["name"] == "g9 emulator"), None)
    if emulator and emulator["status"] != "ok":
        outcome = start_local_emulator()
        # The env var to set is RETURNED, not applied here. A diagnostic function that mutates
        # process environment leaks into everything that runs after it -- caught directly:
        # setting it inside fix() changed plan_gate's behaviour in three unrelated tests that
        # happened to run later in the same session.
        results.append({"check": "g9 emulator",
                        "env": {plan_gate.G9_EMULATOR_ENV: "localstack"} if outcome["ok"] else {},
                        **outcome})
    return results


def diagnose():
    """Run every check. Returns {"ok": bool, "checks": [...]} — ok is False iff any error."""
    checks = [
        _python_check(),
        # >= 1.5 matches the `required_version` the synthesizer writes into every composed
        # root, so a machine below it cannot plan what this repo generates.
        _cli_check("terraform", "terraform", ("version",), True,
                   "Install Terraform >= 1.5 (winget install Hashicorp.Terraform / "
                   "brew install terraform).", min_version=(1, 5)),
        _cli_check("aws cli", "aws", ("--version",), True,
                   "Install the AWS CLI v2 (winget install Amazon.AWSCLI / brew install awscli).",
                   min_version=(2,)),
        _credentials_check(),
        _cli_check("opa", "opa", ("version",), False,
                   "Install OPA to enable the Rego plan gate; it degrades to warn-only without it."),
        _cli_check("tflint", "tflint", ("--version",), False,
                   "Install TFLint for provider-level lint findings in optimize_analyzer; "
                   "run `tflint --init` in a Terraform dir to add the AWS ruleset."),
        _scanner_check(),
        _teams_check(),
        _lockfile_check(),
        _emulator_check(),
        _packages_check(),
    ]
    return {"ok": not any(c["status"] == "error" for c in checks), "checks": checks}


_MARK = {"ok": "[OK]  ", "warn": "[WARN]", "error": "[ERR] "}


def format_result(result):
    lines = [f"MinusOps doctor - {platform.system()} {platform.release()}", ""]
    for check in result["checks"]:
        lines.append(f"{_MARK[check['status']]} {check['name']}: {check['detail']}")
        if check.get("fix"):
            lines.append(f"        fix: {check['fix']}")
    lines.append("")
    errors = [c["name"] for c in result["checks"] if c["status"] == "error"]
    lines.append("environment ready" if result["ok"] else "blocked on: " + ", ".join(errors))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Diagnose the local MinusOps environment.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result = diagnose()
    print(json.dumps(result, indent=2) if args.json else format_result(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
