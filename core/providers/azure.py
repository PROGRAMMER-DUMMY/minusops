"""
Azure implementation of CloudProvider (scaffold).

Maps to the same interface via the `az` CLI:
  identity()         -> az account show
  cost_by_service()  -> az costmanagement query (Cost Management)
  anomalies()        -> Cost Management anomaly API
  owner(hint)        -> az resource list + tags

Returns honest "not implemented" shapes until wired, so the dashboard/CLI degrade
gracefully instead of crashing when MINUS_CLOUD=azure.
"""
from .base import CloudProvider

_MSG = "Azure provider is on the roadmap (AWS is the production-wired cloud; set MINUS_CLOUD=aws)."


class AzureProvider(CloudProvider):
    name = "azure"
    status = "roadmap"

    def identity(self):
        return None, False

    def cost_by_service(self, months_back=6):
        return {"ok": False, "error": _MSG, "months": []}

    def anomalies(self, days_back=60):
        return None, _MSG

    def owner(self, resource_hint):
        return None
