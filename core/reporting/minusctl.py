"""
Operator-facing CLI for MinusOps.

This is a thin safe wrapper around the repo tools. Commands either create local run files,
inspect local artifacts, or print the next safe command to run.

ONE EXCEPTION: `minusctl seed --execute` uploads a fixture, starts a Glue job, and runs an
Athena query. It is the only command here that mutates AWS, it is opt-in (without
`--execute` it prints the commands and changes nothing), and every side effect passes
through `approval.py` first -- gatekeeper by default, fail-closed without a TTY, audited
either way. `minusctl doctor` reads AWS credentials (read-only) and can start a local
LocalStack container with `--fix`. `minusctl adopt` writes only `.minus/` inside the
directory it is pointed at, and only with `--anchor`.

Depends on: core/architecture/{architecture_decision,architecture_model,requirements}.py,
    core/generation/{accelerators,demo,workflow}.py, core/governance/{audit_chain,
    rule_stages,source_guard,tf_validate}.py, core/reporting/{adopt,cli_diagnostics,doctor,
    plan_inspector,runs,seed}.py, plus core/reporting/toolpath.py, core/governance/
    approval.py and core/providers/base.py imported lazily inside the subcommands that
    need them
Shells out to: nothing directly. External processes are reached only through the modules
    above -- `terraform` via tf_validate/seed, the `aws` CLI via seed (mutating, only with
    `--execute`) and doctor (read-only), `docker` via `doctor --fix`.
Used by: app/dashboard_app.py, tests/test_minusctl.py, tests/test_doctor.py,
    tests/test_cli_diagnostics.py
"""
import argparse
import json
import os
import sys
from pathlib import Path

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
import architecture_decision as archdec  # noqa: E402
import architecture_model  # noqa: E402
import accelerators  # noqa: E402
import audit_chain  # noqa: E402
import cicd as cicd_engine  # noqa: E402
import cli_diagnostics  # noqa: E402
import incident_diagnostics  # noqa: E402
import adopt as adopt_engine  # noqa: E402
import demo  # noqa: E402
import doctor  # noqa: E402
import seed as seed_engine  # noqa: E402
import plan_inspector  # noqa: E402
import requirements as reqgate  # noqa: E402
import rule_stages  # noqa: E402
import runs  # noqa: E402
import source_guard  # noqa: E402
import tf_validate  # noqa: E402
import workflow  # noqa: E402


def _json_or_text(data, as_json, text):
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        print(text)


def _latest_run_or_exit():
    run = runs.latest_run()
    if not run:
        # A bare `no run workspaces found` tells an agent nothing it can act on, so the
        # three-part agent error is used even here.
        raise SystemExit(cli_diagnostics.format_agent_error(
            "No run workspaces exist yet.",
            "Nothing has been created in runs/ on this machine.",
            'minusctl create "<what you want to build>"'))
    return run


def _run_by_id_or_latest(run_id=None, command="next"):
    """Resolve a run id, or exit with a suggestion.

    SystemExit rather than a return code: every call site here treats an unresolvable run as
    fatal, so threading an error code through them all would rewrite a dozen callers to fix a
    message. The message is the part that matters -- a bare `run not found: <typo>` gives an
    agent nothing to do next.
    """
    # `runs/<id>` is what our own error output shows and what an operator copies from a
    # directory listing, so accept it rather than bouncing a paste that names the right run.
    if run_id:
        run_id = run_id.replace("\\", "/").rstrip("/")
        if run_id.startswith("runs/"):
            run_id = run_id[len("runs/"):]

    if not run_id or run_id == "latest":
        run = _latest_run_or_exit()
        _require_stage_or_exit(run, command)
        return run
    for item in runs.list_runs():
        if item.get("run_id") == run_id or item.get("run_id", "").startswith(run_id):
            _require_stage_or_exit(item, command)
            return item

    suggestions = cli_diagnostics.suggest_runs(run_id)
    if suggestions:
        # Each candidate carries what it is FOR, because two runs from the same day differ
        # only in that. Suggesting a bare id invites accepting the first one, which is how an
        # agent ends up operating the wrong workload.
        candidates = cli_diagnostics.format_candidates(suggestions)
        reason = (f"No run matches {run_id!r}. {len(suggestions)} existing run(s) are close -- "
                  "likely a typo or a truncated timestamp. Compare the descriptions before "
                  "picking one.")
        fix = [f"minusctl {command} --run {rid}" for rid in suggestions]
        context = {"possible matches": chr(10) + candidates}
    else:
        reason = f"No run matches {run_id!r}, and nothing in runs/ is close enough to guess at."
        fix = f"minusctl {command} --run <id from the list below>"
        context = {"recent runs": chr(10) + cli_diagnostics.format_candidates(
            [rid for rid, _ in cli_diagnostics.recent_runs()])}
    raise SystemExit(cli_diagnostics.format_agent_error(
        f"Run workspace {run_id!r} not found.", reason, fix, context))


# up_to = the last lifecycle step this subcommand genuinely depends on.
# `decision` deliberately needs only step 1: it is the command that WRITES step 2.
_STAGE_REQUIREMENTS = {
    "validate": 3, "conformance": 3, "readiness": 3, "package": 3, "prove": 3,
    "accelerator": 1, "decision": 1, "seed": 3,
}


def _require_stage_or_exit(run, command):
    """Block a subcommand whose prior lifecycle step never ran, naming that exact step."""
    up_to = _STAGE_REQUIREMENTS.get(command)
    if up_to is None:
        return
    gap = cli_diagnostics.missing_prerequisite(
        run.get("root", ""), run.get("run_id", "<id>"), up_to=up_to)
    if not gap:
        return
    raise SystemExit(cli_diagnostics.format_agent_error(
        f"`{command}` needs step {gap['step']} ({gap['name']}), which has not run.",
        f"{gap['artifact']} is missing from {run.get('root', '?')}.",
        gap["command"],
        {"run": run.get("run_id", "?")}))


def _terraform_dir(args):
    if getattr(args, "dir", None):
        return args.dir
    run = _run_by_id_or_latest(getattr(args, "run", None),
                               command=getattr(args, "cmd", "next"))
    return run["terraform_dir"]


def _format_run(run):
    return "\n".join([
        f"run        : {run['run_id']}",
        f"blueprint  : {run.get('blueprint', '-')}",
        f"cloud      : {run.get('cloud', '-')}",
        f"terraform  : {run.get('terraform_dir', '-')}",
        f"reports    : {run.get('reports_dir', '-')}",
    ])


def _print_runs(as_json=False):
    items = runs.list_runs()
    if as_json:
        print(json.dumps(items, indent=2))
        return
    if not items:
        print("no runs")
        return
    for item in items:
        print(f"{item['run_id']}\t{item.get('blueprint', '-')}\t{item.get('terraform_dir', '-')}")


def _report_id(args):
    return "latest" if getattr(args, "latest", False) else args.report


def _print_report_command(command, args):
    if command == "list":
        plan_inspector._print_list()
        return
    report_id = _report_id(args)
    if not report_id:
        raise SystemExit("--report or --latest is required")
    _, manifest, plan = plan_inspector.load_report(report_id)
    if command == "status":
        print(json.dumps(plan_inspector.source_status(report_id), indent=2))
    elif command == "services":
        plan_inspector._print_services(plan)
    elif command == "resources":
        plan_inspector._print_resources(plan)
    elif command == "roles":
        plan_inspector._print_roles(plan)
    elif command == "files":
        plan_inspector._print_files(report_id)
    elif command == "diff":
        print("\n".join(plan_inspector.diff_source(report_id)))
    else:
        raise SystemExit(f"unknown report command: {command}")


def _run_reports(run):
    reports = []
    root = Path(run["reports_dir"])
    if root.exists():
        for path in root.iterdir():
            manifest = path / "manifest.json"
            plan = path / "plan.json"
            if not path.is_dir() or not manifest.exists():
                continue
            try:
                meta = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            reports.append({
                "id": path.name,
                "generated_at": meta.get("generated_at", ""),
                "path": str(path),
                "has_plan_json": plan.exists(),
                "manifest": meta,
            })
    reports.sort(key=lambda item: item.get("generated_at", ""), reverse=True)
    return reports


def _diagnostic_banner(run):
    """A one-line pointer when the run holds a failure.

    `next` is where an operator looks after something breaks, and sending them to the happy
    path while a failed proving report sits in the run is how the failure gets missed. Only
    when there IS one: a banner on every healthy run is noise nobody reads.
    """
    evidence = incident_diagnostics.extract_evidence(run.get("root", ""))
    if not evidence["raw_error"]:
        return []
    result = incident_diagnostics.diagnose(evidence["raw_error"])
    label = result["rule_id"] if result["matched"] else "unclassified failure"
    return ["", f"failure    : {label} -- {result['category']}",
            f"diagnose   : minusctl diagnose --run {run.get('run_id', '<id>')}"]


def _next_steps(run):
    tf_dir = run["terraform_dir"]
    workflow_record = _read_workflow(run)
    requirements_file = workflow_record.get("requirements_file") or str(Path(run["root"]) / reqgate.FILENAME)
    decision_file = str(Path(run["root"]) / archdec.FILENAME)
    requirements_data = reqgate.load(requirements_file)
    requirements_ok, missing_requirements = reqgate.validate(requirements_data or {})
    decision_data = archdec.load(decision_file)
    decision_ok, missing_decision = archdec.validate(decision_data or {})
    if workflow_record.get("architecture_decision_required") and not (Path(tf_dir) / "minus-generated.json").exists():
        lines = [
            "Safe next steps",
            f"run        : {run['run_id']}",
            f"request    : {run.get('request', '-')}",
            f"requirements: {requirements_file}",
            f"decision   : {decision_file}",
            f"req status : {'complete' if requirements_ok else 'incomplete'}",
            f"arch status: {'complete' if decision_ok else 'incomplete'}",
        ]
        if missing_requirements:
            lines.append("req missing: " + ", ".join(missing_requirements))
        if missing_decision:
            lines.append("arch missing: " + ", ".join(missing_decision))
        lines.extend([
            "complete   : python core/architecture/requirements.py check " + requirements_file,
            "decide     : minusctl decision template --write",
            "check arch : python core/architecture/architecture_decision.py check " + decision_file,
            "synthesize : python core/generation/synthesizer.py \"<requirements summary>\" --run " + run["run_id"] + " --requirements-file " + requirements_file + " --decision-file " + decision_file,
            "blocked    : do not generate Terraform from demo fixtures for production",
        ])
        lines.extend(_diagnostic_banner(run))
        return {"run": run, "source": {"status": "PRE_GENERATION"}, "reports": [], "text": "\n".join(lines)}

    guard = source_guard.status(tf_dir)
    reports = _run_reports(run)
    lines = [
        "Safe next steps",
        f"run        : {run['run_id']}",
        f"terraform  : {tf_dir}",
        f"source     : {guard['status']}",
    ]
    if guard["status"] == "STALE":
        lines.append("review diff : minusctl guard diff --run " + run["run_id"])
    if not reports:
        lines.extend([
            "verify     : minusctl gate verify --dir " + tf_dir,
            "prod verify: minusctl gate verify --dir " + tf_dir + " --policy-mode production",
            "plan       : minusctl gate plan --dir " + tf_dir,
            "reports    : none yet",
        ])
    else:
        lines.extend([
            "latest rpt : " + reports[0]["id"],
            "inspect    : minusctl reports services --latest",
            "drift      : minusctl reports diff --latest",
        ])
    lines.append("blocked    : do not apply until a reviewed plan hash is approved")
    lines.extend(_diagnostic_banner(run))
    return {"run": run, "source": guard, "reports": reports, "text": "\n".join(lines)}


def _read_workflow(run):
    path = Path(run["root"]) / "workflow.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _generated_files(run):
    manifest = Path(run["terraform_dir"]) / "minus-generated.json"
    if not manifest.exists():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data.get("files", [])


def _cost_drift_pct(latest):
    """Total forecast-vs-actual variance %, or None when no actuals were pulled."""
    variance = ((latest or {}).get("cost") or {}).get("variance") or {}
    f, a = variance.get("forecast_total"), variance.get("actual_total")
    try:
        return (float(a) - float(f)) / float(f) * 100 if f else None
    except (TypeError, ValueError):
        return None


def _latest_report_details(reports):
    if not reports:
        return {}
    item = reports[0]
    if not item.get("has_plan_json"):
        return {"id": item["id"], "path": item["path"]}
    try:
        report_path = Path(item["path"])
        manifest = json.loads((report_path / "manifest.json").read_text(encoding="utf-8"))
        plan = json.loads((report_path / "plan.json").read_text(encoding="utf-8"))
    except Exception:
        return {"id": item["id"], "path": item["path"], "error": "report could not be loaded"}
    services = {
        service: len(rows)
        for service, rows in plan_inspector.services(plan).items()
    }
    roles = plan_inspector.iam_roles(plan)
    return {
        "id": item["id"],
        "path": item["path"],
        "generated_at": manifest.get("generated_at"),
        "template": manifest.get("template"),
        "counts": manifest.get("counts", {}),
        "cost": manifest.get("cost", {}),
        "services": services,
        "iam_role_count": len(roles.get("roles", [])),
        "iam_policy_count": len(roles.get("policies", [])),
        "source_status": _report_source_status(Path(item["path"]), manifest),
    }


def _report_source_status(report_path, manifest):
    source_dir = manifest.get("dir")
    hash_path = report_path / "source_hashes.json"
    if not source_dir or not hash_path.exists():
        return {"status": "UNKNOWN", "stale": False, "reason": "source snapshot unavailable"}
    try:
        saved = json.loads(hash_path.read_text(encoding="utf-8"))
        current = plan_inspector.source_hashes(source_dir)
    except Exception as exc:
        return {"status": "UNKNOWN", "stale": False, "reason": str(exc)}
    changed = sorted(k for k in saved if k in current and saved[k] != current[k])
    missing = sorted(k for k in saved if k not in current)
    added = sorted(k for k in current if k not in saved)
    stale = bool(changed or missing or added)
    return {
        "status": "STALE" if stale else "CURRENT",
        "stale": stale,
        "changed": changed,
        "missing": missing,
        "added": added,
    }


def _conformance_for_run(run, reports=None):
    """Score the run's latest plan against the analytics reference architecture +
    Well-Architected Analytics Lens. Returns None when there is no plan.json to analyze.
    The run's declared data volume (requirements) activates the scale-tier checks."""
    reports = reports if reports is not None else _run_reports(run)
    if not reports or not reports[0].get("has_plan_json"):
        return None
    try:
        plan = json.loads((Path(reports[0]["path"]) / "plan.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    daily_gb = 0
    try:
        spec = reqgate.load(os.path.join(run.get("root", ""), reqgate.FILENAME))
        daily_gb, _src = reqgate.parse_daily_gb(spec or {})
    except Exception:
        pass
    try:
        return architecture_model.conformance(plan, daily_data_gb=daily_gb)
    except Exception:
        return None


def _format_conformance(report):
    if not report:
        return "Reference conformance: no plan to analyze (run plan_gate plan first)."
    lines = [
        "Reference-architecture conformance",
        f"status : {report['status']}",
        f"score  : {report['score']}/100",
        "",
        "Layers:",
    ]
    for layer, info in report["layers"].items():
        mark = "present" if info["present"] else "MISSING"
        lines.append(f"- {layer:<12} {mark} ({info['count']})")
    lines.append("")
    lines.append("Findings:")
    if not report["findings"]:
        lines.append("- none — conforms to the reference architecture + Well-Architected checks")
    for f in report["findings"]:
        lines.append(f"- [{f['severity']}] {f['id']}: {f['title']}")
        lines.append(f"    {f['detail']}")
        lines.append(f"    ref: {f['reference']}")
    return "\n".join(lines)


def _check(name, ok, severity, detail, fix):
    return {
        "name": name,
        "ok": bool(ok),
        "severity": severity,
        "detail": detail,
        "fix": fix,
    }


def _readiness(run):
    tf_dir = Path(run["terraform_dir"])
    workflow_record = _read_workflow(run)
    requirements_file = workflow_record.get("requirements_file") or str(Path(run["root"]) / reqgate.FILENAME)
    decision_file = str(Path(run["root"]) / archdec.FILENAME)
    requirements_data = reqgate.load(requirements_file)
    requirements_ok, missing_requirements = reqgate.validate(requirements_data or {})
    decision_data = archdec.load(decision_file)
    decision_ok, missing_decision = archdec.validate(decision_data or {})
    if workflow_record.get("architecture_decision_required") and not (tf_dir / "minus-generated.json").exists():
        checks = [
            _check(
                "requirements record exists",
                bool(requirements_data),
                "blocker",
                requirements_file,
                "Run `minusctl create \"<request>\"` to create a requirements-first run.",
            ),
            _check(
                "requirements complete",
                requirements_ok,
                "blocker",
                ", ".join(missing_requirements) if missing_requirements else "complete",
                "Gather the missing functional and non-functional requirements before architecture synthesis.",
            ),
            _check(
                "architecture decision recorded",
                bool(decision_data),
                "blocker",
                decision_file,
                "Research candidates and record the selected architecture before generating Terraform.",
            ),
            _check(
                "architecture decision complete",
                decision_ok,
                "blocker",
                ", ".join(missing_decision) if missing_decision else "complete",
                "Fill selected architecture, selected modules, alternatives, assumptions, risks, "
                "validation, rollback, and sources.",
            ),
        ]
        blockers = [item for item in checks if not item["ok"] and item["severity"] == "blocker"]
        return {
            "status": "NEEDS_REQUIREMENTS" if blockers else "READY_TO_SYNTHESIZE",
            "score": max(0, 100 - len(blockers) * 25),
            "run": run,
            "source": {"status": "PRE_GENERATION"},
            "reports": [],
            "latest_report": {},
            "generated_files": [],
            "checks": checks,
            "blockers": blockers,
            "warnings": [],
        }

    reports = _run_reports(run)
    source = source_guard.status(tf_dir)
    generated_files = _generated_files(run)
    latest = _latest_report_details(reports)
    latest_path = Path(latest["path"]) if latest.get("path") else None
    conformance = _conformance_for_run(run, reports)
    # The workspace must contain REAL Terraform content. Layout-agnostic (module composition
    # and flat blueprints are both legitimate), but a presence-only check passes on one-line
    # comment stubs, so: (a) the root files must collectively declare infrastructure +
    # provider + variables, and (b) no root .tf file may be a contentless stub
    # (comments/blanks only).
    import re as _re
    _BLOCK_RE = _re.compile(r'^\s*(resource|module|data|variable|output|provider|locals|terraform)\b', _re.M)

    def _tf_text(path):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    _root_tf = sorted(tf_dir.glob("*.tf")) if tf_dir.is_dir() else []
    _all_text = "\n".join(_tf_text(p) for p in _root_tf)
    core_tf_missing = []
    if not _re.search(r'^\s*(resource|module)\s+"', _all_text, _re.M):
        core_tf_missing.append("no resource/module blocks in any root .tf")
    if not _re.search(r'^\s*(provider\s+"|terraform\s*\{)', _all_text, _re.M):
        core_tf_missing.append("no provider/terraform block")
    if not _re.search(r'^\s*variable\s+"', _all_text, _re.M):
        core_tf_missing.append("no variable blocks")
    core_tf_missing += [f"{p.name} is a contentless stub"
                        for p in _root_tf if not _BLOCK_RE.search(_tf_text(p))]
    required_report_files = ["architecture.svg", "plan.pdf", "cost.pdf", "report.html"]
    checks = [
        _check(
            "terraform directory exists",
            tf_dir.exists() and tf_dir.is_dir(),
            "blocker",
            str(tf_dir),
            "Create a requirements-first run with `minusctl create \"<request>\"`, then synthesize Terraform after architecture approval.",
        ),
        _check(
            "generated manifest exists",
            (tf_dir / "minus-generated.json").exists(),
            "blocker",
            "minus-generated.json",
            "Regenerate the Terraform workspace through the governed workflow.",
        ),
        _check(
            "source baseline exists",
            (tf_dir / ".minus" / "baseline.json").exists(),
            "blocker",
            ".minus/baseline.json",
            "Run `minusctl guard baseline --run " + run["run_id"] + "` after reviewing the generated source.",
        ),
        _check(
            "source is current",
            source.get("status") == "CURRENT",
            "blocker",
            source.get("status", "UNKNOWN"),
            "Run `minusctl guard diff --run " + run["run_id"] + "` and reconcile manual edits.",
        ),
        _check(
            "core Terraform files present",
            not core_tf_missing,
            "blocker",
            "; ".join(core_tf_missing) or "real content present",
            "Regenerate the Terraform workspace — empty or comment-only stubs do not count.",
        ),
        _check(
            "report exists",
            bool(reports),
            "warning",
            reports[0]["id"] if reports else "none",
            "Run `minusctl gate verify --dir <terraform-dir> --policy-mode production` then `minusctl gate plan --dir <terraform-dir>`.",
        ),
        _check(
            "latest report has required visuals",
            bool(latest_path) and all((latest_path / name).exists() for name in required_report_files),
            "warning",
            ", ".join(name for name in required_report_files if not latest_path or not (latest_path / name).exists()) or "all present",
            "Regenerate the report after planning, then inspect the dashboard report links.",
        ),
        _check(
            "report source snapshot current",
            not latest or latest.get("source_status", {}).get("status") == "CURRENT",
            "warning",
            latest.get("source_status", {}).get("status", "no report"),
            "Run `minusctl reports diff --latest` and regenerate the plan if files changed.",
        ),
        _check(
            "cost evidence is BCM-backed",
            bool(latest.get("cost", {}).get("ok")),
            "warning",
            latest.get("cost", {}).get("pricing_source", "BCM Pricing Calculator API required"),
            "Estimates are created automatically when AWS credentials with BCM access exist; "
            "configure credentials and regenerate the report.",
        ),
        _check(
            "forecast within budget guardrail",
            not (latest.get("cost", {}).get("ok")
                 and latest.get("cost", {}).get("monthly_budget_usd")
                 and float(latest.get("cost", {}).get("monthly_total_usd") or 0)
                 > float(latest.get("cost", {}).get("monthly_budget_usd"))),
            "warning",
            (f"forecast ${float(latest.get('cost', {}).get('monthly_total_usd') or 0):,.2f}/mo vs "
             f"budget ${float(latest.get('cost', {}).get('monthly_budget_usd') or 0):,.2f}/mo"
             if latest.get("cost", {}).get("monthly_budget_usd") else "no budget/estimate to compare"),
            "Raise monthly_budget_usd on governance-observability (or reduce scope) and re-plan — "
            "the plan provisions a guardrail its own forecast already exceeds.",
        ),
        _check(
            "forecast vs actuals drift",
            _cost_drift_pct(latest) is None
            or abs(_cost_drift_pct(latest)) < float(os.environ.get("MINUS_VARIANCE_ALERT_PCT", "20")),
            "warning",
            ("no actuals pulled yet — n/a" if _cost_drift_pct(latest) is None
             else f"total variance {_cost_drift_pct(latest):+.1f}% vs forecast"),
            "Actual spend drifted from the BCM forecast — investigate before the next run: "
            "`python core/cost/bcm_pricing_calculator.py actuals --report-dir <report>` refreshes actuals.",
        ),
        _check(
            "terraform configuration valid",
            not ((_tf_validation := tf_validate.load(str(tf_dir))) and _tf_validation.get("ok") is False),
            "warning",
            ("valid" if (_tf_validation and _tf_validation.get("ok")) else
             "not recorded — run `validate`" if not _tf_validation else
             "terraform not installed" if _tf_validation.get("ok") is None else
             f"{_tf_validation.get('error_count', '?')} error(s)"),
            "Run `minusctl validate --run " + run["run_id"] + "` (offline, no credentials).",
        ),
        _check(
            "data-pipeline requirements profile",
            (not reqgate.is_data_pipeline(requirements_data or {}))
            or reqgate.validate_data_pipeline(requirements_data or {})[0],
            "warning",
            (("complete" if reqgate.validate_data_pipeline(requirements_data or {})[0]
              else ", ".join(reqgate.validate_data_pipeline(requirements_data or {})[1]))
             if reqgate.is_data_pipeline(requirements_data or {}) else "n/a (not a data workload)"),
            "Gather the data-pipeline FR/NFR: `python core/architecture/requirements.py data-check "
            + str(requirements_file) + "` (or run grill-me).",
        ),
        _check(
            "reference-architecture conformance",
            bool(conformance) and conformance["score"] >= 90,
            "warning",
            (f"{conformance['score']}/100, {len(conformance['findings'])} finding(s)"
             if conformance else "no plan to analyze"),
            "Run `minusctl conformance --run " + run["run_id"]
            + "` and address the reference / Well-Architected gaps.",
        ),
        _check(
            "safe package can be written",
            bool(run.get("root")) and Path(run["root"]).exists(),
            "info",
            run.get("root", "-"),
            "Run `minusctl package --run " + run["run_id"] + "`.",
        ),
    ]
    blockers = [item for item in checks if not item["ok"] and item["severity"] == "blocker"]
    warnings = [item for item in checks if not item["ok"] and item["severity"] == "warning"]
    score = max(0, 100 - len(blockers) * 20 - len(warnings) * 7)
    status = "READY" if not blockers and not warnings else "BLOCKED" if blockers else "NEEDS_EVIDENCE"
    return {
        "status": status,
        "score": score,
        "run": run,
        "source": source,
        "reports": [{k: v for k, v in item.items() if k != "manifest"} for item in reports],
        "latest_report": latest,
        "conformance": conformance,
        "generated_files": generated_files,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
    }


def _format_readiness(readiness):
    lines = [
        "Enterprise readiness",
        f"status     : {readiness['status']}",
        f"score      : {readiness['score']}/100",
        f"run        : {readiness['run'].get('run_id', '-')}",
        f"terraform  : {readiness['run'].get('terraform_dir', '-')}",
        "",
        "Checks:",
    ]
    for item in readiness["checks"]:
        marker = "OK" if item["ok"] else item["severity"].upper()
        lines.append(f"- {marker}: {item['name']} ({item['detail']})")
        if not item["ok"]:
            lines.append(f"  fix: {item['fix']}")
    return "\n".join(lines)


def _package_markdown(package):
    run = package["run"]
    latest = package.get("latest_report") or {}
    lines = [
        "# MinusOps Enterprise Run Package",
        "",
        "## Run",
        "",
        f"- Run ID: `{run.get('run_id', '-')}`",
        f"- Blueprint: `{run.get('blueprint', '-')}`",
        f"- Cloud: `{run.get('cloud', '-')}`",
        f"- Terraform directory: `{run.get('terraform_dir', '-')}`",
        f"- Reports directory: `{run.get('reports_dir', '-')}`",
        f"- Readiness: `{package.get('readiness', {}).get('status', '-')}` ({package.get('readiness', {}).get('score', '-')}/100)",
        "",
        "## Request",
        "",
        package.get("request") or "-",
        "",
        "## Source Status",
        "",
        f"- Status: `{package['source'].get('status', '-')}`",
        f"- Changed files: `{len(package['source'].get('changed', []))}`",
        f"- Missing files: `{len(package['source'].get('missing', []))}`",
        f"- Added files: `{len(package['source'].get('added', []))}`",
        "",
        "## Generated Terraform Files",
        "",
    ]
    files = package.get("generated_files") or []
    lines.extend([f"- `{name}`" for name in files] or ["- No generated file manifest found."])
    lines.extend(["", "## Latest Report", ""])
    if latest:
        lines.extend([
            f"- Report ID: `{latest.get('id', '-')}`",
            f"- Path: `{latest.get('path', '-')}`",
            f"- Generated at: `{latest.get('generated_at', '-')}`",
            f"- Plan counts: `{latest.get('counts', {})}`",
            f"- Source status: `{latest.get('source_status', {}).get('status', '-')}`",
        ])
        services = latest.get("services") or {}
        if services:
            lines.extend(["", "### Services", ""])
            lines.extend([f"- {name}: {count}" for name, count in services.items()])
        lines.extend([
            "",
            "### Cost Evidence",
            "",
            f"- Status: `{latest.get('cost', {}).get('ok', False)}`",
            f"- Source: `{latest.get('cost', {}).get('pricing_source', 'BCM Pricing Calculator API required')}`",
        ])
    else:
        lines.append("- No report exists yet. Run `minusctl gate plan --dir <terraform-dir>` after verification.")
    lines.extend([
        "",
        "## Safe Next Steps",
        "",
    ])
    lines.extend([f"- `{line}`" for line in package["next"]["text"].splitlines()])
    lines.extend([
        "",
        "## Readiness Checks",
        "",
    ])
    readiness = package.get("readiness", {})
    for item in readiness.get("checks", []):
        marker = "OK" if item.get("ok") else item.get("severity", "issue").upper()
        lines.append(f"- **{marker}** `{item.get('name')}`: {item.get('detail')}")
        if not item.get("ok"):
            lines.append(f"  - Fix: {item.get('fix')}")
    conformance = readiness.get("conformance")
    if conformance:
        lines.extend([
            "",
            "## Reference-Architecture Conformance",
            "",
            f"- Score: `{conformance['score']}/100` ({conformance['status']})",
            f"- Layers present: "
            + ", ".join(layer for layer, info in conformance["layers"].items() if info["present"]),
            "",
        ])
        if conformance["findings"]:
            lines.append("Gaps (vs AWS reference architecture + Well-Architected Analytics Lens):")
            for f in conformance["findings"]:
                lines.append(f"- **{f['severity']}** `{f['id']}`: {f['title']} — {f['reference']}")
        else:
            lines.append("- No gaps — conforms to the reference architecture + Well-Architected checks.")
    lines.extend([
        "",
        "## Blocked Actions",
        "",
        "- Do not run `terraform apply`, `terraform destroy`, mutating cloud CLI commands, or mutating git commands directly, outside the gate, until the exact plan hash is reviewed and approved. Teardown is governed the same way as create/modify: `minusctl gate plan --dir <dir> --destroy`, then the normal `approve`/`apply`.",
        "- Do not publish enterprise cost totals unless AWS BCM Pricing Calculator API evidence exists.",
        "",
    ])
    return "\n".join(lines)


def _build_package(run):
    workflow_record = _read_workflow(run)
    source = source_guard.status(run["terraform_dir"])
    reports = _run_reports(run)
    readiness = _readiness(run)
    package = {
        "run": run,
        "request": run.get("request") or workflow_record.get("resolution", {}).get("query", ""),
        "workflow": workflow_record,
        "source": source,
        "generated_files": _generated_files(run),
        "reports": [{k: v for k, v in item.items() if k != "manifest"} for item in reports],
        "latest_report": _latest_report_details(reports),
        "next": _next_steps(run),
        "readiness": readiness,
    }
    package["markdown"] = _package_markdown(package)
    return package


def _write_package(run):
    package = _build_package(run)
    root = Path(run["root"])
    md_path = root / "enterprise-package.md"
    json_path = root / "enterprise-package.json"
    md_path.write_text(package["markdown"] + "\n", encoding="utf-8")
    json_data = {k: v for k, v in package.items() if k != "markdown"}
    json_path.write_text(json.dumps(json_data, indent=2) + "\n", encoding="utf-8")
    package["paths"] = {"markdown": str(md_path), "json": str(json_path)}
    return package


def _prove(run):
    """
    End-to-end evidence harness: prove the offline governance chain works on this environment
    (generate -> report artifacts -> audit-chain integrity -> readiness), then report exactly
    which AWS-gated steps remain (real BCM estimate, real gated apply) with the credential
    posture. Writes evidence.md + evidence.json — a hand-off artifact.
    """
    import toolpath  # noqa: E402
    tf_dir = run["terraform_dir"]
    reports = _run_reports(run)
    latest = reports[0] if reports else None
    latest_path = latest["path"] if latest else None
    audit_path = os.path.join(os.getcwd(), ".agents", "logs", "audit.jsonl")
    chain = audit_chain.chain_status(audit_path)
    audit_ok = chain["intact"]            # chained segment intact (tolerates a pre-chaining legacy prefix)
    audit_errors = chain["errors"]
    readiness = _readiness(run)
    terraform = toolpath.find_tool("terraform")
    aws = toolpath.find_tool("aws")
    try:
        from providers.base import get_provider
        posture = get_provider().credential_posture()
    except Exception:
        posture = {"connected": False, "type": "unknown"}

    def artifact(name):
        return bool(latest_path) and os.path.exists(os.path.join(latest_path, name))

    checks = [
        _check("run workspace generated", os.path.isdir(tf_dir), "blocker", tf_dir, "minusctl create \"<request>\""),
        _check("deploy report present", bool(reports), "blocker", latest["id"] if latest else "none", "plan_gate plan"),
        _check("architecture.svg", artifact("architecture.svg"), "warning", "-", "regenerate report"),
        _check("plan.pdf", artifact("plan.pdf"), "warning", "-", "regenerate report"),
        _check("cost.pdf", artifact("cost.pdf"), "warning", "-", "regenerate report"),
        _check("audit chain intact", audit_ok, "blocker",
               (f"ok ({chain['chained_count']} chained"
                + (f", {chain['legacy_count']} legacy pre-chain" if chain["legacy_count"] else "") + ")")
               if audit_ok else f"{len(audit_errors)} error(s)", "investigate audit.jsonl"),
        _check("terraform available", bool(terraform), "warning", terraform or "not found", "install terraform"),
        _check("aws CLI available", bool(aws), "info", aws or "not found", "install aws cli"),
    ]
    blockers = [c for c in checks if not c["ok"] and c["severity"] == "blocker"]
    next_aws = []
    if not posture.get("connected"):
        next_aws.append("Authenticate (aws sso login) to run the real BCM estimate and gated apply.")
    elif posture.get("type") in ("long_term", "root"):
        next_aws.append(f"Use a temporary session (SSO / assumed MFA role) — current is {posture.get('type')}; apply refuses static keys.")
    next_aws += [
        "Real per-service cost: bcm prepare --derive ... then bcm run (BCM prices it).",
        "Real deploy: plan_gate verify -> plan -> approve -> apply against this run's terraform dir.",
    ]
    evidence = {
        "run": run.get("run_id"),
        "offline_chain_proven": not blockers,
        "readiness": readiness["status"], "readiness_score": readiness["score"],
        "audit_chain_ok": audit_ok,
        "audit_chained_records": chain["chained_count"], "audit_legacy_records": chain["legacy_count"],
        "terraform_available": bool(terraform), "aws_available": bool(aws),
        "aws_connected": bool(posture.get("connected")), "credential_type": posture.get("type"),
        "checks": checks, "blockers": blockers, "next_aws_steps": next_aws,
    }
    root = Path(run["root"])
    (root / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# MinusOps Evidence Bundle", "",
        f"- Run: `{run.get('run_id')}`",
        f"- Offline governance chain proven: **{not blockers}**",
        f"- Readiness: **{readiness['status']}** ({readiness['score']}/100)",
        f"- Audit chain intact: **{audit_ok}**",
        f"- Terraform: {bool(terraform)} · AWS CLI: {bool(aws)} · AWS connected: "
        f"{bool(posture.get('connected'))} ({posture.get('type')})",
        "", "## Checks", "",
    ]
    for c in checks:
        lines.append(f"- {'OK' if c['ok'] else c['severity'].upper()}: {c['name']} ({c['detail']})")
    lines += ["", "## Remaining AWS-gated steps", ""] + [f"- {s}" for s in next_aws]
    (root / "evidence.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    evidence["paths"] = {"markdown": str(root / "evidence.md"), "json": str(root / "evidence.json")}
    return evidence


def _rich(parser, examples, requires=(), produces=(), next_step=""):
    """Attach a copy-pasteable epilog to a subcommand parser."""
    parser.epilog = cli_diagnostics.epilog(examples, requires, produces, next_step)
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    return parser


def main(argv=None):
    ap = argparse.ArgumentParser(description="MinusOps safe operator CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create", help="resolve request and create a requirements-first run")
    _rich(create,
          [f'minusctl create "governed lakehouse for 100 GB/day of clickstream"',
           f'minusctl create "nightly finance ETL" --json'],
          requires=("nothing -- this is step 1 of the lifecycle",),
          produces=("runs/<id>/requirements.json", "runs/<id>/run.json"),
          next_step=f"answer the REVIEW fields, then minusctl decision template --write")
    create.add_argument("request")
    create.add_argument("--cloud", default=None)
    create.add_argument("--name", help="workload name; produces a semantic run id "
                                       "<domain>-<name>-<orchestrator>_<timestamp>")
    create.add_argument("--domain", help="owning data domain, e.g. marketing")
    create.add_argument("--orchestrator", help="orchestrator id, e.g. mwaa")
    create.add_argument("--owner", help="owning team contact, recorded in runs/INDEX.md")
    create.add_argument("--input", action="append", default=[], help="Captured request input as name=value")
    create.add_argument("--generate", action="store_true", help="Compatibility flag; generation is blocked until requirements and architecture decision are complete")
    create.add_argument("--json", action="store_true")

    export_cmd = sub.add_parser(
        "export", help="package a run into a domain repository (local file copy only)")
    _rich(export_cmd,
          ['minusctl export --run marketing-clickstream-mwaa_20260822_111530 '
           '--target-repo ../marketing-analytics --dest-dir pipelines/clickstream --generate-workflow'],
          requires=("a synthesized run with generated Terraform",),
          produces=("<target-repo>/<dest-dir>/{terraform,dags,scripts,configs}",
                    "<target-repo>/.github/workflows/<pipeline>-deploy.yml (with --generate-workflow)"),
          next_step="review the diff in the domain repo, commit, and open a PR")
    export_cmd.add_argument("--run", default="latest")
    export_cmd.add_argument("--target-repo", required=True)
    export_cmd.add_argument("--dest-dir", help="e.g. pipelines/clickstream")
    export_cmd.add_argument("--pipeline-name", help="defaults to the last segment of --dest-dir")
    export_cmd.add_argument("--generate-workflow", action="store_true")
    export_cmd.add_argument("--artifact-repo", choices=cicd_engine.ARTIFACT_REPOS,
                            help="publish an immutable commit-tagged artifact before apply")
    export_cmd.add_argument("--region", default="us-east-1")
    export_cmd.add_argument("--json", action="store_true")

    diagnose_cmd = sub.add_parser(
        "diagnose", help="explain a failure: evidence, root cause, options, next command")
    _rich(diagnose_cmd,
          ['minusctl diagnose --run <run-id>',
           'minusctl diagnose --error "Container killed by YARN..."'],
          requires=("a failure -- from --error text, or extracted from the run's local "
                    "artifacts",),
          produces=("a four-part resolution report on stdout",),
          next_step="pick an option and run the command it names")
    diagnose_cmd.add_argument("--run", default=None,
                              help="run workspace to pull local failure evidence from")
    diagnose_cmd.add_argument("--error", default="", help="raw error text")
    diagnose_cmd.add_argument("--address", default=None, help="e.g. aws_glue_job.etl")
    diagnose_cmd.add_argument("--resource-type", default=None)
    diagnose_cmd.add_argument("--with-telemetry", action="store_true",
                              help="ask CloudTrail/Glue about the failing resource "
                                   "(read-only, fail-open)")
    diagnose_cmd.add_argument(
        "--tier", choices=("0", "1", "2", "3"), default=None,
        help="asset criticality tier of the affected data (0=mission critical). "
             "Defaults to the run's declared tier; without either, severity is "
             "reported UNCLASSIFIED rather than guessed")
    diagnose_cmd.add_argument(
        "--pii", action="store_true",
        help="regulated data or its masking controls are involved (forces P1)")
    diagnose_cmd.add_argument(
        "--stakeholder-detected", action="store_true",
        help="a stakeholder noticed before the monitors did (raises severity)")
    diagnose_cmd.add_argument("--json", action="store_true")

    policy = sub.add_parser("policy", help="inspect or promote policy rules")
    policy.add_argument("action", choices=["list", "promote", "demote"])
    policy.add_argument("rule_id", nargs="?", help="e.g. SEC-01")
    policy.add_argument("--by", default="", help="who is promoting/demoting (required)")
    policy.add_argument("--reason", default="", help="what you actually reviewed (required)")
    policy.add_argument("--json", action="store_true")

    run_cmd = sub.add_parser("runs", help="list or show run workspaces")
    run_cmd.add_argument("action", choices=["list", "latest"])
    run_cmd.add_argument("--json", action="store_true")

    guard = sub.add_parser("guard", help="inspect generated source drift")
    guard.add_argument("action", choices=["status", "diff", "baseline", "refresh"])
    guard.add_argument("--dir", help="Terraform source directory")
    guard.add_argument("--run", default="latest", help="Run id or prefix; default latest")
    guard.add_argument("--label", default="manual", help="Baseline label for baseline/refresh")
    guard.add_argument("--ack-manual-edits", default="",
                       help="REQUIRED for refresh: who reviewed the manual edits and why they are "
                            "correct — recorded in the tamper-evident audit chain")
    guard.add_argument("--json", action="store_true")

    reports = sub.add_parser("reports", help="inspect plan reports")
    reports.add_argument("action", choices=["list", "status", "services", "resources", "roles", "files", "diff"])
    reports.add_argument("--report", help="Report hash prefix")
    reports.add_argument("--latest", action="store_true")

    nxt = sub.add_parser("next", help="print safe next steps for a run")
    _rich(nxt,
          [f"minusctl next", f"minusctl next --run 20260818-085523 --json"],
          requires=("runs/<id>/run.json",),
          produces=("nothing -- read-only",),
          next_step="whichever command it prints")
    nxt.add_argument("--run", default="latest")
    nxt.add_argument("--json", action="store_true")

    pkg = sub.add_parser("package", help="write an enterprise handoff package for a run")
    _rich(pkg,
          [f"minusctl package --run 20260818-085523"],
          requires=("runs/<id>/terraform/", "at least one report under runs/<id>/reports/"),
          produces=("runs/<id>/handoff.md", "runs/<id>/handoff.json"),
          next_step="share the package; it is evidence, not an approval")
    pkg.add_argument("--run", default="latest")
    pkg.add_argument("--json", action="store_true")

    ready = sub.add_parser("readiness", help="score enterprise presentation readiness for a run")
    _rich(ready,
          [f"minusctl readiness --run 20260818-085523",
           f"minusctl readiness --strict   # exit 2 unless READY"],
          requires=("runs/<id>/terraform/",),
          produces=("nothing -- read-only",),
          next_step="minusctl gate verify --dir runs/<id>/terraform")
    ready.add_argument("--run", default="latest")
    ready.add_argument("--json", action="store_true")
    ready.add_argument("--strict", action="store_true", help="Exit non-zero unless status is READY")

    conf = sub.add_parser("conformance", help="score a run against the analytics reference architecture + Well-Architected Lens")
    conf.add_argument("--run", default="latest")
    conf.add_argument("--json", action="store_true")
    conf.add_argument("--strict", action="store_true", help="Exit non-zero unless status is READY")

    val = sub.add_parser("validate", help="offline `terraform validate` — non-mutating, credential-free correctness check")
    val.add_argument("--run", default="latest")
    val.add_argument("--json", action="store_true")

    decision_cmd = sub.add_parser("decision", help="manage the architecture decision record for a run")
    decision_cmd.add_argument("action", choices=["template", "check"])
    decision_cmd.add_argument("--run", default="latest")
    decision_cmd.add_argument("--write", action="store_true", help="write template to the run as architecture_decision.json")
    decision_cmd.add_argument("--force", action="store_true", help="overwrite an existing architecture_decision.json with --write")
    decision_cmd.add_argument("--json", action="store_true")

    accelerator_cmd = sub.add_parser("accelerator", help="write reviewable accelerator artifacts for a run")
    accelerator_cmd.add_argument("name", choices=["aws-lakehouse"])
    accelerator_cmd.add_argument("--run", default="latest")
    accelerator_cmd.add_argument("--owner", default="data-platform")
    accelerator_cmd.add_argument("--daily-data-gb", type=float, default=100)
    accelerator_cmd.add_argument("--streaming", action="store_true")
    accelerator_cmd.add_argument("--force", action="store_true")
    accelerator_cmd.add_argument("--json", action="store_true")

    prove_cmd = sub.add_parser("prove", help="run the end-to-end evidence harness for a run")
    prove_cmd.add_argument("--run", default="latest")
    prove_cmd.add_argument("--json", action="store_true")
    # Without --execute this stays the OFFLINE governance-chain evidence bundle it has
    # always been. With it, the same command proves the live data path across five hops --
    # the only mutating thing here, routed through approval.py like `seed`.
    prove_cmd.add_argument("--execute", action="store_true",
                           help="run the live 5-hop data proof against the applied stack "
                                "(mutating; routes through approval.py)")
    prove_cmd.add_argument("--fixture", default=None, help="synthetic records to inject")
    prove_cmd.add_argument(
        "--hops",
        help="comma-separated subset of the hop catalog (default: the full five-hop "
             "proof). Available: " + ", ".join(sorted(seed_engine.HOPS)))
    prove_cmd.add_argument("--table", default="customer_gold")
    prove_cmd.add_argument("--records", type=int, default=1000,
                           help="records the fixture injects; hop 4 proves none were lost")
    prove_cmd.add_argument("--malformed", type=int, default=0,
                           help="how many of those records are deliberately invalid")
    prove_cmd.add_argument("--plan-hash", default=None,
                           help="bind the report to a plan hash; reports/<hash>/proving_report.json")
    prove_cmd.add_argument("--approval-mode", default="gatekeeper",
                           choices=["gatekeeper", "auto-approve"])

    audit_cmd = sub.add_parser("audit", help="verify the tamper-evident audit chain")
    _rich(audit_cmd,
          [f"minusctl audit", f"minusctl audit --json"],
          requires=(".agents/logs/audit.jsonl",),
          produces=("nothing -- read-only",),
          next_step="investigate any break; a broken chain is evidence, not noise")
    audit_cmd.add_argument("action", choices=["verify"])
    audit_cmd.add_argument("--path", default=os.path.join(os.getcwd(), ".agents", "logs", "audit.jsonl"))
    audit_cmd.add_argument("--json", action="store_true")

    demo_cmd = sub.add_parser("demo", help="generate a no-cloud demo run/report")
    demo_cmd.add_argument("name", choices=["governed-data-pipeline"])
    demo_cmd.add_argument("--owner", default="data-platform")
    demo_cmd.add_argument("--daily-data-gb", type=float, default=50)
    demo_cmd.add_argument("--json", action="store_true")

    doctor_cmd = sub.add_parser("doctor", help="diagnose the local environment (cross-platform)")
    _rich(doctor_cmd,
          [f"minusctl doctor", f"minusctl doctor --json", f"minusctl doctor --fix"],
          requires=("nothing -- run this first on a new machine",),
          produces=("nothing, unless --fix starts a LocalStack container",),
          next_step=f'minusctl create "<what you want to build>"')
    doctor_cmd.add_argument("--json", action="store_true")
    doctor_cmd.add_argument("--fix", action="store_true",
                            help="attempt the repairs doctor knows how to make (MINUS-154). "
                                 "Today that is starting a LocalStack container for G9; it "
                                 "will never restart Docker Desktop, which would kill every "
                                 "other container on the machine.")

    adopt_cmd = sub.add_parser(
        "adopt", help="inventory + scan an existing Terraform directory and bring it under the gate")
    _rich(adopt_cmd,
          [f"minusctl adopt --dir infra/legacy",
           f"minusctl adopt --dir infra/legacy --anchor   # claims these files as reviewed"],
          requires=("any directory containing .tf files",),
          produces=("nothing, unless --anchor writes .minus/ inside the target",),
          next_step="minusctl gate verify --dir <dir> --policy-mode production")
    adopt_cmd.add_argument("--dir", required=True)
    adopt_cmd.add_argument("--anchor", action="store_true",
                           help="write the source baseline (the only write this makes)")
    adopt_cmd.add_argument("--label", default="adopted")
    adopt_cmd.add_argument("--json", action="store_true")

    seed_cmd = sub.add_parser(
        "seed", help="prove an APPLIED stack end to end: seed Bronze, run the job, query Gold")
    _rich(seed_cmd,
          [f"minusctl seed --run 20260818-085523            # plan only, sends nothing",
           f"minusctl seed --run 20260818-085523 --execute  # MUTATES AWS"],
          requires=("an APPLIED stack (terraform outputs must resolve)",
                    "runs/<id>/tests/fixtures/sample.json"),
          produces=("objects in Bronze, a Glue job run, one Athena query",),
          next_step="read the row count it reports; an empty Gold table is a failure")
    seed_cmd.add_argument("--run", default=None, help="run id (default: latest)")
    seed_cmd.add_argument("--dir", default=None, help="Terraform directory (overrides --run)")
    seed_cmd.add_argument("--fixture", default=None)
    seed_cmd.add_argument("--table", default="customer_gold")
    seed_cmd.add_argument("--execute", action="store_true",
                          help="perform the AWS side effects (routed through approval.py). "
                               "Without it, seed only prints the commands.")
    seed_cmd.add_argument("--approval-mode", default="gatekeeper",
                          choices=["gatekeeper", "auto-approve"])
    seed_cmd.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "doctor":
        result = doctor.diagnose()
        if args.fix:
            repairs = doctor.fix(result["checks"])
            for repair in repairs:
                mark = "[FIXED]" if repair["ok"] else "[FAILED]"
                print(f"{mark} {repair['check']}: {repair['detail']}")
            if any(r["ok"] for r in repairs):
                # Applying the returned env is the CLI's job, not the diagnostic's -- see the
                # note in doctor.fix(). Applied before re-diagnosing so the second report
                # reflects the repaired machine.
                for repair in repairs:
                    for key, value in (repair.get("env") or {}).items():
                        os.environ.setdefault(key, value)
                result = doctor.diagnose()
                print("export MINUS_G9_EMULATOR=localstack   "
                      "# (set for this process; your shell needs its own)")
            result["repairs"] = repairs
        _json_or_text(result, args.json, doctor.format_result(result))
        return 0 if result["ok"] else 1

    if args.cmd == "adopt":
        try:
            result = adopt_engine.adopt(args.dir, anchor=args.anchor, label=args.label)
        except NotADirectoryError as exc:
            raise SystemExit(str(exc))
        _json_or_text(result, args.json, adopt_engine.format_result(result))
        return 0 if result["ok"] else 1

    if args.cmd == "seed":
        # The one mutating command in this CLI, and only with --execute. Everything else here
        # is local-only by contract (see the module docstring), so the default stays a plan.
        tf_dir = args.dir or _run_by_id_or_latest(args.run, command=args.cmd)["terraform_dir"]
        fixture = args.fixture or os.path.join(
            os.path.dirname(os.path.abspath(tf_dir)), seed_engine.FIXTURE)
        try:
            result = seed_engine.seed(tf_dir, fixture, table=args.table, execute=args.execute,
                                      approval_mode=args.approval_mode)
        except seed_engine.SeedError as exc:
            raise SystemExit(str(exc))
        _json_or_text(result, args.json, seed_engine.format_result(result))
        return 0 if result["ok"] else 1

    if args.cmd == "policy":
        if args.action == "list":
            rules = rule_stages.list_rules()
            if args.json:
                print(json.dumps(rules, indent=2))
            else:
                if not rules:
                    print("no rules registered -- every rule defaults to warn-only")
                for rid, entry in rules.items():
                    stage = entry.get("stage", "warn")
                    who = entry.get("promoted_by") or entry.get("demoted_by") or "-"
                    mark = "BLOCKING" if stage == "blocking" else "warn    "
                    print(f"  {mark}  {rid:<10} {who}")
                print("")
                print("Only BLOCKING rules can stop an apply. Promote with:")
                print("  minusctl policy promote <RULE-ID> --by <you> --reason <what you checked>")
            return 0
        if not args.rule_id:
            raise SystemExit(f"policy {args.action}: a rule id is required")
        fn = rule_stages.promote if args.action == "promote" else rule_stages.demote
        kwargs = {"promoted_by": args.by} if args.action == "promote" else {"demoted_by": args.by}
        try:
            entry = fn(args.rule_id, reason=args.reason, **kwargs)
        except ValueError as exc:
            raise SystemExit(str(exc))
        print(json.dumps(entry, indent=2) if args.json
              else f"[policy] {args.rule_id} -> {entry['stage']} (by {args.by})")
        return 0

    if args.cmd == "create":
        record = workflow.resolve_to_run(
            args.request,
            cloud=args.cloud,
            inputs=workflow.parse_input(args.input),
            generate=args.generate,
            name=args.name, domain=args.domain, orchestrator=args.orchestrator,
            owner=args.owner,
        )
        _json_or_text(record, args.json, workflow.format_result(record))
        return 0 if record.get("ok") else 2

    if args.cmd == "export":
        import export as export_engine
        run = _run_by_id_or_latest(args.run, command="export")
        try:
            manifest = export_engine.export_run(
                run["root"], args.target_repo, dest_dir=args.dest_dir,
                generate_workflow=args.generate_workflow,
                pipeline_name=args.pipeline_name, region=args.region,
                artifact_repo=args.artifact_repo)
        except ValueError as exc:
            print(f"[ERR] {exc}", file=sys.stderr)
            return 1
        _json_or_text(manifest, args.json, export_engine.format_manifest(manifest))
        return 0

    if args.cmd == "diagnose":
        run_root = None
        tier = args.tier
        if args.run:
            record = _run_by_id_or_latest(args.run, command="diagnose")
            run_root = record["root"]
            # The run's DECLARED tier drives severity. --tier overrides it for a
            # one-off triage, but nothing infers a tier from the run's shape -- an
            # undeclared tier yields UNCLASSIFIED rather than a guess.
            tier = tier if tier is not None else record.get("tier")
        telemetry = None
        if args.with_telemetry:
            import cloud_drift
            telemetry = cloud_drift.aws_telemetry
        result = incident_diagnostics.diagnose(
            args.error, telemetry=telemetry, address=args.address,
            resource_type=args.resource_type, run_root=run_root, tier=tier,
            has_pii=args.pii, stakeholder_detected=args.stakeholder_detected)
        _json_or_text(result, args.json, incident_diagnostics.format_report(result))
        # Non-zero when nothing matched, so a CI step can tell "diagnosed" from "still
        # unknown" without parsing the report.
        return 0 if result["matched"] else 1

    if args.cmd == "runs":
        if args.action == "list":
            _print_runs(args.json)
            return 0
        run = _latest_run_or_exit()
        _json_or_text(run, args.json, _format_run(run))
        return 0

    if args.cmd == "guard":
        tf_dir = _terraform_dir(args)
        if args.action in {"baseline", "refresh"}:
            if args.action == "refresh":
                # Re-baselining blesses manual edits to GENERATED code. That must be an
                # explicit, attributable act: without the acknowledgment an agent can stamp
                # its own edits repeatedly, unchallenged.
                if not args.ack_manual_edits:
                    print("guard refresh re-baselines manual edits to generated Terraform. "
                          "State why: --ack-manual-edits \"<who reviewed the diff and why the "
                          "edits are correct>\". The acknowledgment lands in the audit chain.",
                          file=sys.stderr)
                    return 2
                changed = source_guard.status(tf_dir)
                try:
                    import approval as _approval
                    operator = _approval.authz.operator()
                except Exception:
                    operator = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
                audit_chain.append(
                    os.path.join(os.getcwd(), ".agents", "logs", "audit.jsonl"),
                    {
                        "action": "guard_refresh",
                        "operator": operator,
                        "ack": args.ack_manual_edits,
                        "label": args.label,
                        "dir": tf_dir,
                        "drift_before": changed.get("status"),
                        "changed_files": (changed.get("changed") or [])[:50],
                    })
                print(f"[guard] refresh acknowledged by {operator}: {args.ack_manual_edits}")
            result = source_guard.write_baseline(tf_dir, label=args.label)
            _json_or_text(result, args.json, f"baseline written: {tf_dir}")
        elif args.action == "status":
            result = source_guard.status(tf_dir)
            _json_or_text(result, args.json, json.dumps(result, indent=2))
        elif args.action == "diff":
            print("\n".join(source_guard.diff(tf_dir)))
        return 0

    if args.cmd == "reports":
        _print_report_command(args.action, args)
        return 0

    if args.cmd == "next":
        result = _next_steps(_run_by_id_or_latest(args.run, command=args.cmd))
        _json_or_text(result, args.json, result["text"])
        return 0

    if args.cmd == "package":
        result = _write_package(_run_by_id_or_latest(args.run, command=args.cmd))
        text = "\n".join([
            "Enterprise package written",
            f"markdown : {result['paths']['markdown']}",
            f"json     : {result['paths']['json']}",
        ])
        _json_or_text({k: v for k, v in result.items() if k != "markdown"}, args.json, text)
        return 0

    if args.cmd == "readiness":
        result = _readiness(_run_by_id_or_latest(args.run, command=args.cmd))
        _json_or_text(result, args.json, _format_readiness(result))
        if args.strict and result["status"] != "READY":
            return 2
        return 0

    if args.cmd == "validate":
        run = _run_by_id_or_latest(args.run, command=args.cmd)
        result = tf_validate.validate_and_record(run["terraform_dir"])
        _json_or_text(result, args.json, tf_validate._format(result))
        return 0 if (result.get("ok") or result.get("ok") is None) else 2

    if args.cmd == "conformance":
        run = _run_by_id_or_latest(args.run, command=args.cmd)
        report = _conformance_for_run(run)
        _json_or_text(report or {"error": "no plan to analyze"}, args.json, _format_conformance(report))
        if args.strict and (not report or report["status"] != "READY"):
            return 2
        return 0 if report else 2

    if args.cmd == "decision":
        run = _run_by_id_or_latest(args.run, command=args.cmd)
        workflow_record = _read_workflow(run)
        requirements_file = workflow_record.get("requirements_file") or str(Path(run["root"]) / reqgate.FILENAME)
        decision_path = Path(run["root"]) / archdec.FILENAME
        if args.action == "template":
            record = archdec.template(requirements_file=requirements_file)
            result = {"run": run, "path": str(decision_path), "record": record, "written": False}
            if args.write:
                if decision_path.exists() and not args.force:
                    result["error"] = "architecture_decision.json already exists; pass --force to overwrite"
                    _json_or_text(result, args.json, result["error"])
                    return 2
                archdec.write(run["root"], record)
                result["written"] = True
            text = json.dumps(record, indent=2) if not args.write else f"architecture decision template written: {decision_path}"
            _json_or_text(result, args.json, text)
            return 0
        data = archdec.load(str(decision_path))
        ok, missing = archdec.validate(data or {})
        result = {"run": run, "path": str(decision_path), "ok": ok, "missing": missing}
        if ok:
            _json_or_text(result, args.json, f"[architecture] complete: {decision_path}")
            return 0
        text = "[architecture] INCOMPLETE: " + str(decision_path) + "\n" + "\n".join(f"  - {item}" for item in missing)
        _json_or_text(result, args.json, text)
        return 2

    if args.cmd == "accelerator":
        run = _run_by_id_or_latest(args.run, command=args.cmd)
        try:
            if args.name == "aws-lakehouse":
                result = accelerators.write_lakehouse(
                    run,
                    owner=args.owner,
                    daily_data_gb=args.daily_data_gb,
                    streaming=args.streaming,
                    force=args.force,
                )
            else:
                raise SystemExit(f"unknown accelerator: {args.name}")
        except FileExistsError as exc:
            _json_or_text({"run": run, "error": str(exc)}, args.json, f"[accelerator] REFUSED: {exc}")
            return 2
        text = "\n".join([
            "[accelerator] reviewable aws-lakehouse artifacts written",
            f"requirements: {result['requirements_file']}",
            f"decision    : {result['decision_file']}",
            f"next        : {result['next']}",
        ])
        _json_or_text(result, args.json, text)
        return 0

    if args.cmd == "prove" and args.execute:
        run = _run_by_id_or_latest(args.run, command=args.cmd)
        fixture = args.fixture or os.path.join(
            os.path.dirname(os.path.abspath(run["terraform_dir"])), seed_engine.FIXTURE)
        try:
            result = seed_engine.prove_pipeline(
                run["terraform_dir"], fixture, table=args.table, execute=True,
                approval_mode=args.approval_mode, records=args.records,
                malformed=args.malformed, run_name=run["run_id"],
                plan_hash=args.plan_hash, reports_dir=run["reports_dir"],
                hops=[h for h in args.hops.split(",")] if args.hops else None)
        except seed_engine.SeedError as exc:
            raise SystemExit(str(exc))
        _json_or_text(result, args.json, seed_engine.format_proof(result))
        return 0 if result["ok"] else 1

    if args.cmd == "prove":
        result = _prove(_run_by_id_or_latest(args.run, command=args.cmd))
        text = "\n".join([
            "Evidence bundle written",
            f"offline chain proven : {result['offline_chain_proven']}",
            f"readiness            : {result['readiness']} ({result['readiness_score']}/100)",
            f"audit chain intact   : {result['audit_chain_ok']}",
            f"aws connected        : {result['aws_connected']} ({result['credential_type']})",
            f"markdown             : {result['paths']['markdown']}",
        ])
        _json_or_text(result, args.json, text)
        return 0 if result["offline_chain_proven"] else 2

    if args.cmd == "audit":
        ok, errors = audit_chain.verify(args.path)
        result = {"path": args.path, "ok": ok, "errors": errors}
        text = (f"[audit] chain OK: {args.path}" if ok
                else f"[audit] CHAIN INTEGRITY FAILURE: {args.path}\n" + "\n".join(f"  - {e}" for e in errors))
        _json_or_text(result, args.json, text)
        return 0 if ok else 1

    if args.cmd == "demo":
        result = demo.governed_data_pipeline(owner=args.owner, daily_data_gb=args.daily_data_gb)
        text = "\n".join([
            "[DEMO] governed AWS data pipeline",
            f"run       : {result['run']['run_id']}",
            f"terraform : {result['run']['terraform_dir']}",
            f"report    : {result['report_dir']}",
        ])
        _json_or_text(result, args.json, text)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
