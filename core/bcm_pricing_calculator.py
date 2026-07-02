"""
BCM Pricing Calculator integration.

The AWS BCM Pricing Calculator API creates workload estimate objects in AWS
Billing and Cost Management. That is an AWS-side effect, so this tool separates
safe preparation from gated execution:

  prepare -> writes reviewable JSON payloads, no AWS calls
  run     -> requires approval.py, creates the BCM estimate, adds usage, reads result

Usage:
  python core/bcm_pricing_calculator.py prepare --report-dir artifacts/reports/<hash> --account-id 123456789012
  python core/bcm_pricing_calculator.py prepare --report-dir artifacts/reports/<hash> --usage-profile pricing-profile.json
  python core/bcm_pricing_calculator.py run --report-dir artifacts/reports/<hash> --mode gatekeeper
"""
import argparse
import datetime
import json
import os
import re
import secrets
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval import request_approval  # noqa: E402
import toolpath  # noqa: E402

PLACEHOLDER = "REVIEW_REQUIRED"
USAGE_FIELDS = {
    "serviceCode",
    "usageType",
    "operation",
    "key",
    "group",
    "usageAccountId",
    "amount",
    "historicalUsage",
}


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _report_paths(report_dir):
    return {
        "manifest": os.path.join(report_dir, "manifest.json"),
        "plan": os.path.join(report_dir, "plan.json"),
        "usage": os.path.join(report_dir, "bcm-usage.json"),
        "assumptions": os.path.join(report_dir, "bcm-assumptions.json"),
        "create": os.path.join(report_dir, "bcm-create-workload-estimate.json"),
        "commands": os.path.join(report_dir, "bcm-commands.json"),
        "estimate": os.path.join(report_dir, "bcm-estimate.json"),
    }


def _resource_type(address, change):
    rtype = change.get("type")
    if rtype:
        return rtype
    parts = address.split(".")
    for part in parts:
        if part.startswith("aws_"):
            return part
    return address


def _action_summary(change):
    actions = change.get("change", {}).get("actions") or []
    return "+".join(actions) if actions else "unknown"


def _plan_inventory(plan):
    inventory = {}
    for change in plan.get("resource_changes", []):
        mode = change.get("mode", "managed")
        if mode != "managed":
            continue
        address = change.get("address", "")
        rtype = _resource_type(address, change)
        item = inventory.setdefault(rtype, {
            "count": 0,
            "addresses": [],
            "actions": {},
        })
        item["count"] += 1
        if address:
            item["addresses"].append(address)
        action = _action_summary(change)
        item["actions"][action] = item["actions"].get(action, 0) + 1
    return dict(sorted(inventory.items()))


def _usage_line_map(plan):
    mapping = {}
    for index, (rtype, info) in enumerate(_plan_inventory(plan).items(), start=1):
        key = f"U{index:06d}"
        mapping[key] = {
            "terraformResourceType": rtype,
            "terraformResourceCount": info["count"],
            "terraformAddresses": info["addresses"],
            "terraformActions": info["actions"],
        }
    return mapping


def _assumption_doc(plan, usage_profile=None):
    inventory = _plan_inventory(plan)
    profile_note = "not supplied"
    if usage_profile:
        profile_note = usage_profile.get("name") or usage_profile.get("id") or "supplied"
    return {
        "pricing_mode": "aws_bcm_pricing_calculator_review_required",
        "usage_profile": profile_note,
        "terraform_resource_inventory": inventory,
        "usage_line_map": _usage_line_map(plan),
        "review_required_fields": [
            "serviceCode",
            "usageType",
            "operation",
            "usageAccountId",
            "amount",
        ],
        "review_note": (
            "No prices, service mappings, or usage quantities are invented by this project. "
            "BCM usage lines are derived from Terraform resource inventory and remain "
            "REVIEW_REQUIRED until an owner supplies catalog-backed serviceCode, usageType, "
            "operation, account, and monthly amount values."
        ),
    }


def _load_usage_profile(path):
    if not path:
        return None
    profile = _load_json(path)
    if isinstance(profile, list):
        return {"name": os.path.basename(path), "usage": profile}
    if not isinstance(profile, dict):
        raise ValueError("usage profile must be a JSON object or usage array")
    return profile


def _profile_usage(profile):
    if not profile:
        return None
    usage = profile.get("usage")
    if usage is None:
        usage = profile.get("bcm_usage")
    if usage is None:
        return None
    if not isinstance(usage, list):
        raise ValueError("usage profile field 'usage' must be a list")
    return usage


def _bcm_group(region):
    cleaned = re.sub(r"[^a-zA-Z0-9-]", "-", f"tf-{region}")[:30]
    return cleaned.strip("-") or "tf"


# Stable AWS service identifiers (NOT prices, NOT region-specific) keyed by Terraform type.
_SERVICE_CODE = [
    ("aws_glue", "AWSGlue"), ("aws_s3", "AmazonS3"), ("aws_athena", "AmazonAthena"),
    ("aws_redshift", "AmazonRedshift"), ("aws_emr", "ElasticMapReduce"), ("aws_lambda", "AWSLambda"),
    ("aws_dynamodb", "AmazonDynamoDB"), ("aws_kms", "awskms"), ("aws_cloudwatch", "AmazonCloudWatch"),
    ("aws_sfn", "AWSStepFunctions"),
]

# Named, documented, OVERRIDABLE usage assumptions (NOT prices). Recorded in the report so a
# reviewer sees exactly what usage was assumed; override with --assume key=value.
DEFAULT_ASSUMPTIONS = {
    "glue_workers": 2, "glue_minutes_per_run": 10, "glue_runs_per_day": 24, "days_per_month": 30,
    "s3_storage_retention_factor": 30, "athena_queries_per_month": 150, "athena_avg_scan_gb": 15,
}


def _service_code(rtype):
    for prefix, code in _SERVICE_CODE:
        if rtype.startswith(prefix):
            return code
    return None


def _amount_for(service_code, inputs, A):
    """Monthly usage AMOUNT derived from blueprint inputs + assumptions — never a price."""
    daily_gb = float(inputs.get("daily_data_gb", 0) or 0)
    if service_code == "AWSGlue":
        return round(A["glue_workers"] * (A["glue_minutes_per_run"] / 60.0)
                     * A["glue_runs_per_day"] * A["days_per_month"], 2)        # DPU-hours/mo
    if service_code == "AmazonS3":
        # No daily volume input -> no honest amount; the line is skipped (recorded as
        # not-estimated) rather than submitted as a fabricated 0.
        return round(daily_gb * A["s3_storage_retention_factor"], 2) if daily_gb > 0 else None
    if service_code == "AmazonAthena":
        return round(A["athena_queries_per_month"] * A["athena_avg_scan_gb"] / 1024.0, 4)  # TB/mo
    return None


def derive_usage(plan, account_id, region, profile=None, assumptions=None):
    """
    Build one BCM usage line per AWS service in the plan, with the monthly AMOUNT derived
    from blueprint inputs + transparent assumptions. Catalog fields (usageType/operation)
    come from the reviewed profile when available, else stay REVIEW_REQUIRED. serviceCode is
    a stable AWS identifier. No prices are produced here — AWS BCM prices whatever is submitted.
    Returns (usage_lines, assumptions_used).
    """
    A = dict(DEFAULT_ASSUMPTIONS)
    A.update(assumptions or {})
    inputs = {k: (v or {}).get("value") for k, v in (plan.get("variables") or {}).items()}
    try:
        if float(inputs.get("daily_data_gb") or 0) > 0:
            # Recorded so downstream unit economics (cost/GB) can cite the exact volume used.
            A["daily_data_gb"] = float(inputs["daily_data_gb"])
    except (TypeError, ValueError):
        pass
    account = account_id or f"{PLACEHOLDER}_ACCOUNT_ID"
    catalog = {}
    for line in (_profile_usage(profile) or []):
        code = line.get("serviceCode")
        if code and code not in catalog:
            catalog[code] = line
    codes = []
    for rtype in _plan_inventory(plan):
        code = _service_code(rtype)
        if code and code not in codes:
            codes.append(code)
    usage = []
    for i, code in enumerate(codes, start=1):
        ref = catalog.get(code, {})
        amount = _amount_for(code, inputs, A)
        usage.append({
            "serviceCode": code,
            "usageType": ref.get("usageType", f"{PLACEHOLDER}_USAGE_TYPE"),
            "operation": ref.get("operation", f"{PLACEHOLDER}_OPERATION"),
            "key": f"U{i:06d}",
            "group": _bcm_group(region),
            "usageAccountId": account,
            "amount": amount if amount is not None else ref.get("amount", f"{PLACEHOLDER}_MONTHLY_AMOUNT"),
        })
    return usage, A


def build_usage(plan, account_id, region, usage_profile=None):
    """
    Build a conservative BCM usage draft from Terraform plan inventory.

    The BCM API requires serviceCode, usageType, operation, account, and amount.
    Those values are catalog/workload-specific. If a reviewed usage profile is
    supplied, it is used as-is. Otherwise every usage line stays explicitly
    REVIEW_REQUIRED, keyed by discovered Terraform resource type.
    """
    profiled = _profile_usage(usage_profile)
    if profiled is not None:
        return profiled
    account = account_id or f"{PLACEHOLDER}_ACCOUNT_ID"
    inventory = _plan_inventory(plan)
    if not inventory:
        inventory = {
            "terraform_resource": {
                "count": 0,
                "addresses": [],
                "actions": {"unknown": 0},
            }
        }
    usage = []
    for index, rtype in enumerate(inventory, start=1):
        usage.append({
            "serviceCode": f"{PLACEHOLDER}_SERVICE_CODE",
            "usageType": f"{PLACEHOLDER}_USAGE_TYPE",
            "operation": f"{PLACEHOLDER}_OPERATION",
            "key": f"U{index:06d}",
            "group": _bcm_group(region),
            "usageAccountId": account,
            "amount": f"{PLACEHOLDER}_MONTHLY_AMOUNT",
        })
    return usage


def _has_placeholder(value):
    if isinstance(value, str):
        return PLACEHOLDER in value
    if isinstance(value, dict):
        return any(_has_placeholder(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_placeholder(v) for v in value)
    return False


def validate_usage(usage):
    errors = []
    required = ("serviceCode", "usageType", "key", "usageAccountId", "amount")
    for i, entry in enumerate(usage):
        extra = sorted(set(entry) - USAGE_FIELDS)
        if extra:
            errors.append(f"usage[{i}] contains unsupported BCM fields: {', '.join(extra)}")
        for field in required:
            if field not in entry or entry[field] in ("", None):
                errors.append(f"usage[{i}].{field} is required")
        # operation must be present but MAY be empty — e.g. S3 standard storage bills
        # with an empty operation in the AWS Price List catalog.
        if "operation" not in entry or entry["operation"] is None:
            errors.append(f"usage[{i}].operation is required (may be an empty string)")
        key = entry.get("key")
        if key and not re.fullmatch(r"[a-zA-Z0-9]{1,10}", str(key)):
            errors.append(f"usage[{i}].key must be 1-10 alphanumeric characters")
        group = entry.get("group")
        if group and not re.fullmatch(r"[a-zA-Z0-9-]{0,30}", str(group)):
            errors.append(f"usage[{i}].group must be 0-30 alphanumeric/hyphen characters")
        amount = entry.get("amount")
        if not _has_placeholder(amount) and not isinstance(amount, (int, float)):
            errors.append(f"usage[{i}].amount must be numeric")
        if _has_placeholder(entry):
            errors.append(f"usage[{i}] still contains REVIEW_REQUIRED placeholders")
    return errors


def prepare(report_dir, account_id=None, region="us-east-1", rate_type="BEFORE_DISCOUNTS",
            usage_profile=None, derive=False, assumptions=None):
    paths = _report_paths(report_dir)
    if not os.path.exists(paths["plan"]):
        raise FileNotFoundError(f"missing plan.json: {paths['plan']}")
    plan = _load_json(paths["plan"])
    manifest = _load_json(paths["manifest"]) if os.path.exists(paths["manifest"]) else {}
    short = manifest.get("short") or os.path.basename(os.path.abspath(report_dir))
    name = f"{manifest.get('template', 'terraform-plan')}-{short}"
    usage_profile = _load_usage_profile(usage_profile) if isinstance(usage_profile, str) else usage_profile
    assumption_doc = _assumption_doc(plan, usage_profile)
    if derive:
        usage, used = derive_usage(plan, account_id, region, usage_profile, assumptions)
        assumption_doc["pricing_mode"] = "aws_bcm_amounts_derived_from_inputs_no_prices"
        assumption_doc["derived_amount_assumptions"] = used
        assumption_doc["amount_note"] = (
            "Monthly usage amounts were derived from this run's blueprint inputs and the "
            "assumptions above. NO prices are produced here — AWS BCM Pricing Calculator prices "
            "what is submitted. Verify usageType/operation against your region's catalog before publishing."
        )
    else:
        usage = build_usage(plan, account_id, region, usage_profile)
    create_payload = {
        "name": name[:128],
        "rateType": rate_type,
        "clientToken": f"minus-{short}-{secrets.token_hex(8)}",
        "tags": {
            "ManagedBy": "MinusTerraformCli",
            "PlanHash": short,
            "Purpose": "TerraformPlanCostEstimate",
        },
    }
    commands = {
        "note": "Review and replace REVIEW_REQUIRED fields in bcm-usage.json before run. Use AWS Pricing/BCM catalog evidence; this project does not hardcode service-specific prices or usage quantities.",
        "create_workload_estimate": "aws bcm-pricing-calculator create-workload-estimate --cli-input-json file://bcm-create-workload-estimate.json",
        "batch_create_usage": "aws bcm-pricing-calculator batch-create-workload-estimate-usage --cli-input-json file://<generated-with-workloadEstimateId>",
        "get_workload_estimate": "aws bcm-pricing-calculator get-workload-estimate --identifier <workload-estimate-id>",
        "list_usage": "aws bcm-pricing-calculator list-workload-estimate-usage --workload-estimate-id <workload-estimate-id>",
    }
    _write_json(paths["assumptions"], assumption_doc)
    _write_json(paths["usage"], usage)
    _write_json(paths["create"], create_payload)
    _write_json(paths["commands"], commands)
    return paths


def _aws_cli():
    exe = toolpath.find_tool("aws")
    if exe:
        return exe
    raise FileNotFoundError("aws CLI not found on PATH or in standard install locations")


def _run_json(cmd, cwd):
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip() or f"command failed: {cmd}")
    return json.loads(res.stdout or "{}")


def run(report_dir, mode="auto-approve"):
    """Create the workload estimate. Default mode is auto-approve (still audited + RBAC-
    checked): an estimate is a free, deletable BCM pricing object — not infrastructure.
    Human-in-the-loop approval remains the default for APPLY, not for pricing."""
    paths = _report_paths(report_dir)
    usage = _load_json(paths["usage"])
    errors = validate_usage(usage)
    if errors:
        raise RuntimeError("BCM usage payload is not ready:\n- " + "\n- ".join(errors))
    details = f"Create AWS BCM Pricing Calculator workload estimate using {paths['usage']}"
    if not request_approval("bcm-pricing-calculator-estimate", details, mode=mode):
        return False

    aws = _aws_cli()
    cwd = os.path.abspath(report_dir)
    created = _run_json([aws, "bcm-pricing-calculator", "create-workload-estimate",
                         "--cli-input-json", "file://bcm-create-workload-estimate.json"], cwd)
    estimate_id = created["id"]
    batch_payload = {"workloadEstimateId": estimate_id, "usage": usage}
    batch_path = os.path.join(report_dir, "bcm-batch-create-usage.json")
    _write_json(batch_path, batch_payload)
    batch = _run_json([aws, "bcm-pricing-calculator", "batch-create-workload-estimate-usage",
                       "--cli-input-json", "file://bcm-batch-create-usage.json"], cwd)
    estimate = _run_json([aws, "bcm-pricing-calculator", "get-workload-estimate",
                          "--identifier", estimate_id], cwd)
    # Per-service line items (each usage line with its AWS-computed cost) — the breakdown.
    usage_lines = _run_json([aws, "bcm-pricing-calculator", "list-workload-estimate-usage",
                             "--workload-estimate-id", estimate_id], cwd)
    result = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "create": created,
        "batch_create_usage": batch,
        "estimate": estimate,
        "usage_lines": usage_lines,
    }
    _write_json(paths["estimate"], result)

    # Refresh the cost report so cost.pdf shows the per-service forecast (best-effort).
    try:
        import reporter
        reporter.refresh_cost(os.path.abspath(report_dir))
    except Exception as exc:
        print(f"[bcm] estimate saved; cost report refresh skipped: {exc}", file=sys.stderr)
    return True


def _sts_account_id():
    """Account id from ambient credentials, or None. Quiet — used by the auto path."""
    try:
        aws = _aws_cli()
        res = subprocess.run([aws, "sts", "get-caller-identity", "--query", "Account",
                              "--output", "text"], capture_output=True, text=True, timeout=25)
        acct = (res.stdout or "").strip()
        return acct if res.returncode == 0 and re.fullmatch(r"\d{12}", acct) else None
    except Exception:
        return None


def _default_usage_profile():
    """The bundled example profile, CATALOG FIELDS ONLY (amounts stripped): its
    serviceCode/usageType/operation are Price-List-verified, but its quantities are
    illustrative — submitting them as real usage would fabricate a forecast."""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "examples", "bcm-usage-profile.example.json")
    if not os.path.exists(p):
        return None
    try:
        profile = _load_json(p)
        for line in profile.get("usage", []):
            line.pop("amount", None)
        return profile
    except Exception:
        return None


def auto_estimate(report_dir, region="us-east-1", usage_profile=None):
    """Create the BCM estimate automatically when credentials allow. Returns (ok, note).

    Never raises and never blocks: pricing needs no human gate (the estimate is a free,
    deletable BCM object; RBAC + audit still apply via request_approval). Honesty rules:
      * A usage file that already VALIDATES was reviewed — it is used as-is, never
        clobbered. Placeholder boilerplate is regenerated with derived amounts.
      * Amounts are derived from the run's inputs + recorded assumptions; catalog
        usageType/operation come from the reviewed profile. NO prices are produced here.
      * Only complete, catalog-backed lines are submitted; skipped services are recorded
        in bcm-assumptions.json as not_estimated_services — a partial estimate says so.
    Disable with MINUS_BCM_AUTO=0.
    """
    if os.environ.get("MINUS_BCM_AUTO", "1") == "0":
        return False, "disabled via MINUS_BCM_AUTO=0"
    paths = _report_paths(report_dir)
    if not os.path.exists(paths["plan"]):
        return False, "no plan.json in report dir"
    try:
        existing = _load_json(paths["usage"]) if os.path.exists(paths["usage"]) else None
        if not (existing and not validate_usage(existing)):
            account = _sts_account_id()
            if not account:
                return False, "no AWS credentials (sts get-caller-identity failed)"
            prepare(report_dir, account_id=account, region=region,
                    usage_profile=usage_profile or _default_usage_profile(), derive=True)
        usage = _load_json(paths["usage"])
        complete = [u for u in usage if not _has_placeholder(u)]
        skipped = sorted({str(u.get("serviceCode", "unknown")) for u in usage if _has_placeholder(u)})
        if not complete:
            return False, ("no catalog-backed usage lines — review bcm-usage.json or supply "
                           "--usage-profile with your region's serviceCode/usageType/operation")
        if skipped:
            _write_json(paths["usage"], complete)
            try:
                doc = _load_json(paths["assumptions"]) if os.path.exists(paths["assumptions"]) else {}
                doc["not_estimated_services"] = skipped
                doc["partial_estimate_note"] = (
                    "These services are in the plan but had no reviewed catalog identifiers, so "
                    "they are NOT included in the estimate total. Extend the usage profile to price them.")
                _write_json(paths["assumptions"], doc)
            except Exception:
                pass
        errors = validate_usage(complete)
        if errors:
            return False, "usage payload not ready: " + "; ".join(errors[:3])
        ok = run(report_dir, mode="auto-approve")
        if not ok:
            return False, "approver not authorized (RBAC)"
        note = "estimate created"
        if skipped:
            note += f" (partial — not estimated: {', '.join(skipped)})"
        return True, note
    except Exception as exc:
        return False, str(exc)


def scale_curve(report_dir, factors=(1, 5, 10)):
    """Price the SAME architecture at multiples of the declared usage — AWS prices every
    point (temporary workload estimates, deleted after reading), nothing is extrapolated
    locally. Writes bcm-scale-curve.json so the cost report can render the curve and
    diseconomies show up before deploy, not on the first big bill.
    """
    paths = _report_paths(report_dir)
    usage = _load_json(paths["usage"])
    errors = validate_usage(usage)
    if errors:
        raise RuntimeError("usage payload not ready for scale curve:\n- " + "\n- ".join(errors))
    base_estimate = _load_json(paths["estimate"]) if os.path.exists(paths["estimate"]) else {}
    base_total = None
    tc = (base_estimate.get("estimate") or {}).get("totalCost")
    if isinstance(tc, dict):
        tc = tc.get("amount")
    try:
        base_total = float(tc)
    except (TypeError, ValueError):
        pass

    aws = _aws_cli()
    cwd = os.path.abspath(report_dir)
    manifest = _load_json(paths["manifest"]) if os.path.exists(paths["manifest"]) else {}
    short = manifest.get("short") or os.path.basename(cwd)
    points = []
    for factor in factors:
        if factor == 1 and base_total is not None:
            points.append({"factor": 1, "total": base_total, "estimate_id": "(base estimate)"})
            continue
        scaled = []
        for u in usage:
            entry = dict(u)
            entry["amount"] = round(float(u["amount"]) * factor, 4)
            scaled.append(entry)
        create_payload = {
            "name": f"{manifest.get('template', 'terraform-plan')}-{short}-x{factor}"[:128],
            "rateType": "BEFORE_DISCOUNTS",
            "clientToken": f"minus-{short}-x{factor}-{secrets.token_hex(6)}",
            "tags": {"ManagedBy": "MinusTerraformCli", "PlanHash": short, "Purpose": "ScaleCurvePoint"},
        }
        tmp_create = os.path.join(report_dir, f"bcm-scale-x{factor}-create.json")
        _write_json(tmp_create, create_payload)
        created = _run_json([aws, "bcm-pricing-calculator", "create-workload-estimate",
                             "--cli-input-json", f"file://{os.path.basename(tmp_create)}"], cwd)
        est_id = created["id"]
        tmp_batch = os.path.join(report_dir, f"bcm-scale-x{factor}-usage.json")
        _write_json(tmp_batch, {"workloadEstimateId": est_id, "usage": scaled})
        try:
            _run_json([aws, "bcm-pricing-calculator", "batch-create-workload-estimate-usage",
                       "--cli-input-json", f"file://{os.path.basename(tmp_batch)}"], cwd)
            estimate = _run_json([aws, "bcm-pricing-calculator", "get-workload-estimate",
                                  "--identifier", est_id], cwd)
            points.append({"factor": factor,
                           "total": float(estimate.get("totalCost") or 0),
                           "estimate_id": est_id})
        finally:
            # Curve points are point-in-time reads — don't clutter Saved estimates.
            subprocess.run([aws, "bcm-pricing-calculator", "delete-workload-estimate",
                            "--identifier", est_id], cwd=cwd, capture_output=True, timeout=60)
            for tmp in (tmp_create, tmp_batch):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    result = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "rate_type": "BEFORE_DISCOUNTS",
        "points": sorted(points, key=lambda p: p["factor"]),
        "note": "Each point is an AWS BCM-priced estimate of the same usage x factor; "
                "no local extrapolation.",
    }
    _write_json(os.path.join(report_dir, "bcm-scale-curve.json"), result)
    try:
        import reporter
        reporter.refresh_cost(os.path.abspath(report_dir))
    except Exception as exc:
        print(f"[bcm] scale curve saved; cost report refresh skipped: {exc}", file=sys.stderr)
    return result


def _parse_assumptions(pairs):
    out = {}
    for item in pairs or []:
        if "=" not in item:
            raise ValueError(f"--assume must be key=value: {item}")
        k, v = item.split("=", 1)
        try:
            out[k.strip()] = float(v) if ("." in v) else int(v)
        except ValueError:
            out[k.strip()] = v.strip()
    return out


def _modifications(path, key):
    """Load a user-supplied modifications array (or {key:[...]}); never invented here."""
    data = _load_json(path)
    if isinstance(data, dict):
        return data.get(key) or data.get("modifications") or []
    return data if isinstance(data, list) else []


def run_bill_scenario(report_dir, usage_mods=None, commitments=None, mode="auto-approve", name=None):
    """
    Phase F — model commitments (Savings Plans / RIs) with a BCM Bill Scenario, then a Bill
    Estimate with per-service line items + commitment lines. Usage- and commitment-modification
    payloads are USER-SUPPLIED (generate with `aws bcm-pricing-calculator
    batch-create-bill-scenario-commitment-modification generate-cli-skeleton`), so nothing
    account/region/commitment-specific is invented. Gated + audited like every AWS-side effect.
    """
    paths = _report_paths(report_dir)
    manifest = _load_json(paths["manifest"]) if os.path.exists(paths["manifest"]) else {}
    short = manifest.get("short") or os.path.basename(os.path.abspath(report_dir))
    name = name or f"minus-scenario-{short}"
    if not request_approval("bcm-pricing-calculator-bill-scenario",
                            f"Create AWS BCM bill scenario + estimate (commitment modeling) for {short}",
                            mode=mode):
        return False
    aws = _aws_cli()
    cwd = os.path.abspath(report_dir)
    scenario = _run_json([aws, "bcm-pricing-calculator", "create-bill-scenario", "--name", name], cwd)
    sid = scenario["id"]
    out = {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "scenario": scenario}

    if usage_mods:
        _write_json(os.path.join(report_dir, "bcm-scenario-usage-input.json"),
                    {"billScenarioId": sid, "usageModifications": _modifications(usage_mods, "usageModifications")})
        out["usage_modifications"] = _run_json(
            [aws, "bcm-pricing-calculator", "batch-create-bill-scenario-usage-modification",
             "--cli-input-json", "file://bcm-scenario-usage-input.json"], cwd)
    if commitments:
        _write_json(os.path.join(report_dir, "bcm-scenario-commitment-input.json"),
                    {"billScenarioId": sid, "commitmentModifications": _modifications(commitments, "commitmentModifications")})
        out["commitment_modifications"] = _run_json(
            [aws, "bcm-pricing-calculator", "batch-create-bill-scenario-commitment-modification",
             "--cli-input-json", "file://bcm-scenario-commitment-input.json"], cwd)

    bill = _run_json([aws, "bcm-pricing-calculator", "create-bill-estimate",
                      "--bill-scenario-id", sid, "--name", name], cwd)
    bid = bill["id"]
    out["bill_estimate"] = bill
    out["line_items"] = _run_json([aws, "bcm-pricing-calculator", "list-bill-estimate-line-items",
                                   "--bill-estimate-id", bid], cwd)
    out["commitments"] = _run_json([aws, "bcm-pricing-calculator", "list-bill-estimate-commitments",
                                    "--bill-estimate-id", bid], cwd)
    _write_json(os.path.join(report_dir, "bcm-scenario-estimate.json"), out)
    try:
        import reporter
        reporter.refresh_cost(cwd)
    except Exception as exc:
        print(f"[bcm] scenario saved; cost report refresh skipped: {exc}", file=sys.stderr)
    return True


def fetch_actuals(report_dir, month=None, months_back=6):
    """
    Pull AWS Cost Explorer per-service actuals (read-only, no gate — Cost Explorer is read-only)
    and write them to bcm-actuals.json so the cost report renders a forecast-vs-actual variance
    table. Picks the requested month, else the most recent month that has spend. Returns the
    actuals dict ({service: amount}) or raises if Cost Explorer is unavailable.
    """
    import providers.base as pb
    provider = pb.get_provider("aws")
    data = provider.cost_by_service(months_back=months_back)
    if not data.get("ok"):
        raise RuntimeError(f"Cost Explorer unavailable: {data.get('error', 'unknown')}")
    months = [m for m in data.get("months", []) if m.get("by_service")]
    if not months:
        raise RuntimeError("Cost Explorer returned no per-service spend for the window.")
    chosen = None
    if month:
        chosen = next((m for m in months if m.get("month") == month), None)
        if chosen is None:
            raise RuntimeError(f"No Cost Explorer data for month {month}.")
    else:
        chosen = months[-1]
    actuals = dict(chosen["by_service"])
    out_path = os.path.join(report_dir, "bcm-actuals.json")
    _write_json(out_path, actuals)
    try:
        import reporter
        reporter.refresh_cost(os.path.abspath(report_dir))
    except Exception as exc:
        print(f"[bcm] actuals saved; cost report refresh skipped: {exc}", file=sys.stderr)
    return {"month": chosen.get("month"), "actuals": actuals, "path": out_path}


def main():
    ap = argparse.ArgumentParser(description="AWS BCM Pricing Calculator report integration")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="write reviewable BCM payload files; no AWS calls")
    p.add_argument("--report-dir", required=True)
    p.add_argument("--account-id", default="")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--rate-type", default="BEFORE_DISCOUNTS",
                   choices=["BEFORE_DISCOUNTS", "AFTER_DISCOUNTS", "AFTER_DISCOUNTS_AND_COMMITMENTS"])
    p.add_argument("--usage-profile", default="",
                   help="optional reviewed JSON profile containing BCM usage entries (catalog fields)")
    p.add_argument("--derive", action="store_true",
                   help="derive monthly usage amounts from blueprint inputs + assumptions (no prices)")
    p.add_argument("--assume", action="append", default=[],
                   help="override a usage assumption, e.g. --assume glue_runs_per_day=12")
    r = sub.add_parser("run", help="create the BCM workload estimate (free pricing object; audited)")
    r.add_argument("--report-dir", required=True)
    r.add_argument("--mode", default="auto-approve", choices=["gatekeeper", "auto-approve"],
                   help="estimates default to auto-approve; use gatekeeper to require a prompt")
    s = sub.add_parser("scenario", help="BCM bill scenario + estimate (Savings Plan / RI modeling)")
    s.add_argument("--report-dir", required=True)
    s.add_argument("--usage-modifications", default="",
                   help="user-supplied usageModifications JSON (from generate-cli-skeleton)")
    s.add_argument("--commitments", default="",
                   help="user-supplied commitmentModifications JSON (Savings Plans / RIs)")
    s.add_argument("--mode", default="auto-approve", choices=["gatekeeper", "auto-approve"],
                   help="estimates default to auto-approve; use gatekeeper to require a prompt")
    a = sub.add_parser("actuals", help="pull Cost Explorer per-service actuals for forecast-vs-actual (read-only)")
    a.add_argument("--report-dir", required=True)
    a.add_argument("--month", default="", help="YYYY-MM month to compare (default: most recent with spend)")
    sc = sub.add_parser("scale-curve", help="AWS-price the same architecture at 1x/5x/10x usage (temporary estimates)")
    sc.add_argument("--report-dir", required=True)
    sc.add_argument("--factors", default="1,5,10", help="comma-separated usage multipliers")
    args = ap.parse_args()

    if args.cmd == "prepare":
        paths = prepare(args.report_dir, args.account_id, args.region, args.rate_type,
                        args.usage_profile, derive=args.derive, assumptions=_parse_assumptions(args.assume))
        print("[bcm] prepared:")
        for key in ("assumptions", "create", "usage", "commands"):
            print(f"  {key}: {paths[key]}")
        return 0
    if args.cmd == "run":
        return 0 if run(args.report_dir, args.mode) else 1
    if args.cmd == "scenario":
        return 0 if run_bill_scenario(args.report_dir, args.usage_modifications or None,
                                      args.commitments or None, args.mode) else 1
    if args.cmd == "actuals":
        try:
            res = fetch_actuals(args.report_dir, args.month or None)
        except RuntimeError as exc:
            print(f"[bcm] {exc}", file=sys.stderr)
            return 1
        print(f"[bcm] actuals for {res['month']} written to {res['path']} ({len(res['actuals'])} services)")
        return 0
    if args.cmd == "scale-curve":
        try:
            factors = tuple(float(f) if "." in f else int(f) for f in args.factors.split(","))
            res = scale_curve(args.report_dir, factors=factors)
        except (RuntimeError, ValueError) as exc:
            print(f"[bcm] {exc}", file=sys.stderr)
            return 1
        for p in res["points"]:
            print(f"[bcm] x{p['factor']}: ${p['total']:,.2f}/mo")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
