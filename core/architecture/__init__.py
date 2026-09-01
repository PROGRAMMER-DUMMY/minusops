"""Requirements & architecture decisions — the reviewed records generation is bound to.

Package marker only; it holds no code. The modules below are normally imported flat
(`import requirements as reqgate`) via the sys.path bootstrap each one performs, not through
this package path — core/reporting/doctor.py is the one caller that tries both.

Depends on: nothing (docstring only)
Shells out to: nothing
Used by: `core.architecture` package-path importers (core/reporting/doctor.py); see
    requirements.py, architecture_decision.py, architecture_model.py, intent_assertions.py,
    team_resolver.py, discovery.py for the real dependency edges
"""
