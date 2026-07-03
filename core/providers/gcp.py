"""
GCP implementation of CloudProvider (scaffold).

Maps to the same interface via the `gcloud` CLI / Billing:
  identity()         -> gcloud config get-value account / project
  cost_by_service()  -> Cloud Billing BigQuery export or `gcloud billing`
  anomalies()        -> Billing budgets / anomaly signals
  owner(hint)        -> gcloud asset/resource labels

Returns honest "not implemented" shapes until wired, so the dashboard/CLI degrade
gracefully instead of crashing when MINUS_CLOUD=gcp.
"""
from .base import CloudProvider

_MSG = "GCP provider is on the roadmap (AWS is the production-wired cloud; set MINUS_CLOUD=aws)."


class GCPProvider(CloudProvider):
    name = "gcp"
    status = "roadmap"

    def identity(self):
        return None, False

    def cost_by_service(self, months_back=6):
        return {"ok": False, "error": _MSG, "months": []}

    def anomalies(self, days_back=60):
        return None, _MSG

    def owner(self, resource_hint):
        return None

    # Pre-deploy pricing discovery (list_billable_services/resolve_resource_type/
    # lookup_usage_dimensions/confirmed_free) is not implemented yet — inherited from
    # CloudProvider, they degrade to empty/None so a caller never crashes; coverage_audit.py
    # will show every resource type as unresolved for this cloud until they're wired up. When
    # GCP is actually needed, implement them against the Cloud Billing Catalog API: GET
    # https://cloudbilling.googleapis.com/v1/services/{service}/skus (services.skus.list) —
    # requires the cloud-platform OAuth scope, unlike Azure's public endpoint. There is no GCP
    # equivalent of AWS BCM's workload-estimate object, so a resolved estimate here would be
    # SELF-COMPUTED (catalog unit price x derived usage amount) rather than provider-computed —
    # label it as such in any report, never with the same confidence as an AWS BCM total.
