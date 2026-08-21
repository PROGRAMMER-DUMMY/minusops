"""Reporting, inspection & ops — turns a plan into something a human can read and run.

Package marker only; it holds no code. The modules here are normally imported flat
(`import reporter`, `import runs`) via the sys.path bootstrap each one performs, not through
this package path — `from core.reporting.optimize_analyzer import ...` in doctor.py is the
one package-path import, and it is a fallback behind a try/except.

Depends on: nothing (docstring only)
Shells out to: nothing
Used by: `core.reporting` package-path importers (core/reporting/doctor.py); see
    minusctl.py, reporter.py, doctor.py, seed.py, adopt.py, optimize_analyzer.py,
    plan_inspector.py, cli_diagnostics.py, finops_agent.py, excel_finops_generator.py,
    health_checker.py, runs.py, toolpath.py for the real dependency edges
"""
