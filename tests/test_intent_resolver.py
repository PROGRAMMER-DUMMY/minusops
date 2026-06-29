import blueprints
import dispatcher
import intent_resolver


def test_registered_blueprints_are_valid():
    assert blueprints.validate_blueprints() == {}


def test_blueprint_validation_catches_missing_contract_fields():
    errors = blueprints.validate_blueprint({"id": "bad"})

    assert "missing field: name" in errors
    assert "missing field: safe_next_steps" in errors


def test_create_data_pipeline_resolves_to_governed_blueprint():
    result = intent_resolver.resolve("Create a governed AWS data pipeline for analytics", cloud="aws")

    assert result["intent"] == "BLUEPRINT"
    assert result["blueprint"]["id"] == "aws-data-pipeline-standard"
    assert result["confidence"] == "high"
    assert {item["name"] for item in result["missing_inputs"]} == {"owner", "daily_data_gb"}
    assert "Generate Terraform into an explicit user-approved directory." in result["next_safe_actions"]


def test_unknown_creation_request_asks_for_clarification():
    result = intent_resolver.resolve("Create a quantum warehouse stack", cloud="aws")

    assert result["intent"] == "ASK_CLARIFICATION"
    assert result["blueprint"] is None
    assert "aws-data-pipeline-standard" in result["available_blueprints"]


def test_non_creation_request_falls_back_to_operation_path():
    result = intent_resolver.resolve("show current cost anomalies", cloud="aws")

    assert result["intent"] == "OPERATION"
    assert result["blueprint"] is None


def test_dispatcher_routes_creation_to_blueprint_not_deploy():
    assert dispatcher.classify_intent("build a data pipeline") == "BLUEPRINT"
