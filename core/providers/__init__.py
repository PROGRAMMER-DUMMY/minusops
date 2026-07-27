"""AWS provider access.

The governance core (finops, dashboard, coverage_audit) reaches AWS only through
get_provider() — never a cloud CLI directly.
"""
from .base import get_provider, active_cloud  # noqa: F401
