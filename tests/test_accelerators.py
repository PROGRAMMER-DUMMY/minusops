import os

import accelerators


def test_lakehouse_accelerator_records_complete_requirements_and_decision():
    requirements = accelerators.lakehouse_requirements(owner="analytics", daily_data_gb=25)
    decision = accelerators.lakehouse_decision(requirements_file="requirements.json")

    req_ok, req_missing = accelerators.reqgate.validate(requirements)
    decision_ok, decision_missing = accelerators.archdec.validate(decision)

    assert req_ok, req_missing
    assert decision_ok, decision_missing
    assert "storage-medallion-s3" in decision["selected_modules"]
    assert "query-athena" in decision["selected_modules"]
    # The accelerator now also gathers the data-pipeline FR/NFR profile (conformant by construction).
    assert accelerators.reqgate.is_data_pipeline(requirements)
    dp_ok, dp_missing = accelerators.reqgate.validate_data_pipeline(requirements)
    assert dp_ok, dp_missing


def test_lakehouse_accelerator_adds_streaming_module_only_when_requested():
    batch = accelerators.lakehouse_decision(streaming=False)
    streaming = accelerators.lakehouse_decision(streaming=True)

    assert "speed-layer-kinesis" not in batch["selected_modules"]
    assert "speed-layer-kinesis" in streaming["selected_modules"]


def test_write_lakehouse_refuses_overwrite_without_force(tmp_path, monkeypatch):
    monkeypatch.setattr(accelerators.runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(accelerators.runs, "RUNS_DIR", str(tmp_path / "runs"))
    run = accelerators.runs.new_run(blueprint="requirements-first", request="create lakehouse")

    result = accelerators.write_lakehouse(run, owner="analytics", daily_data_gb=50)

    assert os.path.exists(result["requirements_file"])
    assert os.path.exists(result["decision_file"])
    try:
        accelerators.write_lakehouse(run)
    except FileExistsError as exc:
        assert "pass --force" in str(exc)
    else:
        raise AssertionError("expected overwrite refusal")
