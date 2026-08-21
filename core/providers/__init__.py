"""Package surface for provider access: re-exports `get_provider` and `active_cloud`.

The governance core (finops, dashboard, coverage_audit) reaches AWS only through
`get_provider()` — never a cloud CLI directly. Keep this file a pure re-export; a
provider that gets constructed at import time would fire AWS CLI calls during
`import core.providers`, which several tests and `minusctl doctor` do offline.

Depends on: base (same package)
Shells out to: nothing directly (AWSProvider shells out to the `aws` CLI)
Used by: core/reporting/doctor.py (as `core.providers.base`); most callers import
    `providers.base` directly via the core/ sys.path shim rather than through here
"""
from .base import get_provider, active_cloud  # noqa: F401
