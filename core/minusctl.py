"""
Operator-facing CLI for MinusOps.

This is a thin safe wrapper around the repo tools. It does not run Terraform,
cloud CLIs, or mutating commands. Commands either create local run files, inspect
local artifacts, or print the next safe command to run.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import architecture_decision as archdec  # noqa: E402
import architecture_model  # noqa: E402
import accelerators  # noqa: E402
import audit_chain  # noqa: E402
import demo  # noqa: E402
import plan_inspector  # noqa: E402
import requirements as reqgate  # noqa: E402
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
        raise SystemExit("no run workspaces found")
    return run


def _run_by_id_or_latest(run_id=None):
    if not run_id or run_id == "latest":
        return _latest_run_or_exit()
    for item in runs.list_runs():
        if item.get("run_id") == run_id or item.get("run_id", "").startswith(run_id):
            return item
    raise SystemExit(f"run not found: {run_id}")


def _terraform_dir(args):
    if getattr(args, "dir", None):
        return args.dir
    run = _run_by_id_or_latest(getattr(args, "run", None))
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
            "complete   : python core/requirements.py check " + requirements_file,
            "decide     : python core/minusctl.py decision template --write",
            "check arch : python core/architecture_decision.py check " + decision_file,
            "synthesize : python core/synthesizer.py \"<requirements summary>\" --run " + run["run_id"] + " --requirements-file " + requirements_file + " --decision-file " + decision_file,
            "blocked    : do not generate Terraform from demo fixtures for production",
        ])
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
        lines.append("review diff : python core/minusctl.py guard diff --run " + run["run_id"])
    if not reports:
        lines.extend([
            "verify     : python core/plan_gate.py verify --dir " + tf_dir,
            "prod verify: python core/plan_gate.py verify --dir " + tf_dir + " --policy-mode production",
            "plan       : python core/plan_gate.py plan --dir " + tf_dir,
            "reports    : none yet",
        ])
    else:
        lines.extend([
            "latest rpt : " + reports[0]["id"],
            "inspect    : python core/minusctl.py reports services --latest",
            "drift      : python core/minusctl.py reports diff --latest",
        ])
    lines.append("blocked    : do not apply until a reviewed plan hash is approved")
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
                "Run `python core/minusctl.py create \"<request>\"` to create a requirements-first run.",
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
                "Fill selected architecture, selected modules, alternatives, assumptions, risks, and sources.",
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
    # The workspace must contain REAL Terraform content — layout-agnostic (module
    # composition and flat blueprints are both legitimate), but an agent once passed the
    # old presence-only check with one-line comment stubs, so: (a) the root files must
    # collectively declare infrastructure + provider + variables, and (b) no root .tf
    # file may be a contentless stub (comments/blanks only).
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
            "Create a requirements-first run with `python core/minusctl.py create \"<request>\"`, then synthesize Terraform after architecture approval.",
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
            "Run `python core/minusctl.py guard baseline --run " + run["run_id"] + "` after reviewing the generated source.",
        ),
        _check(
            "source is current",
            source.get("status") == "CURRENT",
            "blocker",
            source.get("status", "UNKNOWN"),
            "Run `python core/minusctl.py guard diff --run " + run["run_id"] + "` and reconcile manual edits.",
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
            "Run `python core/plan_gate.py verify --dir <terraform-dir> --policy-mode production` then `python core/plan_gate.py plan --dir <terraform-dir>`.",
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
            "Run `python core/minusctl.py reports diff --latest` and regenerate the plan if files changed.",
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
            "`python core/bcm_pricing_calculator.py actuals --report-dir <report>` refreshes actuals.",
        ),
        _check(
            "terraform configuration valid",
            not ((_tf_validation := tf_validate.load(str(tf_dir))) and _tf_validation.get("ok") is False),
            "warning",
            ("valid" if (_tf_validation and _tf_validation.get("ok")) else
             "not recorded — run `validate`" if not _tf_validation else
             "terraform not installed" if _tf_validation.get("ok") is None else
             f"{_tf_validation.get('error_count', '?')} error(s)"),
            "Run `python core/minusctl.py validate --run " + run["run_id"] + "` (offline, no credentials).",
        ),
        _check(
            "data-pipeline requirements profile",
            (not reqgate.is_data_pipeline(requirements_data or {}))
            or reqgate.validate_data_pipeline(requirements_data or {})[0],
            "warning",
            (("complete" if reqgate.validate_data_pipeline(requirements_data or {})[0]
              else ", ".join(reqgate.validate_data_pipeline(requirements_data or {})[1]))
             if reqgate.is_data_pipeline(requirements_data or {}) else "n/a (not a data workload)"),
            "Gather the data-pipeline FR/NFR: `python core/requirements.py data-check "
            + str(requirements_file) + "` (or run grill-me).",
        ),
        _check(
            "reference-architecture conformance",
            bool(conformance) and conformance["score"] >= 90,
            "warning",
            (f"{conformance['score']}/100, {len(conformance['findings'])} finding(s)"
             if conformance else "no plan to analyze"),
            "Run `python core/minusctl.py conformance --run " + run["run_id"]
            + "` and address the reference / Well-Architected gaps.",
        ),
        _check(
            "safe package can be written",
            bool(run.get("root")) and Path(run["root"]).exists(),
            "info",
            run.get("root", "-"),
            "Run `python core/minusctl.py package --run " + run["run_id"] + "`.",
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
        lines.append("- No report exists yet. Run `python core/plan_gate.py plan --dir <terraform-dir>` after verification.")
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
        "- Do not run `terraform apply`, `terraform destroy`, mutating cloud CLI commands, or mutating git commands until the exact plan hash is reviewed and approved.",
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="MinusOps safe operator CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create", help="resolve request and create a requirements-first run")
    create.add_argument("request")
    create.add_argument("--cloud", default=None)
    create.add_argument("--input", action="append", default=[], help="Captured request input as name=value")
    create.add_argument("--generate", action="store_true", help="Compatibility flag; generation is blocked until requirements and architecture decision are complete")
    create.add_argument("--json", action="store_true")

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
    nxt.add_argument("--run", default="latest")
    nxt.add_argument("--json", action="store_true")

    pkg = sub.add_parser("package", help="write an enterprise handoff package for a run")
    pkg.add_argument("--run", default="latest")
    pkg.add_argument("--json", action="store_true")

    ready = sub.add_parser("readiness", help="score enterprise presentation readiness for a run")
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

    audit_cmd = sub.add_parser("audit", help="verify the tamper-evident audit chain")
    audit_cmd.add_argument("action", choices=["verify"])
    audit_cmd.add_argument("--path", default=os.path.join(os.getcwd(), ".agents", "logs", "audit.jsonl"))
    audit_cmd.add_argument("--json", action="store_true")

    demo_cmd = sub.add_parser("demo", help="generate a no-cloud demo run/report")
    demo_cmd.add_argument("name", choices=["governed-data-pipeline"])
    demo_cmd.add_argument("--owner", default="data-platform")
    demo_cmd.add_argument("--daily-data-gb", type=float, default=50)
    demo_cmd.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "create":
        record = workflow.resolve_to_run(
            args.request,
            cloud=args.cloud,
            inputs=workflow.parse_input(args.input),
            generate=args.generate,
        )
        _json_or_text(record, args.json, workflow.format_result(record))
        return 0 if record.get("ok") else 2

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
                # Re-baselining blesses manual edits to GENERATED code — that must be an
                # explicit, attributable act, not a rubber stamp (an agent once stamped
                # its own edits six times unchallenged).
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
        result = _next_steps(_run_by_id_or_latest(args.run))
        _json_or_text(result, args.json, result["text"])
        return 0

    if args.cmd == "package":
        result = _write_package(_run_by_id_or_latest(args.run))
        text = "\n".join([
            "Enterprise package written",
            f"markdown : {result['paths']['markdown']}",
            f"json     : {result['paths']['json']}",
        ])
        _json_or_text({k: v for k, v in result.items() if k != "markdown"}, args.json, text)
        return 0

    if args.cmd == "readiness":
        result = _readiness(_run_by_id_or_latest(args.run))
        _json_or_text(result, args.json, _format_readiness(result))
        if args.strict and result["status"] != "READY":
            return 2
        return 0

    if args.cmd == "validate":
        run = _run_by_id_or_latest(args.run)
        result = tf_validate.validate_and_record(run["terraform_dir"])
        _json_or_text(result, args.json, tf_validate._format(result))
        return 0 if (result.get("ok") or result.get("ok") is None) else 2

    if args.cmd == "conformance":
        run = _run_by_id_or_latest(args.run)
        report = _conformance_for_run(run)
        _json_or_text(report or {"error": "no plan to analyze"}, args.json, _format_conformance(report))
        if args.strict and (not report or report["status"] != "READY"):
            return 2
        return 0 if report else 2

    if args.cmd == "decision":
        run = _run_by_id_or_latest(args.run)
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
        run = _run_by_id_or_latest(args.run)
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

    if args.cmd == "prove":
        result = _prove(_run_by_id_or_latest(args.run))
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
