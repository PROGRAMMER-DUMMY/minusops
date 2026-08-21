"""Provider entry point: `get_provider()` / `active_cloud()`, plus the return-shape contract.

The core (finops, dashboard, coverage_audit) reaches AWS only through `get_provider()`,
never a cloud CLI directly. AWS is the only cloud; the Azure/GCP scaffolds and the
one-implementation CloudProvider ABC were removed once multi-cloud was dropped from scope.
Re-adding either is a reversal of that decision, not a feature: an empty scaffold lets the
product claim multi-cloud support it cannot back, and an ABC with one implementation just
duplicates the contract documented below. `get_provider()` therefore raises on any name
other than "aws" instead of falling back to a stub.

The docstring below is the contract. It is what dashboard/CLI/gate call sites are written
against, and there is no ABC left to enforce it — change a return shape here and every
consumer listed under "Used by" is affected.

AWSProvider's return shapes (relied on by the dashboard and CLI):
  identity()          -> (account_id: str | None, connected: bool)
  credential_posture()-> {"connected": bool, "account": str|None,
                          "type": "temporary"|"long_term"|"root"|"unknown"}
  cost_by_service()   -> {"ok": bool, "error": str, "months": [
                             {"month": "YYYY-MM", "total": float, "by_service": {svc: amount}} ]}
  anomalies()         -> (list[dict] | None, error: str)   # dicts: id, service, date, impact
  owner(hint)         -> str | None                        # team/owner from tags

Pre-deploy PRICING methods (separate from the actuals methods above — actuals need live
resources, pricing does not). Never guess: an unresolved type must surface via
`core/cost/coverage_audit.py` rather than be silently priced at $0 or an invented rate.
  list_billable_services()          -> [{"service_code": str, "display_name": str}, ...]
  resolve_resource_type(tf_type)    -> {"service_code": str, "display_name": str, "verified": bool} | None
  lookup_usage_dimensions(service, filters=None) -> catalog dict (usageType/operation/sku)
  confirmed_free(tf_type)           -> {"display_name": str, "note": str} | None

Depends on: aws (lazy import inside get_provider, so importing this module costs nothing)
Shells out to: nothing here; the AWSProvider it returns is the product's only path to the
    cloud, via the `aws` CLI — `sts get-caller-identity`, `ce get-cost-and-usage`,
    `ce get-anomalies`, `resourcegroupstaggingapi get-resources`, and the Price List
    (`pricing`) API through pricing_catalog. Anything reaching AWS goes through here.
Used by: core/reporting/finops_agent.py, core/reporting/reporter.py,
    core/reporting/doctor.py, core/reporting/minusctl.py, core/governance/plan_gate.py,
    core/governance/authz.py, core/cost/coverage_audit.py,
    core/cost/bcm_pricing_calculator.py, core/generation/intent_resolver.py
    (active_cloud only), app/dashboard_app.py, tests/test_providers.py and other tests
"""


def active_cloud():
    """The cloud this build targets. AWS-only; kept as a function because reports and
    manifests record it as a label."""
    return "aws"


def get_provider(name=None):
    """Return the AWS provider. `name` is accepted for call-site compatibility and must be
    'aws' (or omitted) — anything else is an error rather than a silent fallback."""
    if name is not None and name.lower() != "aws":
        raise ValueError(f"Unknown cloud provider: {name!r} (this build is AWS-only)")
    from .aws import AWSProvider
    return AWSProvider()
