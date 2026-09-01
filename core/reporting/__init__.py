"""Reporting, inspection & ops — turns a plan into something a human can read and run.

Package marker only; it holds no code. The modules here are imported flat (`import reporter`,
`import runs`) via the sys.path bootstrap each one performs, never through this package path.

That is a correctness rule, not a preference. Reaching one of these files by both routes gives
two module objects with independent module-level state -- see tests/test_module_identity.py.
doctor.py used to be the exception, preferring `from core.governance import plan_gate` with the
flat form as an ImportError fallback, and a plain `minusctl doctor` consequently held two
plan_gate objects and two _gate_state_lock instances. It now bootstraps like everything else.

Depends on: nothing (docstring only)
Shells out to: nothing
Used by: nothing imports through this path any more; see minusctl.py, reporter.py, doctor.py,
    seed.py, adopt.py, optimize_analyzer.py, plan_inspector.py, cli_diagnostics.py,
    finops_agent.py, excel_finops_generator.py, health_checker.py, runs.py, toolpath.py for
    the real dependency edges
"""
