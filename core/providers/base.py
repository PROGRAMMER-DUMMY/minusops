"""
Provider access for the governance core.

The core (finops, dashboard, coverage_audit) reaches AWS only through `get_provider()`,
never a cloud CLI directly. AWS is the only cloud; the Azure/GCP scaffolds and the
one-implementation CloudProvider ABC were removed once multi-cloud was dropped from scope.

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
