"""
CloudProvider — the one interface every cloud must implement.

The control plane is cloud-agnostic: it calls these methods and never touches a cloud CLI
directly. Pick the active cloud with the MINUS_CLOUD env var (aws | azure | gcp), default 'aws'.

Return shapes (kept identical across clouds so the dashboard/CLI don't branch):
  identity()         -> (account_id: str | None, connected: bool)
  cost_by_service()  -> {"ok": bool, "error": str, "months": [
                            {"month": "YYYY-MM", "total": float, "by_service": {svc: amount}} ]}
  anomalies()        -> (list[dict] | None, error: str)   # dicts: id, service, date, impact
  owner(hint)        -> str | None                        # team/owner from tags/labels

Pre-deploy PRICING methods (separate from the actuals methods above — actuals need live
resources, pricing does not). These are the onboarding contract for a new cloud: implement all
four against that cloud's OFFICIAL pricing catalog, then run `core/cost/coverage_audit.py` against
a real plan for that cloud until every resource type is auto_priced / catalog_mapped_needs_usage
/ confirmed_free — never trust a cloud's pricing numbers until coverage_audit shows no
UNRESOLVED entries. They default to a safe "unknown" return (not @abstractmethod, not a raise)
so a provider that only implements actuals — like Azure/GCP today — still instantiates AND a
caller that doesn't special-case an unfinished cloud doesn't crash either: everything just comes
back unresolved, which `coverage_audit.py` already renders as an honest, visible gap rather than
a fabricated result. Check `.status` if you need to distinguish "this cloud checked and found
nothing" from "this cloud's pricing discovery isn't implemented yet." Only AWS overrides these
today.
  list_billable_services()          -> [{"service_code": str, "display_name": str}, ...]
  resolve_resource_type(tf_type)    -> {"service_code": str, "display_name": str, "verified": bool} | None
  lookup_usage_dimensions(service, filters=None) -> catalog dict (usageType/operation/sku equivalents)
  confirmed_free(tf_type)           -> {"display_name": str, "note": str} | None
"""
import os
from abc import ABC, abstractmethod


class CloudProvider(ABC):
    name = "base"
    # "implemented" = production-wired; "roadmap" = honest scaffold that degrades gracefully.
    status = "roadmap"

    @abstractmethod
    def identity(self):
        """Return (account_or_subscription_id, connected_bool)."""

    @abstractmethod
    def cost_by_service(self, months_back=6):
        """Return spend grouped by service for the trailing months."""

    @abstractmethod
    def anomalies(self, days_back=60):
        """Return (anomaly_list, error_str)."""

    @abstractmethod
    def owner(self, resource_hint):
        """Return the owning team/person for a resource hint, or None."""

    def credential_posture(self):
        """
        Report the active credential posture: {connected, type, ...} where type is
        "temporary" | "long_term" | "root" | "unknown". Default derives only
        connectivity; clouds override to classify temporary vs long-term sessions.
        """
        account, connected = self.identity()
        return {"connected": connected, "account": account, "type": "unknown"}

    def list_billable_services(self):
        """Every billable service in this cloud's official pricing catalog. Read-only.
        Default: [] (pricing discovery not implemented for this cloud yet)."""
        return []

    def resolve_resource_type(self, tf_type):
        """Map a Terraform resource type to this cloud's service identifier, or None if
        unresolved. Must never guess — an unresolved type should surface via coverage_audit,
        not be silently priced at $0 or an invented rate. Default: None for every type (pricing
        discovery not implemented for this cloud yet — coverage_audit will show everything as
        unresolved, which is the honest state, not a crash)."""
        return None

    def lookup_usage_dimensions(self, service, filters=None):
        """The catalog dimensions (AWS: usageType/operation; Azure: meter/skuName; GCP:
        sku description) needed to price a service, from the cloud's OFFICIAL pricing API.
        Read-only, always for human review — never trusted directly for a reportable total.
        Default: {} (pricing discovery not implemented for this cloud yet)."""
        return {}

    def confirmed_free(self, tf_type):
        """A reviewed fact that this resource type carries no billable SKU on this cloud, or
        None. Default: None for every type (nothing has been reviewed yet for this cloud, so
        nothing is asserted free — the honest default is 'unknown', not 'free')."""
        return None


def active_cloud():
    return os.environ.get("MINUS_CLOUD", "aws").strip().lower()


def get_provider(name=None):
    """Factory: return the provider for `name` (or the MINUS_CLOUD env, default aws)."""
    name = (name or active_cloud()).lower()
    if name == "aws":
        from .aws import AWSProvider
        return AWSProvider()
    if name == "azure":
        from .azure import AzureProvider
        return AzureProvider()
    if name == "gcp":
        from .gcp import GCPProvider
        return GCPProvider()
    raise ValueError(f"Unknown cloud provider: {name!r} (expected aws | azure | gcp)")


def capabilities():
    """Capability matrix across clouds — AWS is production-wired; others are roadmap."""
    matrix = {}
    for cloud in ("aws", "azure", "gcp"):
        try:
            matrix[cloud] = get_provider(cloud).status
        except Exception:
            matrix[cloud] = "unavailable"
    return matrix
