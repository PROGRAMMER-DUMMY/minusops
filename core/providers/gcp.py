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
