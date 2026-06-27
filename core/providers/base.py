"""
CloudProvider — the one interface every cloud must implement.

The control plane is cloud-agnostic: it calls these four methods and never touches
a cloud CLI directly. Pick the active cloud with the MINUS_CLOUD env var
(aws | azure | gcp), default 'aws'.

Return shapes (kept identical across clouds so the dashboard/CLI don't branch):
  identity()         -> (account_id: str | None, connected: bool)
  cost_by_service()  -> {"ok": bool, "error": str, "months": [
                            {"month": "YYYY-MM", "total": float, "by_service": {svc: amount}} ]}
  anomalies()        -> (list[dict] | None, error: str)   # dicts: id, service, date, impact
  owner(hint)        -> str | None                        # team/owner from tags/labels
"""
import os
from abc import ABC, abstractmethod


class CloudProvider(ABC):
    name = "base"

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
