"""Package surface for provider access: re-exports `get_provider` and `active_cloud`.

The governance core (finops, dashboard, coverage_audit) reaches AWS only through
`get_provider()` — never a cloud CLI directly. Keep this file a pure re-export; a
provider that gets constructed at import time would fire AWS CLI calls during
`import core.providers`, which several tests and `minusctl doctor` do offline.

Depends on: base (same package)
Shells out to: nothing directly (AWSProvider shells out to the `aws` CLI)
Used by: callers import `providers.base` directly via the core/ sys.path shim rather than
    through here -- doctor.py did reach it as `core.providers.base` until that turned out to
    load a second copy of its neighbours (tests/test_module_identity.py)
"""
from .base import get_provider, active_cloud  # noqa: F401
