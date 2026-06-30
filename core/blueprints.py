"""
Governed blueprint registry.

Blueprints are the product contract between short user intent and generated
Terraform. The resolver may recommend a blueprint, but creation and deployment
still go through explicit generation, verification, plan, approval, and apply.
"""

from copy import deepcopy


REQUIRED_BLUEPRINT_FIELDS = {
    "id": str,
    "name": str,
    "cloud": str,
    "status": str,
    "aliases": list,
    "summary": str,
    "services": list,
    "required_inputs": list,
    "controls": list,
    "safe_next_steps": list,
}

REQUIRED_INPUT_FIELDS = {
    "name": str,
    "prompt": str,
    "reason": str,
}


BLUEPRINTS = [
    {
        "id": "aws-data-pipeline-standard",
        "name": "AWS Data Pipeline Standard",
        "cloud": "aws",
        # Demo/cached fixture, NOT the production path. It powers `minusctl demo` + the golden
        # tests as a reproducible worked example. Production architecture is gathered with
        # `grill-me` and composed from `core/modules.py` via the `architect` path — every company
        # differs on orchestrator / pattern / data-quality / schema, so one recipe can't serve all.
        "status": "demo-fixture",
        "aliases": [
            "data pipeline",
            "analytics pipeline",
            "etl pipeline",
            "lakehouse",
            "medallion",
            "s3 glue athena",
            "raw curated analytics",
        ],
        "summary": (
            "Governed AWS batch analytics pipeline using S3 bronze/silver/gold, "
            "KMS, Glue, Step Functions, Athena, CloudWatch, scoped IAM, and cost controls."
        ),
        "services": [
            "Amazon S3",
            "AWS KMS",
            "AWS Glue",
            "AWS Step Functions",
            "Amazon Athena",
            "Amazon CloudWatch",
            "AWS Budgets",
            "AWS Cost Anomaly Detection",
        ],
        "required_inputs": [
            {
                "name": "environment",
                "prompt": "Environment name",
                "default": "dev",
                "reason": "Used for naming, tags, and state separation.",
            },
            {
                "name": "region",
                "prompt": "AWS region",
                "default": "us-east-1",
                "reason": "Terraform providers and pricing vary by region.",
            },
            {
                "name": "owner",
                "prompt": "Owning team or cost center",
                "default": None,
                "reason": "Required for tags, audit records, and FinOps ownership.",
            },
            {
                "name": "ingestion_mode",
                "prompt": "Batch or streaming ingestion",
                "default": "batch",
                "choices": ["batch", "streaming"],
                "reason": "Determines whether the pipeline starts from scheduled files or live events.",
            },
            {
                "name": "daily_data_gb",
                "prompt": "Expected data volume in GB per day",
                "default": None,
                "reason": "Used for lifecycle, Athena scan limits, budgets, and alarms.",
            },
        ],
        "controls": [
            "SSE-KMS for storage and logs",
            "S3 public access blocks",
            "Versioning and lifecycle policies",
            "Per-service IAM roles with scoped resource permissions",
            "CloudWatch alarms and log retention",
            "Budget and anomaly detection hooks",
            "Terraform plan hash approval before apply",
        ],
        "safe_next_steps": [
            "Collect required inputs.",
            "Generate Terraform into an explicit user-approved directory.",
            "Run optimize_analyzer.py against that directory.",
            "Run plan_gate.py verify against that directory.",
            "Stop before terraform plan/apply unless the user asks for the next gated step.",
        ],
    }
]


def _is_non_empty_string(value):
    return isinstance(value, str) and bool(value.strip())


def validate_blueprint(blueprint):
    """Return a list of schema errors for one blueprint."""
    errors = []
    for field, expected_type in REQUIRED_BLUEPRINT_FIELDS.items():
        if field not in blueprint:
            errors.append(f"missing field: {field}")
            continue
        if not isinstance(blueprint[field], expected_type):
            errors.append(f"{field} must be {expected_type.__name__}")

    for field in ("id", "name", "cloud", "status", "summary"):
        if field in blueprint and not _is_non_empty_string(blueprint[field]):
            errors.append(f"{field} must be a non-empty string")

    for field in ("aliases", "services", "controls", "safe_next_steps"):
        if field in blueprint and isinstance(blueprint[field], list):
            if not blueprint[field]:
                errors.append(f"{field} must not be empty")
            if not all(_is_non_empty_string(item) for item in blueprint[field]):
                errors.append(f"{field} entries must be non-empty strings")

    if "required_inputs" in blueprint and isinstance(blueprint["required_inputs"], list):
        names = set()
        for index, item in enumerate(blueprint["required_inputs"]):
            if not isinstance(item, dict):
                errors.append(f"required_inputs[{index}] must be object")
                continue
            for field, expected_type in REQUIRED_INPUT_FIELDS.items():
                if field not in item:
                    errors.append(f"required_inputs[{index}] missing field: {field}")
                    continue
                if not isinstance(item[field], expected_type):
                    errors.append(f"required_inputs[{index}].{field} must be {expected_type.__name__}")
            name = item.get("name")
            if name in names:
                errors.append(f"duplicate required input: {name}")
            if name:
                names.add(name)
            if "choices" in item and not (
                    isinstance(item["choices"], list)
                    and item["choices"]
                    and all(_is_non_empty_string(choice) for choice in item["choices"])):
                errors.append(f"required_inputs[{index}].choices must be non-empty string list")

    return errors


def validate_blueprints(blueprints=None):
    """Return a dict of blueprint_id -> list of validation errors."""
    blueprints = blueprints if blueprints is not None else BLUEPRINTS
    errors = {}
    seen = set()
    for index, blueprint in enumerate(blueprints):
        blueprint_id = blueprint.get("id", f"<index:{index}>") if isinstance(blueprint, dict) else f"<index:{index}>"
        item_errors = validate_blueprint(blueprint) if isinstance(blueprint, dict) else ["blueprint must be object"]
        if blueprint_id in seen:
            item_errors.append(f"duplicate blueprint id: {blueprint_id}")
        seen.add(blueprint_id)
        if item_errors:
            errors[blueprint_id] = item_errors
    return errors


def list_blueprints(cloud=None):
    """Return registered blueprints, optionally filtered by cloud."""
    cloud = (cloud or "").strip().lower()
    items = BLUEPRINTS
    if cloud:
        items = [bp for bp in items if bp["cloud"] == cloud]
    return deepcopy(items)


def get_blueprint(blueprint_id):
    """Return one blueprint by id, or None."""
    for blueprint in BLUEPRINTS:
        if blueprint["id"] == blueprint_id:
            return deepcopy(blueprint)
    return None


def match_blueprints(query, cloud=None):
    """
    Score blueprints for a user query.

    This intentionally stays deterministic and conservative. The resolver uses
    these scores to recommend a known blueprint instead of letting a vague
    request turn into free-form infrastructure.
    """
    normalized = " ".join((query or "").lower().split())
    matches = []
    for blueprint in list_blueprints(cloud):
        score = 0
        reasons = []
        for alias in blueprint["aliases"]:
            alias_norm = alias.lower()
            if alias_norm in normalized:
                score += 6
                reasons.append(alias)
                continue
            alias_terms = [term for term in alias_norm.split() if len(term) > 2]
            hits = [term for term in alias_terms if term in normalized]
            if hits:
                score += len(hits)
                reasons.extend(hits)
        for service in blueprint["services"]:
            service_terms = [term.lower() for term in service.split() if len(term) > 2]
            hits = [term for term in service_terms if term in normalized]
            if hits:
                score += len(hits)
                reasons.extend(hits)
        if score:
            matches.append({
                "blueprint": blueprint,
                "score": score,
                "reasons": sorted(set(reasons)),
            })
    return sorted(matches, key=lambda item: item["score"], reverse=True)
