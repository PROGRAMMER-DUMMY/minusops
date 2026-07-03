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

    # Pre-deploy pricing discovery (list_billable_services/resolve_resource_type/
    # lookup_usage_dimensions/confirmed_free) is not implemented yet — inherited from
    # CloudProvider, they degrade to empty/None so a caller never crashes; coverage_audit.py
    # will show every resource type as unresolved for this cloud until they're wired up. When
    # Azure is actually needed, implement them against the Azure Retail Prices API — public,
    # unauthenticated, no credentials required: https://prices.azure.com/api/retail/prices
    # (filter by serviceName / armSkuName / armRegionName via OData $filter). There is no Azure
    # equivalent of AWS BCM's workload-estimate object, so a resolved estimate here would be
    # SELF-COMPUTED (catalog unit price x derived usage amount) rather than provider-computed —
    # label it as such in any report, never with the same confidence as an AWS BCM total.
