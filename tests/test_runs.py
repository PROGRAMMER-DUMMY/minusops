import runs


def test_get_run_by_prefix_returns_existing_run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(runs, "RUNS_DIR", str(tmp_path / "runs"))
    run = runs.new_run(blueprint="requirements-first", request="create platform")

    found = runs.get_run(run["run_id"][:12])

    assert found["run_id"] == run["run_id"]
