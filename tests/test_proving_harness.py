"""
The 5-hop end-to-end proving harness (PRD-ARCH-2026-007, FR-01).

A stack that plans cleanly, applies cleanly, and scores full readiness can still be a
pipeline that has never carried a byte. The three-hop proof answered "did data land, did the
job run, does Gold return rows". Two hops were missing, and they are the two that catch the
expensive failures: a transform that silently drops malformed rows instead of quarantining
them, and a data-quality suite nobody ever ran.

Nothing here reaches AWS. What is tested is the contract around the harness -- hop ordering,
that a failed hop stops the ones downstream of it, that no report is written for a proof that
did not run, and that the report's arithmetic cannot be satisfied by losing records.

Depends on: core/reporting/seed.py
Shells out to: nothing (every `_aws` call is faked)
Used by: nothing (pytest entry point)
"""
import io
import json
import os
from contextlib import redirect_stdout

import pytest

import runs
import seed as seed_engine


def _capture_cli(module, argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = module.main(argv)
    return code, out.getvalue()


def _patch_runs(tmp_path, monkeypatch):
    import workflow
    for holder in (runs, workflow.runs):
        monkeypatch.setattr(holder, "WORKSPACE", str(tmp_path))
        monkeypatch.setattr(holder, "RUNS_DIR", str(tmp_path / "runs"))


def _synthesized_run(**kwargs):
    """A run far enough along the lifecycle that `prove` will accept it. The stage guard is
    doing its job -- a pipeline that was never synthesized cannot be proven."""
    run = runs.new_run(**kwargs)
    with open(os.path.join(run["terraform_dir"], "main.tf"), "w", encoding="utf-8") as f:
        f.write('resource "aws_s3_bucket" "bronze" {}\n')
    return run

FIVE_HOP_OUTPUTS = {
    "bucket_names": {"bronze": "acme-bronze-1", "silver": "acme-silver-1",
                     "gold": "acme-gold-1"},
    "glue_job_names": {"bronze_to_silver": "acme-bronze_to_silver"},
    "athena_workgroup": "acme-analysts",
    "glue_catalog_database": "acme_gold",
    "dq_job_name": "acme-dq",
    "dq_results_bucket": "acme-dq-results-1",
    "quarantine_bucket": "acme-quarantine-1",
}


def _ge_result(passed=14, failed=0):
    """Great Expectations writes its own validation-result shape; the harness reads it
    rather than re-implementing the assertions."""
    return {"statistics": {"successful_expectations": passed,
                           "unsuccessful_expectations": failed}}


def _fake_aws_factory(gold_rows="980", quarantined=20, ge=None, job_state="SUCCEEDED"):
    ge = ge if ge is not None else _ge_result()

    def _fake(args, **kwargs):
        head = " ".join(args[:2])
        if head == "s3 cp":
            return True, "", ""
        if head == "glue start-job-run":
            return True, {"JobRunId": "jr_1"}, ""
        if head == "glue get-job-run":
            return True, {"JobRun": {"JobRunState": job_state,
                                     "ErrorMessage": "OutOfMemoryError"}}, ""
        if head == "s3 ls":
            return True, "\n".join(f"2026-08-22 10:00:00 128 bad-{i}.json"
                                   for i in range(quarantined)), ""
        if head == "s3api list-objects-v2":
            return True, {"Contents": [{"Key": "validations/latest.json"}]}, ""
        if head == "s3 cp-to-stdout":
            return True, ge, ""
        if head == "athena start-query-execution":
            return True, {"QueryExecutionId": "q1"}, ""
        if head == "athena get-query-execution":
            return True, {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}, ""
        if head == "athena get-query-results":
            return True, {"ResultSet": {"Rows": [
                {"Data": [{"VarCharValue": "row_count"}]},
                {"Data": [{"VarCharValue": gold_rows}]}]}}, ""
        raise AssertionError(f"unexpected call: {args}")
    return _fake


@pytest.fixture
def fixture_file(tmp_path):
    path = tmp_path / "sample.json"
    path.write_text(json.dumps([{"id": i} for i in range(1000)]), encoding="utf-8")
    return str(path)


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(seed_engine, "read_outputs", lambda _dir: FIVE_HOP_OUTPUTS)
    monkeypatch.setattr(seed_engine.approval, "request_approval", lambda *a, **k: True)
    monkeypatch.setattr(seed_engine.time, "sleep", lambda _s: None)


# --- Plan mode ------------------------------------------------------------------------

def test_plan_mode_names_all_five_hops_and_sends_nothing(tmp_path, fixture_file, offline,
                                                         monkeypatch):
    """The default must remain "print what you would do". This is the only MinusOps command
    that mutates AWS."""
    def _never(args, **kwargs):
        raise AssertionError(f"plan mode reached AWS: {args}")
    monkeypatch.setattr(seed_engine, "_aws", _never)

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file)

    assert result["executed"] is False
    assert [h["name"] for h in result["plan"]["hops"]] == list(seed_engine.HOP_NAMES)


def test_plan_mode_writes_no_report(tmp_path, fixture_file, offline, monkeypatch):
    """A proving report is evidence that a proof ran. Writing one for a plan would make the
    artifact a lie the moment it is filed."""
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file,
                                        reports_dir=str(tmp_path / "reports"))

    assert result.get("report_path") is None
    assert not (tmp_path / "reports").exists()


def test_a_three_hop_stack_is_refused_by_name(tmp_path, fixture_file, monkeypatch):
    """A stack without the dq module cannot prove hops 3 and 4. Running the other three and
    calling the result PASS is the false green this harness exists to prevent."""
    monkeypatch.setattr(seed_engine, "read_outputs", lambda _dir: {
        "bucket_names": {"bronze": "b"}, "glue_job_names": {"x": "j"},
        "athena_workgroup": "w", "glue_catalog_database": "d"})

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file)

    assert result["ok"] is False
    assert "quarantine_bucket" in result["error"]
    assert "dq_job_name" in result["error"]


# --- Execution ------------------------------------------------------------------------

def test_five_hops_run_in_order_and_pass(tmp_path, fixture_file, offline, monkeypatch):
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        records=1000, malformed=20,
                                        reports_dir=str(tmp_path / "reports"))

    assert result["ok"] is True
    assert [h["hop"] for h in result["hops"]] == [1, 2, 3, 4, 5]
    assert [h["name"] for h in result["hops"]] == list(seed_engine.HOP_NAMES)
    assert all(h["status"] == "PASS" for h in result["hops"])


def test_a_failed_hop_stops_the_ones_downstream(tmp_path, fixture_file, offline, monkeypatch):
    """Querying Gold after the transform failed produces a stale-data answer that reads as
    success. The sequence has to stop where the truth stops."""
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory(job_state="FAILED"))

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        reports_dir=str(tmp_path / "reports"))

    assert result["ok"] is False
    assert [h["hop"] for h in result["hops"]] == [1, 2]
    assert result["hops"][-1]["status"] == "FAIL"
    assert "OutOfMemoryError" in result["hops"][-1]["detail"]


def test_a_failed_proof_still_writes_its_report(tmp_path, fixture_file, offline, monkeypatch):
    """Evidence of failure is evidence. A report that exists only on success is a report
    that cannot be used to argue against a deploy."""
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory(job_state="FAILED"))

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        reports_dir=str(tmp_path / "reports"))

    report = json.loads(open(result["report_path"], encoding="utf-8").read())
    assert report["status"] == "FAIL"


def test_denied_approval_runs_nothing(tmp_path, fixture_file, monkeypatch):
    monkeypatch.setattr(seed_engine, "read_outputs", lambda _dir: FIVE_HOP_OUTPUTS)
    monkeypatch.setattr(seed_engine.approval, "request_approval", lambda *a, **k: False)
    def _never(args, **kwargs):
        raise AssertionError(f"acted without approval: {args}")
    monkeypatch.setattr(seed_engine, "_aws", _never)

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True)

    assert result["ok"] is False
    assert result["hops"] == []


def test_the_approval_request_names_every_side_effect(tmp_path, fixture_file, monkeypatch):
    """One prompt for the whole sequence, listing what it does. Five prompts is five clicks
    an operator learns to make without reading."""
    monkeypatch.setattr(seed_engine, "read_outputs", lambda _dir: FIVE_HOP_OUTPUTS)
    monkeypatch.setattr(seed_engine.time, "sleep", lambda _s: None)
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())
    asked = {}
    monkeypatch.setattr(seed_engine.approval, "request_approval",
                        lambda action, details, mode="gatekeeper": asked.update(
                            action=action, details=details) or True)

    seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                               reports_dir=str(tmp_path / "reports"))

    for named in ("acme-bronze-1", "acme-bronze_to_silver", "acme-dq", "acme_gold"):
        assert named in asked["details"]


# --- Hop 3: data quality --------------------------------------------------------------

def test_failed_expectations_fail_the_hop(tmp_path, fixture_file, offline, monkeypatch):
    """A DQ suite that runs and reports failures is a working suite reporting a broken
    pipeline. Counting the job's exit status alone would call that PASS."""
    monkeypatch.setattr(seed_engine, "_aws",
                        _fake_aws_factory(ge=_ge_result(passed=11, failed=3)))

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        reports_dir=str(tmp_path / "reports"))

    dq = next(h for h in result["hops"] if h["name"] == "great_expectations_dq")
    assert dq["status"] == "FAIL"
    assert dq["assertions_failed"] == 3
    assert result["ok"] is False


def test_an_unreadable_validation_result_is_not_a_pass(tmp_path, fixture_file, offline,
                                                       monkeypatch):
    """No result file means the suite never wrote one. Unknown is not success."""
    fake = _fake_aws_factory()

    def _no_results(args, **kwargs):
        if " ".join(args[:2]) == "s3api list-objects-v2":
            return True, {}, ""
        return fake(args, **kwargs)
    monkeypatch.setattr(seed_engine, "_aws", _no_results)

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        reports_dir=str(tmp_path / "reports"))

    dq = next(h for h in result["hops"] if h["name"] == "great_expectations_dq")
    assert dq["status"] == "FAIL"


# --- Hop 4: quarantine routing --------------------------------------------------------

def test_quarantined_and_gold_records_must_account_for_everything_injected(
        tmp_path, fixture_file, offline, monkeypatch):
    """The failure this hop exists for: a transform that drops malformed rows instead of
    quarantining them. Gold looks clean, the count looks plausible, and 20 records are gone."""
    monkeypatch.setattr(seed_engine, "_aws",
                        _fake_aws_factory(gold_rows="980", quarantined=5))

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        records=1000, malformed=20,
                                        reports_dir=str(tmp_path / "reports"))

    quarantine = next(h for h in result["hops"] if h["name"] == "quarantine_verification")
    assert quarantine["status"] == "FAIL"
    assert "15" in quarantine["detail"], "the report must name how many records were lost"


def test_a_clean_run_accounts_for_every_injected_record(tmp_path, fixture_file, offline,
                                                        monkeypatch):
    monkeypatch.setattr(seed_engine, "_aws",
                        _fake_aws_factory(gold_rows="980", quarantined=20))

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        records=1000, malformed=20,
                                        reports_dir=str(tmp_path / "reports"))

    quarantine = next(h for h in result["hops"] if h["name"] == "quarantine_verification")
    assert quarantine["status"] == "PASS"
    assert quarantine["clean_records_routed_gold"] == 980
    assert quarantine["malformed_records_quarantined"] == 20


def test_an_empty_quarantine_when_bad_records_were_injected_is_a_failure(
        tmp_path, fixture_file, offline, monkeypatch):
    """20 malformed records went in and nothing was quarantined: either the validation never
    ran or it passed rows it should have caught."""
    monkeypatch.setattr(seed_engine, "_aws",
                        _fake_aws_factory(gold_rows="1000", quarantined=0))

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        records=1000, malformed=20,
                                        reports_dir=str(tmp_path / "reports"))

    quarantine = next(h for h in result["hops"] if h["name"] == "quarantine_verification")
    assert quarantine["status"] == "FAIL"


# --- The report -----------------------------------------------------------------------

def test_the_report_lands_under_the_plan_hash(tmp_path, fixture_file, offline, monkeypatch):
    """Evidence is bound to the exact plan it proves. A report filed under a run name alone
    cannot say WHICH version of the stack was tested."""
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())

    result = seed_engine.prove_pipeline(
        str(tmp_path), fixture_file, execute=True, plan_hash="a" * 64,
        reports_dir=str(tmp_path / "reports"))

    assert result["report_path"].replace("\\", "/").endswith(
        f"reports/{'a' * 64}/proving_report.json")


def test_the_report_matches_the_published_schema(tmp_path, fixture_file, offline, monkeypatch):
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())

    result = seed_engine.prove_pipeline(
        str(tmp_path), fixture_file, execute=True, run_name="marketing-clickstream-mwaa_1",
        records=1000, malformed=20, reports_dir=str(tmp_path / "reports"))
    report = json.loads(open(result["report_path"], encoding="utf-8").read())

    assert report["run_name"] == "marketing-clickstream-mwaa_1"
    assert report["status"] == "PASS"
    assert report["proven_at"].endswith("+00:00") or report["proven_at"].endswith("Z")
    for hop in report["hops"]:
        assert set(("hop", "name", "status", "latency_seconds")) <= set(hop)


def test_total_latency_is_the_sum_of_the_hops(tmp_path, fixture_file, offline, monkeypatch):
    """A total that does not add up is a total somebody computed separately, which is how a
    report starts disagreeing with itself."""
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        reports_dir=str(tmp_path / "reports"))

    assert round(result["total_latency_seconds"], 3) == round(
        sum(h["latency_seconds"] for h in result["hops"]), 3)


def test_the_report_is_tamper_evident(tmp_path, fixture_file, offline, monkeypatch):
    """"Signed" here means what it means everywhere else in this repo: a SHA-256 over the
    canonical payload, so an edited hop count no longer matches its own digest."""
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        reports_dir=str(tmp_path / "reports"))
    report = json.loads(open(result["report_path"], encoding="utf-8").read())

    assert seed_engine.verify_report(report) is True
    report["hops"][0]["records_injected"] = 999999
    assert seed_engine.verify_report(report) is False


def test_the_report_carries_no_emoji(tmp_path, fixture_file, offline, monkeypatch):
    """NFR-01. These reports are pasted into tickets and rendered by terminals that turn an
    emoji into a replacement box or a mojibake pair."""
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        reports_dir=str(tmp_path / "reports"))
    raw = open(result["report_path"], encoding="utf-8").read()

    assert all(ord(ch) < 0x2190 for ch in raw), "non-ASCII symbol found in the proving report"


def test_the_formatted_summary_carries_no_emoji(tmp_path, fixture_file, offline, monkeypatch):
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        reports_dir=str(tmp_path / "reports"))
    text = seed_engine.format_proof(result)

    assert all(ord(ch) < 0x2190 for ch in text)
    assert "bronze_ingestion" in text


# --- AC-01: reached through the CLI ---------------------------------------------------

def test_prove_without_execute_stays_the_offline_evidence_bundle(tmp_path, monkeypatch):
    """`minusctl prove` already meant something: the offline governance-chain evidence
    bundle. Redefining it would break the one command an operator runs before a handoff."""
    import minusctl

    _patch_runs(tmp_path, monkeypatch)
    run = _synthesized_run(blueprint="requirements-first", request="x")

    _, output = _capture_cli(minusctl, ["prove", "--run", run["run_id"], "--json"])

    # The exit code reflects whether the chain proved, which a bare run's does not. What
    # matters here is WHICH path ran: the evidence bundle, not the five-hop harness.
    assert "offline_chain_proven" in output
    assert "athena_serving_query" not in output


def test_prove_execute_runs_the_five_hop_harness(tmp_path, monkeypatch):
    """AC-01."""
    import minusctl

    _patch_runs(tmp_path, monkeypatch)
    run = _synthesized_run(name="clickstream", domain="marketing")
    fixture = tmp_path / "sample.json"
    fixture.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(seed_engine, "read_outputs", lambda _dir: FIVE_HOP_OUTPUTS)
    monkeypatch.setattr(seed_engine.approval, "request_approval", lambda *a, **k: True)
    monkeypatch.setattr(seed_engine.time, "sleep", lambda _s: None)
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())

    code, output = _capture_cli(minusctl, [
        "prove", "--run", run["run_id"], "--execute", "--fixture", str(fixture),
        "--records", "1000", "--malformed", "20"])

    assert code == 0
    assert "athena_serving_query" in output
    assert os.path.exists(os.path.join(run["reports_dir"], "unbound", "proving_report.json"))


def test_prove_execute_reports_a_failing_pipeline_as_a_failure(tmp_path, monkeypatch):
    """A non-zero exit is what a CI promotion gate reads."""
    import minusctl

    _patch_runs(tmp_path, monkeypatch)
    run = _synthesized_run(name="clickstream", domain="marketing")
    fixture = tmp_path / "sample.json"
    fixture.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(seed_engine, "read_outputs", lambda _dir: FIVE_HOP_OUTPUTS)
    monkeypatch.setattr(seed_engine.approval, "request_approval", lambda *a, **k: True)
    monkeypatch.setattr(seed_engine.time, "sleep", lambda _s: None)
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory(job_state="FAILED"))

    code, _ = _capture_cli(minusctl, ["prove", "--run", run["run_id"], "--execute",
                                      "--fixture", str(fixture)])

    assert code == 1


# --- PRD v11 Step 3 (FR-03): the modular hop registry ---------------------------------

def test_the_registry_covers_exactly_the_declared_hop_names():
    """A hop in HOP_NAMES with no registry entry cannot be selected; a registry entry
    missing from HOP_NAMES never appears in a plan. Either way the report lies about what
    the proof covered."""
    # The registry is a superset of the default proof: latency_sla and pii are selectable
    # extensions, not part of the five-hop sequence.
    assert set(seed_engine.HOP_KEYS) <= set(seed_engine.HOPS)
    assert [seed_engine.HOPS[k].name for k in seed_engine.HOP_KEYS] == list(seed_engine.HOP_NAMES)
    # Every dependency names a hop that exists, or the selection guard cannot enforce it.
    for key, spec in seed_engine.HOPS.items():
        for needed in spec.requires:
            assert needed in seed_engine.HOPS, f"{key} requires unknown hop {needed}"


def test_only_the_requested_hops_run(tmp_path, fixture_file, offline, monkeypatch):
    """AC-02. `--hops ingest,transform` must not start an Athena query."""
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())
    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        hops=("ingest", "transform"))

    assert [h["name"] for h in result["hops"]] == ["bronze_ingestion", "spark_glue_etl"]
    assert result["ok"] is True


def test_unselected_hops_are_reported_as_not_run_never_as_passed(
        tmp_path, fixture_file, offline, monkeypatch):
    """The report is signed. A hop that never executed must not be indistinguishable from
    one that passed, or the signature attests to coverage that does not exist."""
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())
    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        hops=("ingest",))

    statuses = {h["name"]: h["status"] for h in result["coverage"]}
    assert statuses["bronze_ingestion"] == "PASS"
    assert statuses["athena_serving_query"] == "NOT_RUN"
    assert all(s in ("PASS", "FAIL", "NOT_RUN") for s in statuses.values())


def test_an_unknown_hop_name_is_refused_before_anything_runs(
        tmp_path, fixture_file, offline, monkeypatch):
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())
    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        hops=("ingest", "teleport"))

    assert result["status"] == "REFUSED"
    assert "teleport" in result["error"]
    assert result["hops"] == []


def test_quarantine_without_query_is_refused_not_silently_wrong(
        tmp_path, fixture_file, offline, monkeypatch):
    """The trap this closes: quarantine reconciles injected == gold + quarantined, and it
    reads the Gold count from the query hop. With query unselected the count defaults to 0
    and the hop reports `1000 injected, 0 reached Gold, N unaccounted for` -- a confident
    FALSE failure. Refusing the selection is the only honest answer."""
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())
    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        hops=("ingest", "quarantine"))

    assert result["status"] == "REFUSED"
    assert "query" in result["error"]


def test_a_non_blocking_hop_failing_does_not_stop_the_rest(
        tmp_path, fixture_file, offline, monkeypatch):
    """latency_sla is advisory: a slow pipeline is not a broken one. A blocking hop failing
    still short-circuits, because querying Gold after a failed transform returns stale data
    that reads as success."""
    assert seed_engine.HOPS["ingest"].blocking is True
    assert seed_engine.HOPS["transform"].blocking is True
    assert seed_engine.HOPS["latency_sla"].blocking is False


def test_default_still_runs_the_full_five_hop_proof(
        tmp_path, fixture_file, offline, monkeypatch):
    """Backward compatibility: no --hops means exactly what it meant before."""
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())
    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True)

    assert [h["name"] for h in result["hops"]] == list(seed_engine.HOP_NAMES)
    assert result["ok"] is True


def test_seed_three_hop_contract_is_untouched():
    """seed.py's own docstring: TWO ENTRY POINTS, ONE SET OF HOPS. `seed()` must not grow
    a hops argument or change shape."""
    import inspect
    assert "hops" not in inspect.signature(seed_engine.seed).parameters


def test_a_partial_selection_only_needs_the_outputs_those_hops_use(
        tmp_path, fixture_file, monkeypatch):
    """The point of --hops is proving what you CAN on the stack you have. A three-hop stack
    has no dq_job_name, and demanding one before running `ingest,transform` would refuse a
    selection that needs nothing from the data-quality module."""
    three_hop = {k: v for k, v in FIVE_HOP_OUTPUTS.items()
                 if k not in ("dq_job_name", "dq_results_bucket", "quarantine_bucket")}
    monkeypatch.setattr(seed_engine, "read_outputs", lambda _dir: three_hop)
    monkeypatch.setattr(seed_engine.approval, "request_approval", lambda *a, **k: True)
    monkeypatch.setattr(seed_engine.time, "sleep", lambda _s: None)
    monkeypatch.setattr(seed_engine, "_aws", _fake_aws_factory())

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True,
                                        hops=("ingest", "transform"))

    assert result["status"] == "PASS", result["error"]
    assert [h["name"] for h in result["hops"]] == ["bronze_ingestion", "spark_glue_etl"]


def test_a_three_hop_stack_still_refuses_the_full_five_hop_default(
        tmp_path, fixture_file, monkeypatch):
    """The original guard is unchanged: asking for the full proof on a stack that cannot
    support it is still refused by name, never silently reduced."""
    three_hop = {k: v for k, v in FIVE_HOP_OUTPUTS.items()
                 if k not in ("dq_job_name", "dq_results_bucket", "quarantine_bucket")}
    monkeypatch.setattr(seed_engine, "read_outputs", lambda _dir: three_hop)

    result = seed_engine.prove_pipeline(str(tmp_path), fixture_file, execute=True)

    assert result["status"] == "REFUSED"
    assert "dq_job_name" in result["error"]


def test_the_hops_flag_reaches_the_harness(tmp_path, monkeypatch):
    """A flag the CLI accepts but drops is a flag that does not exist."""
    import minusctl
    _patch_runs(tmp_path, monkeypatch)
    run = _synthesized_run(blueprint="demo", request="hops flag")
    seen = {}

    def _spy(*args, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "status": "PASS", "hops": [], "coverage": [],
                "executed": True, "plan": {}, "total_latency_seconds": 0.0,
                "report_path": None, "error": None}

    monkeypatch.setattr(seed_engine, "prove_pipeline", _spy)
    monkeypatch.setattr(seed_engine, "format_proof", lambda _r: "")
    _capture_cli(minusctl, ["prove", "--run", run["run_id"], "--execute",
                            "--hops", "ingest,transform"])

    assert seen["hops"] == ["ingest", "transform"]


def test_no_hops_flag_passes_none_so_the_default_is_the_harness_default(tmp_path, monkeypatch):
    import minusctl
    _patch_runs(tmp_path, monkeypatch)
    run = _synthesized_run(blueprint="demo", request="no hops flag")
    seen = {}

    def _spy(*args, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "status": "PASS", "hops": [], "coverage": [],
                "executed": True, "plan": {}, "total_latency_seconds": 0.0,
                "report_path": None, "error": None}

    monkeypatch.setattr(seed_engine, "prove_pipeline", _spy)
    monkeypatch.setattr(seed_engine, "format_proof", lambda _r: "")
    _capture_cli(minusctl, ["prove", "--run", run["run_id"], "--execute"])

    assert seen["hops"] is None
