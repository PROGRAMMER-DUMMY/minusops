"""
Incident diagnostics: raw failure text in, a four-part resolution report out.

A Terraform apply error, a YARN OOM kill and a Great Expectations assertion failure are three
different opaque stack traces that all end with an engineer guessing. This turns each into
evidence, root cause, evaluated alternatives with their trade-offs, and the exact next command.

THREE THINGS IT REFUSES TO DO.

1. **Guess.** An unrecognised error returns `matched: False` with the raw evidence attached
   and no root cause. A confident wrong diagnosis is worse than none: the report looks
   authoritative, so the engineer follows it instead of reading the error.

2. **Invent a price.** PRD v9 NFR-03 asks for "verified AWS pricing rates (e.g. Glue DPU rate
   $0.44/DPU-hour)". That figure is a us-east-1 list price -- wrong in eu-west-1, wrong again
   after any repricing, and exactly the fabricated cost number
   `core/cost/budget_calculator.py` exists to refuse. What IS durable is the RATIO: a G.2X
   worker carries twice the DPUs of a G.1X in every region, forever, because that is what the
   instance class is. So options carry `cost_multiplier` (a structural fact) and describe the
   delta relatively, and the report points at `minusctl cost estimate` for dollars, which is
   the only path in this system that produces a reportable total. If the reviewed catalog ever
   gains a dated rate citation for a service, `_rate_citation()` surfaces it.

3. **Reach the network by default.** The offline path is pure regex over a string and makes
   no subprocess call at all -- this runs on a laptop with no credentials, mid-incident, and
   reaching for CloudWatch there is slower for no answer and fails exactly when it is needed.
   `--with-telemetry` injects a lookup, and a lookup that raises produces no evidence rather
   than an exception.

The rule table is declarative (Matt's ruling, 2026-08-22): a list of `FailureRule` dataclasses
rather than an if/elif chain, so adding a signature is a data edit and every rule is
independently testable. Order matters -- the first match wins -- so put specific patterns
above general ones.

Depends on: core/cost/pricing_catalog.py (lazily, only for an optional dated rate citation)
Shells out to: nothing. Telemetry is a caller-injected callable; this module never calls AWS.
Used by: core/reporting/minusctl.py (`minusctl next`), tests/test_incident_diagnostics.py
"""
import argparse
import dataclasses
import json
import os
import re
import sys

# What an option DOES, so a reader can sort by kind rather than reading all of them.
STRATEGIES = ("scaling", "optimization", "architectural_pivot", "configuration")

REPORT_WIDTH = 100
COST_EVIDENCE_COMMAND = "minusctl cost estimate"


@dataclasses.dataclass(frozen=True)
class RemediationOption:
    """One way out, with what it costs relative to today.

    `cost_multiplier` is a RATIO against current spend for the resource in question: 1.0 is
    free, 2.0 doubles it. Never a dollar figure -- see the module docstring on why.
    """
    title: str
    strategy: str
    description: str
    cost_delta: str
    action_command: str
    cost_multiplier: float = 1.0


@dataclasses.dataclass(frozen=True)
class FailureRule:
    rule_id: str
    pattern: re.Pattern
    category: str
    root_cause: str
    vulnerability: str
    options: tuple
    service_code: str = ""
    # Does this failure produce wrong-but-plausible output rather than an obvious stop?
    # Silent failures bump severity, because by the time anyone looks the bad numbers have
    # already been acted on. Defaults False: a loud crash is the safer assumption to make
    # about a rule nobody has classified.
    silent: bool = False
    # Does it touch regulated data or the controls protecting it? Forces P1.
    regulated: bool = False
    # A failure that resolves itself needs a ticket, not a pager.
    transient: bool = False


# Ordered: first match wins, so specific patterns sit above general ones.
FAILURE_RULES = [
    # --- Glue / Spark -----------------------------------------------------------------
    FailureRule(
        rule_id="GLUE-OOM-01",
        pattern=re.compile(
            r"(container killed by yarn|exceeding memory limits|outofmemoryerror|"
            r"java heap space)", re.I),
        category="Compute Memory Exhaustion (OutOfMemoryError)",
        root_cause="Executor memory exhausted, typically during a wide shuffle or join.",
        vulnerability=("The configured worker class holds less memory than the partition "
                       "batch being shuffled, so the executor spills and is then killed."),
        service_code="AWSGlue",
        options=(
            RemediationOption(
                title="Vertical scaling (no code change)",
                strategy="scaling",
                description=("Move worker_type G.1X -> G.2X. Twice the memory and twice the "
                             "DPUs per worker; the job code is untouched."),
                cost_delta="2x compute cost per worker-hour (G.2X carries 2x the DPUs of G.1X)",
                action_command='set worker_type = "G.2X" in modules/compute-glue-etl/main.tf, '
                               "then: minusctl gate plan",
                cost_multiplier=2.0,
            ),
            RemediationOption(
                title="Partition tuning (no additional spend)",
                strategy="optimization",
                description=("Repartition before the wide join so each executor holds a "
                             "smaller slice. Same workers, smaller working set."),
                cost_delta="no change to hourly rate; total cost falls if the job finishes sooner",
                action_command="add .repartition(N) before the join in scripts/etl.py, "
                               "then: minusctl gate plan",
                cost_multiplier=1.0,
            ),
            RemediationOption(
                title="Engine pivot to EMR Serverless",
                strategy="architectural_pivot",
                description=("Dynamic executor allocation instead of a fixed worker class. "
                             "Right when volume varies widely run to run."),
                cost_delta="billed per vCPU-second used rather than per allocated worker-hour; "
                           "direction depends on your utilisation",
                action_command="swap compute-glue-etl for compute-emr-serverless in the "
                               "architecture decision, then: minusctl gate plan",
                cost_multiplier=1.0,
            ),
        ),
    ),
    FailureRule(
        rule_id="GLUE-SKEW-01",
        pattern=re.compile(r"(shuffle fetch failed|executor heartbeat timed out|"
                           r"stage failure.*task .* failed 4 times)", re.I),
        category="Compute Skew / Straggler Timeout",
        root_cause="One partition is far larger than the others, so a single task outlives "
                   "the rest of the stage.",
        vulnerability="A skewed join key concentrates rows on one executor.",
        service_code="AWSGlue",
        options=(
            RemediationOption(
                title="Salt the skewed key (no additional spend)",
                strategy="optimization",
                description="Add a salt column to the join key to spread the hot partition.",
                cost_delta="no change to hourly rate",
                action_command="salt the join key in scripts/etl.py, then: minusctl gate plan",
                cost_multiplier=1.0,
            ),
            RemediationOption(
                title="Raise the executor timeout",
                strategy="configuration",
                description=("Buys the straggler time to finish. Treats the symptom -- the "
                             "skew is still there and will worsen with volume."),
                cost_delta="no change to hourly rate; longer runs cost more in total",
                action_command="raise timeout_minutes in modules/compute-glue-etl/main.tf, "
                               "then: minusctl gate plan",
                cost_multiplier=1.0,
            ),
        ),
    ),
    # --- Terraform apply ---------------------------------------------------------------
    FailureRule(
        rule_id="TF-IAM-CONSISTENCY-01",
        transient=True,
        pattern=re.compile(r"(has been propagated|invalidinputexception.*role|"
                           r"is not authorized to perform: iam:passrole|"
                           r"invalidparameterexception.*role)", re.I),
        category="IAM Eventual Consistency",
        root_cause="The role was created moments earlier and has not propagated to the "
                   "service that is being asked to assume it.",
        vulnerability=("The apply creates the role and the resource that uses it in the same "
                       "graph, with nothing forcing a settle window between them."),
        options=(
            RemediationOption(
                title="Re-run the apply (no additional spend)",
                strategy="optimization",
                description=("Propagation is usually seconds. The plan is hash-bound, so "
                             "re-applying the SAME approved plan is safe and changes nothing "
                             "about what will be created."),
                cost_delta="no change",
                action_command="minusctl gate apply",
                cost_multiplier=1.0,
            ),
            RemediationOption(
                title="Order the dependency explicitly",
                strategy="configuration",
                description=("Add depends_on from the consuming resource to the role so "
                             "Terraform stops racing propagation on every fresh apply."),
                cost_delta="no change",
                action_command="add depends_on to the consuming resource, then: "
                               "minusctl gate plan",
                cost_multiplier=1.0,
            ),
        ),
    ),
    FailureRule(
        rule_id="TF-S3-NAME-01",
        pattern=re.compile(r"(bucketalreadyexists|bucketalreadyownedbyyou|"
                           r"bucket namespace)", re.I),
        category="S3 Global Namespace Collision",
        root_cause="The bucket name is already taken -- S3 names are global across all AWS "
                   "accounts, not scoped to yours.",
        vulnerability="A name built from a prefix alone collides with an unrelated bucket, or "
                      "with a previous run sharing the prefix.",
        options=(
            RemediationOption(
                title="Add the account and run suffix (no additional spend)",
                strategy="configuration",
                description=("The pattern the other modules already use: account id plus a "
                             "run_id hash, which makes the name unique in both directions."),
                cost_delta="no change",
                action_command="add the account_id + run_id suffix to the bucket name, "
                               "then: minusctl gate plan",
                cost_multiplier=1.0,
            ),
            RemediationOption(
                title="Import the existing bucket",
                strategy="configuration",
                description=("Right only if the bucket is genuinely yours and holds data you "
                             "intend to keep. Verify before importing -- an import binds "
                             "Terraform to a bucket it can later destroy."),
                cost_delta="no change",
                action_command="minusctl adopt --dir <terraform-dir>",
                cost_multiplier=1.0,
            ),
        ),
    ),
    FailureRule(
        rule_id="TF-QUOTA-01",
        pattern=re.compile(r"(vcpulimitexceeded|limitexceeded|quota.*exceeded|"
                           r"maxnumberofrules)", re.I),
        category="Service Quota Exhausted",
        root_cause="The account's quota for this resource is already consumed.",
        vulnerability="The architecture assumes headroom the account does not have.",
        options=(
            RemediationOption(
                title="Request a quota increase",
                strategy="configuration",
                description=("The quota itself is free; what it permits is not. Approval is "
                             "usually hours, sometimes days -- start it now."),
                cost_delta="no change to rates; raises the ceiling on what can be spent",
                action_command="request the increase in Service Quotas, then: "
                               "minusctl gate apply",
                cost_multiplier=1.0,
            ),
            RemediationOption(
                title="Reduce the requested footprint (no additional spend)",
                strategy="optimization",
                description="Lower the instance or node count to fit the existing quota.",
                cost_delta="lower than the plan that failed",
                action_command="reduce the count in the module inputs, then: "
                               "minusctl gate plan",
                cost_multiplier=1.0,
            ),
        ),
    ),
    # --- Athena / Trino ----------------------------------------------------------------
    FailureRule(
        rule_id="ATHENA-SPLIT-01",
        silent=True,
        pattern=re.compile(r"(hive_cannot_open_split|hive_bad_data|"
                           r"hive_partition_schema_mismatch)", re.I),
        category="Athena Split / Schema Mismatch",
        root_cause="Athena could not read an object the catalog says belongs to the table.",
        vulnerability=("The table schema and the files on S3 disagree -- usually a writer "
                       "changed the layout without the catalog following."),
        options=(
            RemediationOption(
                title="Realign the catalog to the files (no additional spend)",
                strategy="optimization",
                description=("Correct the column types or the projection format so the table "
                             "describes what is actually written."),
                cost_delta="no change",
                action_command="fix projection_date_format / columns in "
                               "modules/query-athena, then: minusctl gate plan",
                cost_multiplier=1.0,
            ),
            RemediationOption(
                title="Quarantine the unreadable objects",
                strategy="configuration",
                description=("Move the malformed files out of the table prefix so the rest "
                             "of the partition stays queryable."),
                cost_delta="no change",
                action_command="move the bad objects to the quarantine bucket, then re-run "
                               "the query",
                cost_multiplier=1.0,
            ),
        ),
    ),
    FailureRule(
        rule_id="ATHENA-TIMEOUT-01",
        pattern=re.compile(r"(query timeout|query exhausted resources|"
                           r"query_exceeded_time_limit)", re.I),
        category="Athena Query Timeout / Resource Exhaustion",
        root_cause="The query exceeded the workgroup's limit before returning.",
        vulnerability="A full scan of an unpartitioned or unprojected table.",
        options=(
            RemediationOption(
                title="Partition and project the table (no additional spend)",
                strategy="optimization",
                description=("Partition projection resolves partitions in memory and prunes "
                             "the scan. Lowers both runtime and bytes billed."),
                cost_delta="lower than today -- Athena bills by bytes scanned",
                action_command="set create_projected_table = true in modules/query-athena, "
                               "then: minusctl gate plan",
                cost_multiplier=1.0,
            ),
            RemediationOption(
                title="Move the workload to the warehouse",
                strategy="architectural_pivot",
                description=("Redshift Serverless suits sustained high-concurrency BI that "
                             "Athena's per-query model handles poorly."),
                cost_delta="adds a warehouse to the footprint; bounded by max_capacity and "
                           "the usage limit",
                action_command="add consumption-redshift-serverless to the architecture "
                               "decision, then: minusctl gate plan",
                cost_multiplier=1.0,
            ),
        ),
    ),
    # --- Proving harness / data quality -------------------------------------------------
    FailureRule(
        rule_id="DQ-EXPECTATION-01",
        silent=True,
        pattern=re.compile(r"(great expectations.*fail|expectations did not pass|"
                           r"expectation.*failed|assertions_failed)", re.I),
        category="Data Quality Assertion Failure",
        root_cause="The suite ran and reported failing expectations -- a working check "
                   "describing broken data.",
        vulnerability=("An upstream producer changed the data without the contract "
                       "changing, or the contract was wrong to begin with."),
        options=(
            RemediationOption(
                title="Fix the upstream producer (no additional spend)",
                strategy="optimization",
                description=("The right fix when the expectation is correct and the data is "
                             "wrong. Everything else hides the defect."),
                cost_delta="no change",
                action_command="correct the producer, then: minusctl prove --execute",
                cost_multiplier=1.0,
            ),
            RemediationOption(
                title="Amend the expectation",
                strategy="configuration",
                description=("Right only when the data is correct and the contract was "
                             "wrong. Relaxing a suite to make a pipeline green is how a data "
                             "quality gate becomes decorative."),
                cost_delta="no change",
                action_command="amend the suite in modules/dq-great-expectations, then: "
                               "minusctl prove --execute",
                cost_multiplier=1.0,
            ),
        ),
    ),
    FailureRule(
        rule_id="DQ-QUARANTINE-01",
        silent=True,
        pattern=re.compile(r"(quarantine.*(exceed|threshold|spillover)|"
                           r"unaccounted for|records? (were )?lost)", re.I),
        category="Quarantine Threshold / Data Loss",
        root_cause="More records were diverted or dropped than the pipeline tolerates.",
        vulnerability=("Either the producer's quality fell sharply, or the transform is "
                       "dropping rows instead of quarantining them."),
        options=(
            RemediationOption(
                title="Trace the missing records (no additional spend)",
                strategy="optimization",
                description=("Injected must equal Gold plus quarantined. A gap means rows "
                             "vanished silently, which is a transform bug, not a data issue."),
                cost_delta="no change",
                action_command="minusctl prove --execute --records N --malformed M",
                cost_multiplier=1.0,
            ),
            RemediationOption(
                title="Raise the quarantine tolerance",
                strategy="configuration",
                description=("Only after the gap is explained. Raising it first converts a "
                             "loud failure into silent data loss."),
                cost_delta="no change",
                action_command="raise the threshold in the requirements, then: "
                               "minusctl prove --execute",
                cost_multiplier=1.0,
            ),
        ),
    ),
]

# Where local failure text is looked for, in order. A proving report is the most specific
# (it names the hop that failed), so it is checked first.
_LOG_CANDIDATES = ("terraform.log", "apply.log", "error.log")


def _rate_citation(service_code):
    """A reviewed, dated AWS Price List fact for this service, or None.

    Lazy import and a swallowed failure on purpose: an absent or unreadable catalog must
    degrade the report by one line, never fail the diagnosis someone is reading mid-incident.
    """
    if not service_code:
        return None
    try:
        import pricing_catalog
        return pricing_catalog.rate_citation_for_service_code(service_code)
    except Exception:  # noqa: BLE001
        return None


def _text(value):
    return value if isinstance(value, str) else ""


def extract_evidence(run_root):
    """Local failure text for a run, without touching the network.

    Returns {"raw_error", "source", "address", "resource_type"}. A run with nothing wrong
    yields an empty `raw_error` -- diagnosing a green result would manufacture an incident.
    """
    empty = {"raw_error": "", "source": None, "address": None, "resource_type": None}
    if not run_root or not os.path.isdir(run_root):
        return empty

    # A proving report names the hop that failed, which is more specific than a log tail.
    reports_dir = os.path.join(run_root, "reports")
    for base, _dirs, files in os.walk(reports_dir):
        if "proving_report.json" not in files:
            continue
        path = os.path.join(base, "proving_report.json")
        try:
            with open(path, encoding="utf-8") as f:
                report = json.load(f)
        except (OSError, ValueError):
            continue
        for hop in report.get("hops") or []:
            if isinstance(hop, dict) and hop.get("status") not in (None, "PASS"):
                return {"raw_error": _text(hop.get("detail")), "source": path,
                        "address": hop.get("job_name") or hop.get("name"),
                        "resource_type": "aws_glue_job" if "glue" in _text(
                            hop.get("name")) else None}

    for name in _LOG_CANDIDATES:
        path = os.path.join(run_root, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        if "error" in text.lower():
            return {"raw_error": text.strip(), "source": path,
                    "address": None, "resource_type": None}

    return empty


def diagnose(error_text, telemetry=None, address=None, resource_type=None, run_root=None,
             tier=None, has_pii=False, stakeholder_detected=False):
    """
    Classify a failure and return the structured diagnosis behind the report.

    `telemetry` is an optional `(address, resource_type) -> {"identity", "errors"}` callable,
    injected by the caller exactly as `cloud_drift` does it. Absent, or raising, the result
    carries `telemetry_available: False` and the diagnosis still stands.
    """
    evidence = {"raw_error": _text(error_text).strip(), "source": None,
                "address": address, "resource_type": resource_type,
                "identity": None, "telemetry_errors": []}
    if run_root and not evidence["raw_error"]:
        found = extract_evidence(run_root)
        evidence.update({k: found[k] for k in ("raw_error", "source")})
        evidence["address"] = address or found["address"]
        evidence["resource_type"] = resource_type or found["resource_type"]

    telemetry_available = False
    if telemetry and evidence["address"]:
        try:
            found = telemetry(evidence["address"], evidence["resource_type"])
        except Exception:  # noqa: BLE001 -- an unreachable account must not block the report
            found = None
        if found:
            telemetry_available = True
            evidence["identity"] = found.get("identity")
            evidence["telemetry_errors"] = list(found.get("errors") or [])

    rule = None
    haystack = evidence["raw_error"] + "\n" + "\n".join(evidence["telemetry_errors"])
    if haystack.strip():
        rule = next((r for r in FAILURE_RULES if r.pattern.search(haystack)), None)

    # Severity is assessed even for an unmatched error: "we do not know what broke" and
    # "we do not know how much it matters" are separate questions, and a PII exposure is a
    # P1 whether or not a rule recognised the stack trace.
    assessed = assess_severity(rule, tier=tier, has_pii=has_pii,
                               stakeholder_detected=stakeholder_detected)

    if rule is None:
        return {"matched": False, "rule_id": None, "category": "Unclassified",
                "root_cause": None, "vulnerability": None, "options": [],
                "evidence": evidence, "telemetry_available": telemetry_available,
                "rate_citation": None, "severity": assessed["severity"],
                "route": assessed["route"], "severity_reason": assessed["reason"],
                "severity_factors": assessed["factors"]}

    return {
        "matched": True,
        "rule_id": rule.rule_id,
        "category": rule.category,
        "root_cause": rule.root_cause,
        "vulnerability": rule.vulnerability,
        "options": [dataclasses.asdict(o) for o in rule.options],
        "evidence": evidence,
        "telemetry_available": telemetry_available,
        "rate_citation": _rate_citation(rule.service_code),
        "severity": assessed["severity"],
        "route": assessed["route"],
        "severity_reason": assessed["reason"],
        "severity_factors": assessed["factors"],
    }


# --- Severity --------------------------------------------------------------------------
#
# Severity is computed per incident from impact, NOT looked up from the alert's source.
# The rejected design mapped outage->P1, data-quality->P2, FinOps->P3 and pinned each to a
# channel. Two of this module's own rules break under it: DQ-QUARANTINE-01 is silent,
# unrecoverable data loss that the mapping caps at a Teams message, and
# TF-IAM-CONSISTENCY-01 is a self-healing retry that the mapping pages someone for at 3am.
# Over-paging and under-reacting from the same table.
#
# Inputs, in the order they are applied:
#   1. Regulated-data exposure -> P1, overriding everything. A small PII leak outranks a
#      large low-risk outage, because the exposure may already have happened.
#   2. Asset tier -> the baseline. This is the pre-computed "who depends on this" answer
#      and is the single fastest signal available at triage.
#   3. Silent failure -> one level worse. Wrong-but-plausible output has already been acted
#      on by the time anyone looks; an obvious crash has not.
#   4. Transient -> one level better. A failure that resolves itself needs a ticket.
#
# Routing keys on the computed severity, so a P1 cost anomaly pages exactly like a P1
# outage rather than being filed as email because of which tool noticed it.

SEVERITIES = ("P1", "P2", "P3", "P4")
UNCLASSIFIED = "UNCLASSIFIED"

# Tier 0 leans P1/P2, Tier 1 P2/P3, Tier 2/3 P3/P4 -- baselines sit at the top of each
# band so the modifiers below can move in both directions.
TIER_BASELINE = {"0": "P1", "1": "P2", "2": "P3", "3": "P4"}

ROUTES = {
    "P1": "PagerDuty page, plus a Slack/Teams incident channel and leadership notification",
    "P2": "Secondary on-call page, plus a Slack/Teams thread with an update SLA",
    "P3": "Jira ticket and an async team notification",
    "P4": "Operational log and dashboard only; no notification",
    UNCLASSIFIED: "Human triage: declare the asset tier, then re-run the diagnosis",
}


def _shift(severity, steps):
    """Move `steps` levels (negative = more severe), clamped to the ends of the scale."""
    index = min(max(SEVERITIES.index(severity) + steps, 0), len(SEVERITIES) - 1)
    return SEVERITIES[index]


def find_rule(error_text):
    """The first rule whose pattern matches, or None. Order matters -- see FAILURE_RULES."""
    haystack = _text(error_text)
    if not haystack.strip():
        return None
    return next((r for r in FAILURE_RULES if r.pattern.search(haystack)), None)


def assess_severity(rule, tier=None, has_pii=False, silent_override=None,
                    stakeholder_detected=False):
    """Compute severity and its routing from impact, returning the reasoning with it.

    `tier` is the run's declared asset tier ("0".."3"). Without one this returns
    UNCLASSIFIED rather than a number: the same doctrine as an unmatched error. A severity
    nobody can justify from declared facts is worse than none, because it looks
    authoritative enough to act on.

    `silent_override` lets a caller state what the rule cannot know -- whether THIS
    occurrence produced wrong-but-plausible output. None defers to the rule's own flag.
    """
    regulated = bool(has_pii) or bool(getattr(rule, "regulated", False))
    if regulated:
        return {"severity": "P1", "route": ROUTES["P1"],
                "reason": ("regulated/PII data or the controls protecting it are involved, "
                           "which forces P1 regardless of asset tier"),
                "tier": tier, "factors": ["regulated_data"]}

    key = str(tier).strip() if tier is not None else ""
    if key not in TIER_BASELINE:
        return {"severity": UNCLASSIFIED, "route": ROUTES[UNCLASSIFIED],
                "reason": ("no asset tier is declared for this run, so business impact "
                           "cannot be established; refusing to guess a severity"),
                "tier": tier, "factors": []}

    severity = TIER_BASELINE[key]
    factors = [f"tier_{key}_baseline_{severity}"]

    silent = getattr(rule, "silent", False) if silent_override is None else bool(silent_override)
    if silent:
        severity = _shift(severity, -1)
        factors.append("silent_failure")
    if stakeholder_detected:
        severity = _shift(severity, -1)
        factors.append("stakeholder_detected_before_monitor")
    if getattr(rule, "transient", False):
        severity = _shift(severity, 1)
        factors.append("transient_self_healing")

    return {"severity": severity, "route": ROUTES[severity], "tier": tier,
            "factors": factors,
            "reason": f"tier {key} baseline {TIER_BASELINE[key]}, adjusted by "
                      + (", ".join(factors[1:]) or "nothing")}


def _wrap(label, value, indent=23):
    """Label plus a value whose continuation lines align under it."""
    body = _text(value).strip() or "-"
    pad = " " * indent
    lines = body.splitlines()
    out = [f"   - {label.ljust(indent - 5)}{lines[0]}"]
    out.extend(f"   {pad}{line}" for line in lines[1:])
    return out


def format_report(result):
    """The four-part report. ASCII only -- these are pasted into tickets."""
    rule = "=" * REPORT_WIDTH
    lines = [rule, "DIAGNOSTIC & INCIDENT RESOLUTION REPORT", rule, ""]
    evidence = result["evidence"]

    # Severity leads: it decides who is woken and how fast, so it is the first thing the
    # reader needs. It sits above the evidence rather than buried under the options.
    if result.get("severity"):
        lines.extend(_wrap("Severity:", f"{result['severity']} -- {result['severity_reason']}"))
        lines.extend(_wrap("Routing:", result.get("route") or "-"))
        lines.append("")

    lines.append("1. EXACT LOG & TELEMETRY EVIDENCE")
    lines.extend(_wrap("Resource Address:", evidence.get("address") or "-"))
    if evidence.get("source"):
        lines.extend(_wrap("Evidence Source:", evidence["source"]))
    if evidence.get("identity"):
        lines.extend(_wrap("Changed By:", evidence["identity"]))
    lines.extend(_wrap("Raw Error Log:", evidence.get("raw_error") or "(none captured)"))
    for message in evidence.get("telemetry_errors") or []:
        lines.extend(_wrap("Telemetry:", message))
    lines.append("")

    lines.append("2. ROOT-CAUSE ANALYSIS")
    if not result["matched"]:
        lines.extend(_wrap("Category:", "no signature matched"))
        lines.extend(_wrap("Detailed Cause:", (
            "This error does not match a known signature, so no root cause is asserted. "
            "The raw evidence above is the reliable part; a guessed diagnosis would be "
            "worse than none.")))
        lines.extend(_wrap("Next:", "add a signature to core/reporting/"
                                    "incident_diagnostics.py once the cause is understood."))
        lines.extend(["", rule])
        return "\n".join(lines)

    lines.extend(_wrap("Category:", result["category"]))
    lines.extend(_wrap("Detailed Cause:", result["root_cause"]))
    lines.extend(_wrap("Vulnerability:", result["vulnerability"]))
    lines.append("")

    lines.append("3. EVALUATION OF ALTERNATIVES & TRADE-OFFS")
    lines.append("")
    for index, option in enumerate(result["options"]):
        label = chr(ord("A") + index)
        lines.append(f"   Option {label} ({option['title']} -- {option['strategy']}):")
        lines.extend(_wrap("Change:", option["description"]))
        lines.extend(_wrap("Cost Impact:", option["cost_delta"]))
        lines.extend(_wrap("Implementation:", option["action_command"]))
        lines.append("")

    # Relative deltas only; dollars come from the one path that produces evidenced totals.
    lines.append(f"   Cost impacts above are RATIOS against current spend, not prices. For a "
                 f"reportable dollar figure run `{COST_EVIDENCE_COMMAND}`, which is the only "
                 f"path in MinusOps that produces one.")
    if result.get("rate_citation"):
        lines.append(f"   Reviewed catalog rate: {result['rate_citation']}")
    lines.append("")

    lines.append("4. ACTIONABLE INSTRUCTION & NEXT COMMAND")
    first = result["options"][0]
    lines.append(f"   To proceed with Option A ({first['title']}):")
    lines.append(f"     $ {first['action_command']}")
    lines.append("")
    lines.append(rule)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Diagnose a failure and print a resolution report (offline by default)")
    ap.add_argument("--error", default="", help="raw error text; omit to read from --run")
    ap.add_argument("--run", default="", help="run workspace to extract local evidence from")
    ap.add_argument("--address", default=None)
    ap.add_argument("--resource-type", default=None)
    ap.add_argument("--with-telemetry", action="store_true",
                    help="ask CloudTrail/Glue for the failing resource (read-only, "
                         "fail-open, requires credentials)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    telemetry = None
    if args.with_telemetry:
        try:
            import cloud_drift
            telemetry = cloud_drift.aws_telemetry
        except ImportError:
            telemetry = None

    result = diagnose(args.error, telemetry=telemetry, address=args.address,
                      resource_type=args.resource_type, run_root=args.run or None)
    print(json.dumps(result, indent=2) if args.json else format_report(result))
    return 0 if result["matched"] else 1


if __name__ == "__main__":
    sys.exit(main())
