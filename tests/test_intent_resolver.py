"""
A creation request reaches the requirements gate, never a deploy.

The routing property is the point: "build me a data pipeline" must land on REQUIREMENTS and
an operational phrase must not. An unknown request asks for clarification rather than picking
the nearest blueprint, because guessing here starts a run against infrastructure nobody named.

Depends on: core/generation/intent_resolver.py, core/generation/blueprints.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import blueprints
import intent_resolver


def test_registered_blueprints_are_valid():
    assert blueprints.validate_blueprints() == {}


def test_blueprint_validation_catches_missing_contract_fields():
    errors = blueprints.validate_blueprint({"id": "bad"})

    assert "missing field: name" in errors
    assert "missing field: safe_next_steps" in errors


def test_create_data_pipeline_resolves_to_requirements_first():
    result = intent_resolver.resolve("Create a governed AWS data pipeline for analytics", cloud="aws")

    assert result["intent"] == "REQUIREMENTS"
    assert result["blueprint"] is None
    assert result["confidence"] == "high"
    assert result["missing_inputs"] == []
    assert "Write a requirements.json skeleton into the run workspace." in result["next_safe_actions"]


def test_unknown_creation_request_asks_for_clarification():
    result = intent_resolver.resolve("Create a quantum warehouse stack", cloud="aws")

    assert result["intent"] == "REQUIREMENTS"
    assert result["blueprint"] is None


def test_non_creation_request_falls_back_to_operation_path():
    result = intent_resolver.resolve("show current cost anomalies", cloud="aws")

    assert result["intent"] == "OPERATION"
    assert result["blueprint"] is None


def test_creation_requests_route_to_requirements_not_deploy():
    """dispatcher.py was removed -- an agent routes intent now. The property it protected
    (a build request must reach the requirements gate, never a deploy) belongs here, on the
    resolver that actually decides it."""
    assert intent_resolver.resolve("build a data pipeline")["intent"] == "REQUIREMENTS"
