"""Cost & pricing — AWS BCM is the only source of a reportable number, never invented.

Package marker only; it holds no code, so importing it pulls in none of the modules below.
Those are normally imported flat (`import pricing_catalog`) via the sys.path bootstrap each
one performs, not through this package path.

Depends on: nothing (docstring only)
Shells out to: nothing
Used by: `core.cost` package-path importers; see bcm_pricing_calculator.py, pricing_catalog.py,
    coverage_audit.py, budget_calculator.py for the real dependency edges
"""
