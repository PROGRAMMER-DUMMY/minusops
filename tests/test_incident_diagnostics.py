"""
PRD v9: the incident diagnostics and remediation engine.

Turns a raw failure -- a YARN OOM kill, an IAM eventual-consistency error, a GE assertion
failure -- into the four-part report an engineer can act on: evidence, root cause, evaluated
alternatives with their trade-offs, and the exact next command.

Two properties are load-bearing and both are about NOT inventing things:

  * An unrecognised error produces "no signature matched" plus the raw evidence, never a
    guessed diagnosis. A confident wrong root cause is worse than none -- it sends someone
    down a path the error never supported.
  * Cost deltas are RATIOS, not dollars. `G.2X` is 2x the DPU count of `G.1X`, which is an
    instance-class fact true in every region. `$0.44/DPU-hour` is a us-east-1 list price that
    is wrong in eu-west-1 and wrong again after any repricing. See the note in
    `incident_diagnostics` and `core/cost/budget_calculator.py`.

Depends on: core/reporting/incident_diagnostics.py
Shells out to: nothing (the offline path must make no network call at all)
Used by: nothing (pytest entry point)
"""
import json
import os
import time

import pytest

import incident_diagnostics as diag

GLUE_OOM = (
    "Container killed by YARN for exceeding memory limits. 5.5 GB of 5.5 GB physical "
    "memory used. Container marked as failed."
)
IAM_CONSISTENCY = (
    "Error: error creating Glue Job: InvalidInputException: Please check your role and "
    "verify that the role has been propagated"
)
ATHENA_SPLIT = "HIVE_CANNOT_OPEN_SPLIT: Error opening Hive split s3://gold/events/dt=2026-08-22"
GE_FAILURE = "Great Expectations suite failed: 3 of 14 expectations did not pass"


# --- FR-02: signature classification --------------------------------------------------

@pytest.mark.parametrize("text,category_fragment", [
    (GLUE_OOM, "memory"),
    (IAM_CONSISTENCY, "consistency"),
    (ATHENA_SPLIT, "split"),
    (GE_FAILURE, "quality"),
])
def test_each_operational_domain_has_a_signature(text, category_fragment):
    """FR-02 names four domains: Terraform apply, Glue/Spark, Athena/Trino, and the proving
    harness. One unmatched domain is a whole class of failure the engine stays silent on."""
    result = diag.diagnose(text)

    assert result["matched"] is True
    assert category_fragment in result["category"].lower()


def test_an_unknown_error_is_not_given_an_invented_diagnosis():
    """The most important test here. A confident wrong root cause sends an engineer down a
    path the error never supported, and they trust it because the report looks authoritative."""
    result = diag.diagnose("Error: something nobody has ever seen before, code 0x9f")

    assert result["matched"] is False
    assert result["root_cause"] is None
    assert result["options"] == []
    assert "something nobody has ever seen" in result["evidence"]["raw_error"]


def test_an_empty_error_is_not_a_match():
    for text in ("", None, "   "):
        assert diag.diagnose(text)["matched"] is False


def test_the_first_matching_rule_wins_and_is_reported_by_id():
    """Rules are ordered. Which one fired has to be inspectable, or a mis-classification is
    untraceable."""
    result = diag.diagnose(GLUE_OOM)

    assert result["rule_id"]
    assert any(r.rule_id == result["rule_id"] for r in diag.FAILURE_RULES)


# --- FR-03: alternatives and trade-offs -----------------------------------------------

def test_every_rule_offers_at_least_two_paths_forward():
    """FR-03. One option is an instruction, not an evaluation. The engineer needs to be able
    to weigh spend against effort."""
    for rule in diag.FAILURE_RULES:
        assert len(rule.options) >= 2, f"{rule.rule_id} offers no alternative"


def test_every_rule_offers_at_least_one_zero_cost_option():
    """FR-03 Option B. If the only way out of every incident is to spend more, the engine is
    a sales funnel rather than a diagnostic."""
    for rule in diag.FAILURE_RULES:
        assert any(o.cost_multiplier == 1.0 for o in rule.options), \
            f"{rule.rule_id} has no zero-cost path"


def test_options_carry_a_strategy_from_the_declared_set():
    for rule in diag.FAILURE_RULES:
        for option in rule.options:
            assert option.strategy in diag.STRATEGIES


def test_every_option_names_an_actionable_command():
    for rule in diag.FAILURE_RULES:
        for option in rule.options:
            assert option.action_command.strip()


# --- NFR-03 as this repo can honestly satisfy it --------------------------------------

def test_cost_deltas_are_ratios_never_invented_dollar_rates():
    """`budget_calculator` exists to refuse fabricated cost figures, and a hardcoded
    $0.44/DPU-hour is a us-east-1 list price that is wrong in eu-west-1 and wrong again after
    any repricing. The DPU RATIO between G.1X and G.2X is an instance-class fact that holds
    everywhere, so that is what the report states."""
    for rule in diag.FAILURE_RULES:
        for option in rule.options:
            assert "$" not in option.cost_delta, \
                f"{rule.rule_id}/{option.title} states a dollar figure"
            assert isinstance(option.cost_multiplier, float)


def test_scaling_an_instance_class_reports_its_real_multiplier():
    """G.2X is twice the DPU count of G.1X. That is the number an engineer needs to reason
    about spend, and it does not depend on a price list."""
    result = diag.diagnose(GLUE_OOM)

    scale = next(o for o in result["options"] if o["strategy"] == "scaling")
    assert scale["cost_multiplier"] == 2.0


def test_the_report_points_at_the_command_that_produces_real_dollars():
    """Refusing to invent a number is only useful if it says where the real one comes from."""
    text = diag.format_report(diag.diagnose(GLUE_OOM))
    assert "minusctl cost estimate" in text


# --- FR-01: the report ----------------------------------------------------------------

def test_the_report_has_all_four_numbered_sections():
    text = diag.format_report(diag.diagnose(GLUE_OOM))

    for heading in ("1. EXACT LOG & TELEMETRY EVIDENCE", "2. ROOT-CAUSE ANALYSIS",
                    "3. EVALUATION OF ALTERNATIVES & TRADE-OFFS",
                    "4. ACTIONABLE INSTRUCTION & NEXT COMMAND"):
        assert heading in text


def test_the_report_quotes_the_raw_error_verbatim():
    """The paraphrase is the diagnosis; the raw text is the evidence. An engineer who
    disagrees with the diagnosis needs the original to reason from."""
    text = diag.format_report(diag.diagnose(GLUE_OOM))
    assert "5.5 GB of 5.5 GB physical memory used" in text


def test_an_unmatched_failure_still_produces_a_readable_report():
    """A failure the engine cannot classify is exactly when someone needs the evidence laid
    out. Returning nothing would send them back to the raw scrollback."""
    text = diag.format_report(diag.diagnose("Error: unknown condition 0x9f"))

    assert "1. EXACT LOG & TELEMETRY EVIDENCE" in text
    assert "unknown condition 0x9f" in text
    assert "no signature matched" in text.lower()


def test_the_report_carries_no_emoji():
    """NFR-01."""
    for text in (diag.format_report(diag.diagnose(GLUE_OOM)),
                 diag.format_report(diag.diagnose("Error: unknown"))):
        assert all(ord(ch) < 0x2190 for ch in text)


def test_the_report_labels_options_a_b_c_in_order():
    text = diag.format_report(diag.diagnose(GLUE_OOM))
    assert "Option A" in text and "Option B" in text
    assert text.index("Option A") < text.index("Option B")


# --- NFR-02: offline, sub-50ms, zero network ------------------------------------------

def test_classification_makes_no_network_call(monkeypatch):
    """The default path runs on a laptop with no credentials, mid-incident. Reaching for AWS
    there is slower for no answer and fails when it is needed most."""
    import subprocess

    def _explode(*args, **kwargs):
        raise AssertionError("offline diagnosis reached a subprocess")

    monkeypatch.setattr(subprocess, "run", _explode)
    monkeypatch.setattr(subprocess, "Popen", _explode)

    assert diag.diagnose(GLUE_OOM)["matched"] is True


def test_classification_is_sub_50ms():
    """NFR-02. Generous by three orders of magnitude against a pure-regex path -- this fails
    only if someone puts I/O behind it."""
    start = time.perf_counter()
    for _ in range(20):
        diag.diagnose(GLUE_OOM)
    elapsed_ms = (time.perf_counter() - start) * 1000 / 20

    assert elapsed_ms < 50, f"{elapsed_ms:.2f}ms per classification"


# --- FR-02: local evidence extraction -------------------------------------------------

def test_evidence_is_read_from_a_proving_report(tmp_path):
    root = tmp_path / "run"
    (root / "reports" / "abc").mkdir(parents=True)
    (root / "reports" / "abc" / "proving_report.json").write_text(json.dumps({
        "status": "FAIL",
        "hops": [{"hop": 2, "name": "spark_glue_etl", "status": "FAIL",
                  "detail": GLUE_OOM, "job_name": "acme-etl"}]}), encoding="utf-8")

    evidence = diag.extract_evidence(str(root))

    assert GLUE_OOM in evidence["raw_error"]
    assert evidence["source"].endswith("proving_report.json")


def test_evidence_falls_back_to_a_terraform_log(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "terraform.log").write_text("Error: " + IAM_CONSISTENCY, encoding="utf-8")

    evidence = diag.extract_evidence(str(root))

    assert "propagated" in evidence["raw_error"]


def test_no_local_evidence_is_reported_as_absent_not_invented(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()

    evidence = diag.extract_evidence(str(root))

    assert evidence["raw_error"] == ""
    assert evidence["source"] is None


def test_a_passing_proving_report_yields_no_failure_evidence(tmp_path):
    """Diagnosing a successful run would manufacture an incident out of a green result."""
    root = tmp_path / "run"
    (root / "reports" / "abc").mkdir(parents=True)
    (root / "reports" / "abc" / "proving_report.json").write_text(json.dumps({
        "status": "PASS",
        "hops": [{"hop": 1, "name": "bronze_ingestion", "status": "PASS", "detail": "ok"}]}),
        encoding="utf-8")

    assert diag.extract_evidence(str(root))["raw_error"] == ""


# --- Telemetry: opt-in, fail-open -----------------------------------------------------

def test_telemetry_is_off_by_default():
    result = diag.diagnose(GLUE_OOM)
    assert result["telemetry_available"] is False


def test_a_supplied_telemetry_lookup_enriches_the_evidence():
    def _lookup(address, resource_type):
        return {"identity": "john.doe@acme.com",
                "errors": ["2026-08-22 02:45:12 UTC " + GLUE_OOM]}

    result = diag.diagnose(GLUE_OOM, telemetry=_lookup,
                           address="aws_glue_job.customer_events_etl",
                           resource_type="aws_glue_job")

    assert result["telemetry_available"] is True
    assert "02:45:12" in json.dumps(result["evidence"])


def test_a_telemetry_lookup_that_raises_never_blocks_the_report():
    """Fail-open. The diagnostic is what someone is reading DURING an incident; an
    unreachable CloudWatch must not be the reason they get nothing."""
    def _explode(address, resource_type):
        raise RuntimeError("no credentials")

    result = diag.diagnose(GLUE_OOM, telemetry=_explode,
                           address="aws_glue_job.etl", resource_type="aws_glue_job")

    assert result["matched"] is True
    assert result["telemetry_available"] is False


# --- FR-04: reachable from the CLI ----------------------------------------------------

def test_diagnose_subcommand_reports_a_known_failure(tmp_path, monkeypatch):
    """The engine is only useful if an operator mid-incident can reach it by name."""
    import io as _io
    from contextlib import redirect_stdout

    import minusctl
    import runs as runs_mod
    import workflow

    for holder in (runs_mod, workflow.runs):
        monkeypatch.setattr(holder, "WORKSPACE", str(tmp_path))
        monkeypatch.setattr(holder, "RUNS_DIR", str(tmp_path / "runs"))

    out = _io.StringIO()
    with redirect_stdout(out):
        code = minusctl.main(["diagnose", "--error", GLUE_OOM])

    assert code == 0
    assert "ROOT-CAUSE ANALYSIS" in out.getvalue()


def test_diagnose_exits_non_zero_on_an_unclassified_failure(tmp_path, monkeypatch):
    """A CI step wrapping this needs to distinguish "diagnosed" from "still unknown"."""
    import io as _io
    from contextlib import redirect_stdout

    import minusctl

    out = _io.StringIO()
    with redirect_stdout(out):
        code = minusctl.main(["diagnose", "--error", "Error: nobody knows 0x9f"])

    assert code == 1
    assert "no signature matched" in out.getvalue().lower()


def test_next_surfaces_a_diagnosis_when_the_run_has_a_failure(tmp_path, monkeypatch):
    """FR-04. `minusctl next` is where an operator looks after something breaks; it should
    not send them to the happy path while a failed proving report sits in the run."""
    import io as _io
    from contextlib import redirect_stdout

    import minusctl
    import runs as runs_mod
    import workflow

    for holder in (runs_mod, workflow.runs):
        monkeypatch.setattr(holder, "WORKSPACE", str(tmp_path))
        monkeypatch.setattr(holder, "RUNS_DIR", str(tmp_path / "runs"))

    run = runs_mod.new_run(name="clickstream", domain="marketing")
    with open(os.path.join(run["terraform_dir"], "main.tf"), "w", encoding="utf-8") as f:
        f.write('resource "aws_s3_bucket" "b" {}\n')
    report_dir = os.path.join(run["reports_dir"], "abc")
    os.makedirs(report_dir, exist_ok=True)
    with open(os.path.join(report_dir, "proving_report.json"), "w", encoding="utf-8") as f:
        json.dump({"status": "FAIL", "hops": [
            {"hop": 2, "name": "spark_glue_etl", "status": "FAIL", "detail": GLUE_OOM}]}, f)

    out = _io.StringIO()
    with redirect_stdout(out):
        minusctl.main(["next", "--run", run["run_id"]])
    text = out.getvalue()

    assert "minusctl diagnose" in text
    assert "GLUE-OOM-01" in text or "memory" in text.lower()


def test_next_is_unchanged_for_a_run_with_no_failure(tmp_path, monkeypatch):
    """The overwhelmingly common case. A diagnostic banner on every healthy run is noise."""
    import io as _io
    from contextlib import redirect_stdout

    import minusctl
    import runs as runs_mod
    import workflow

    for holder in (runs_mod, workflow.runs):
        monkeypatch.setattr(holder, "WORKSPACE", str(tmp_path))
        monkeypatch.setattr(holder, "RUNS_DIR", str(tmp_path / "runs"))

    run = runs_mod.new_run(name="clean", domain="ops")

    out = _io.StringIO()
    with redirect_stdout(out):
        minusctl.main(["next", "--run", run["run_id"]])

    assert "minusctl diagnose" not in out.getvalue()


# --- PRD v11 Step 4 (FR-05): impact-driven severity ------------------------------------
#
# The rejected design bound severity to the alert SOURCE -- outage=P1, data quality=P2,
# FinOps=P3 -- each pinned to a fixed channel. Two things break under it, both visible in
# the rules this module already ships:
#
#   DQ-QUARANTINE-01 is "Quarantine Threshold / Data Loss". Silent, unrecoverable, and on a
#   Tier 0 billing table it is a P1 -- the source-based mapping caps it at a Teams message.
#
#   TF-IAM-CONSISTENCY-01 is a transient eventual-consistency retry. The source-based
#   mapping pages someone at 3am for something that resolves itself.
#
# So severity is computed per incident from asset tier, whether the failure is silent, and
# whether regulated data is exposed -- and routing follows the computed severity, never the
# tool that raised it.

import incident_diagnostics as diag


def test_severity_is_not_a_function_of_category_alone():
    """The same signal on a Tier 0 asset and a Tier 3 sandbox must not land on one level."""
    tier0 = diag.assess_severity(diag.find_rule("Glue job OutOfMemoryError"), tier="0")
    tier3 = diag.assess_severity(diag.find_rule("Glue job OutOfMemoryError"), tier="3")

    assert tier0["severity"] != tier3["severity"]
    assert diag.SEVERITIES.index(tier0["severity"]) < diag.SEVERITIES.index(tier3["severity"])


def test_pii_exposure_is_p1_whatever_the_tier_says():
    """The override the framework puts above every other factor: a small regulated-data
    exposure outranks a large low-risk outage."""
    assessed = diag.assess_severity(None, tier="3", has_pii=True)

    assert assessed["severity"] == "P1"
    assert "pii" in assessed["reason"].lower() or "regulated" in assessed["reason"].lower()


def test_a_silent_failure_is_bumped_above_a_visible_one():
    """Wrong-but-plausible data is more dangerous than an obviously broken job, because it
    has already been acted on by the time anyone looks."""
    rule = diag.find_rule("Great Expectations expectation suite failed")
    silent = diag.assess_severity(rule, tier="1", silent_override=True)
    visible = diag.assess_severity(rule, tier="1", silent_override=False)

    assert diag.SEVERITIES.index(silent["severity"]) < diag.SEVERITIES.index(visible["severity"])


def test_undeclared_tier_refuses_to_guess():
    """Same doctrine as the unmatched-error path: a severity nobody can justify from
    declared facts is worse than none, because it looks authoritative."""
    assessed = diag.assess_severity(diag.find_rule("Glue job OutOfMemoryError"), tier=None)

    assert assessed["severity"] == diag.UNCLASSIFIED
    assert assessed["route"] == diag.ROUTES[diag.UNCLASSIFIED]
    assert "tier" in assessed["reason"].lower()


def test_routing_is_keyed_on_severity_not_on_the_tool_that_fired():
    """A P1 cost anomaly pages exactly like a P1 outage. Capping FinOps at an email digest
    regardless of severity is the specific failure this replaces."""
    assert set(diag.ROUTES) == set(diag.SEVERITIES) | {diag.UNCLASSIFIED}
    assert "pagerduty" in diag.ROUTES["P1"].lower()
    assert "log" in diag.ROUTES["P4"].lower()

    quota = diag.assess_severity(diag.find_rule("has exceeded the service quota"), tier="0")
    oom = diag.assess_severity(diag.find_rule("Glue job OutOfMemoryError"), tier="0")
    assert quota["route"] == diag.ROUTES[quota["severity"]]
    assert oom["route"] == diag.ROUTES[oom["severity"]]


def test_a_transient_retry_does_not_page_a_human_at_three_am():
    """TF-IAM-CONSISTENCY-01 resolves itself. Paging for it is how on-call learns to ignore
    the pager, which is the expensive failure."""
    rule = diag.find_rule(
        "InvalidInputException: Glue could not assume role arn:aws:iam::1234:role/etl")

    assert rule is not None and rule.rule_id == "TF-IAM-CONSISTENCY-01"
    assessed = diag.assess_severity(rule, tier="2")
    assert assessed["severity"] in ("P3", "P4")


def test_every_rule_declares_whether_it_is_silent():
    """`silent` drives a severity bump, so an un-set default silently suppresses it."""
    for rule in diag.FAILURE_RULES:
        assert isinstance(rule.silent, bool), rule.rule_id


def test_diagnose_carries_the_assessment_when_a_tier_is_known():
    result = diag.diagnose("Glue job failed with OutOfMemoryError", tier="0")

    assert result["severity"] == "P1"
    assert result["route"] == diag.ROUTES["P1"]
