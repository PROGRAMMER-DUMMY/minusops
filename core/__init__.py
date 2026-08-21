"""Package marker for the MinusOps governance engine. Intentionally almost empty.

Most of the repo does not import `core.*` at all: modules add `core/` and its
subdirectories to `sys.path` and import siblings flat (`import toolpath`,
`from providers.base import get_provider`), so this file is usually bypassed. Keep it free
of imports and side effects — anything added here runs twice, once per import style, and
would break the flat path used by every gate and the dashboard.

`__version__` is declared but not read anywhere; it is a label, not a wired-up version.

Depends on: nothing (stdlib only)
Shells out to: nothing
Used by: core/reporting/doctor.py, which imports `core.governance` / `core.architecture` /
    `core.providers` / `core.reporting` package-style before falling back to flat imports
"""

__version__ = "0.1.0"
