"""Cloud provider abstraction.

The governance core (dispatcher, finops, dashboard) talks to clouds ONLY through
the CloudProvider interface in base.py — never to a cloud CLI directly. This is what
makes the framework multi-cloud: add a provider, not a rewrite.
"""
from .base import CloudProvider, get_provider, active_cloud  # noqa: F401
