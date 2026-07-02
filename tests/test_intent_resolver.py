import blueprints
import dispatcher
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


def test_dispatcher_routes_creation_to_blueprint_not_deploy():
    assert dispatcher.classify_intent("build a data pipeline") == "REQUIREMENTS"
