# core/ — package map

`core/` is split into 6 subpackages by responsibility. This file is the map of *what lives
where and why* — read it before adding a new file or moving one. Unlike `docs/REPO_MAP.md`
(a gitignored, point-in-time snapshot of the whole repo), this file documents structure that's
expected to stay stable, so it's checked into source control.

```
core/
  generation/    requirements -> composed Terraform workspace
  architecture/  the reviewed records generation is bound to
  governance/    verify -> plan -> approve -> apply, plus the audit trail
  cost/          AWS BCM is the only source of a reportable number
  reporting/     turns a plan into something a human can read and run
  providers/     one CloudProvider interface per cloud (aws implemented; azure/gcp scaffolds)
```

## generation/ — composition

| File | Purpose |
| :-- | :-- |
| `synthesizer.py` | Composes vetted modules into a governed Terraform workspace from a requirements record. |
| `modules.py` | The module registry — metadata for each `modules/<id>/`: what it satisfies, provisions, needs. |
| `module_provenance.py` | Pins a module's content hash + source/provider-version at maintainer-update time; detects drift. `minus-update-module` CLI. |
| `blueprints.py` | Governed blueprint registry — short intent -> generated pipeline contract. |
| `accelerators.py` | Explicit, reviewable architecture starting points an operator opts into (never automatic). |
| `intent_resolver.py` | Short NL request -> approved blueprint + missing inputs. Never generates or deploys. |
| `patterns.py` | Approved-composition cache so a repeat request reuses prior reviewed work. |
| `workflow.py` | Safe agent entrypoint: request -> requirements -> architecture decision -> generation. |
| `terraform_generator.py` | Legacy flat-file generator for cached demo fixtures only. |
| `demo.py` | No-cloud demo generator — zero Terraform/AWS calls. |

## architecture/ — the reviewed decision records

| File | Purpose |
| :-- | :-- |
| `requirements.py` | Requirements gate — generation refuses to run without a complete, reviewed record. |
| `architecture_decision.py` | Why a module set was chosen, with alternatives — production synthesis is bound to this. |
| `architecture_model.py` | Six-layer reference-architecture classifier + conformance scoring (any cloud). |
| `discovery.py` | Deterministic Terraform Registry / AWS-docs URL builder so research is grounded, not memory. |

## governance/ — the deploy gate

| File | Purpose |
| :-- | :-- |
| `plan_gate.py` | verify -> plan -> approve -> apply (and destroy). Hash-binds approval to an exact plan. |
| `approval.py` | Gatekeeper / auto-approve gate for side-effecting actions, always audited. |
| `authz.py` | RBAC seam — operator identity + approver allowlist. |
| `audit_chain.py` | Tamper-evident, hash-chained append-only audit log. |
| `audit_logger.py` | Thin operator-facing wrapper around the audit chain. |
| `source_guard.py` | Local source-baseline + manual-edit diff for generated workspaces. |
| `tf_validate.py` | Offline, credential-free `terraform validate` wrapper. |

## cost/ — pricing evidence

| File | Purpose |
| :-- | :-- |
| `bcm_pricing_calculator.py` | AWS BCM Pricing Calculator integration: `prepare` (no AWS calls) -> `run` (gated estimate). |
| `pricing_catalog.py` | Terraform-type -> AWS serviceCode resolution; longest-prefix match against `pricing_data/`. |
| `coverage_audit.py` | Fail-closed gate: every resource type is auto-priced / needs a profile / confirmed free / unresolved — never silently absent. |
| `budget_calculator.py` | Honest cost guidance for the dispatcher's BUDGET intent — never a number of its own. |
| `pricing_data/aws_resource_map.json` | Reviewed resource-type -> serviceCode map. |
| `pricing_data/free_resources.json` | Reviewed allowlist of resource types confirmed to carry no billable SKU. |

## reporting/ — turning a plan into something usable

| File | Purpose |
| :-- | :-- |
| `reporter.py` | Builds the versioned report bundle per plan-hash (architecture/dataflow SVGs, plan/cost/inspect PDFs). |
| `plan_inspector.py` | Reads generated reports — services, resources, IAM roles, source drift. |
| `optimize_analyzer.py` | Per-resource HCL scanner (security/cost/observability findings). |
| `finops_agent.py` | Live cost intelligence over the active cloud (cost, anomalies, CloudTrail correlation). |
| `health_checker.py` | Operational health probes over the active cloud/credential posture. |
| `runs.py` | Run-workspace manager — `runs/<run-id>/` (gitignored, never source control). |
| `dispatcher.py` | Natural-language intent router; dispatches to the right script via subprocess. |
| `minusctl.py` | Operator-facing CLI. Never mutates infrastructure itself. |
| `toolpath.py` | Cross-platform discovery of external CLIs (terraform, aws). |

## providers/ — cloud abstraction (unchanged by this restructure)

`base.py` (the `CloudProvider` contract) + `aws.py` (implemented) + `azure.py` / `gcp.py`
(scaffolds). Selected via `MINUS_CLOUD`. Already its own subpackage before this restructure;
nothing here moved.

---

## Cross-package dependencies

Roughly layered — lower groups depend on higher ones, not the reverse:

```
generation/  --> architecture/, governance/, cost/(none), reporting/
architecture/ --> generation/ (modules.py only)
governance/  --> reporting/ (plan_inspector, toolpath, reporter — lazy), cost/ (coverage_audit — lazy), providers/
cost/        --> governance/ (approval), reporting/ (toolpath, reporter — lazy), providers/
reporting/   --> cost/ (bcm_pricing_calculator, pricing_catalog), governance/ (approval, and via
                  minusctl.py: audit_chain, source_guard, tf_validate), architecture/ (via
                  minusctl.py: architecture_decision, requirements; via reporter.py lazy:
                  architecture_model), generation/ (via minusctl.py: accelerators, demo,
                  workflow; via reporter.py lazy: modules), providers/
providers/   --> reporting/ (toolpath, lazy), cost/ (pricing_catalog, lazy)
```

`minusctl.py` and `reporter.py` are the two files that actually reach into every other
subpackage — `minusctl.py` because it's the CLI surface that fans out to every subcommand,
`reporter.py` because building a report touches the module registry (for names/services) and
the conformance model (for the reference-architecture score). If you're judging blast radius
for a change to `architecture/` or `generation/`, check these two files specifically —
`reporting/`'s dependency isn't just the cost/governance/providers it might look like at a
glance.

There's no strict DAG — `plan_gate.py` (governance/) lazily imports `reporter`/`coverage_audit`
at call time specifically to avoid an import cycle at module-load time (reporter also touches
governance-adjacent state). If you're adding a new cross-package import and hit a circular
import at load time, make it a lazy (function-body) import like the existing ones in
`plan_gate.py`, `synthesizer.py`, and `reporter.py` — don't restructure the packages to avoid it.

## The sys.path bootstrap (read this before adding a new file)

Every file in `core/` is written to work two ways at once, unchanged: as a directly-run script
(`python core/governance/plan_gate.py verify --dir ...`, still the documented CLI form) and as
an installed package module (`core.governance.plan_gate:main`, the console-script form in
`pyproject.toml`). Both need bare, flat imports like `import audit_chain` or `import toolpath`
to resolve — not `from core.governance import audit_chain` — because that's what every existing
file already does and rewriting ~80 import statements across the whole package would be a much
larger, much riskier change than the actual file move.

That works because any file with a cross-subpackage bare import starts with:

```python
_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("generation", "architecture", "governance", "cost", "reporting", "providers"):
    sys.path.insert(0, os.path.join(_CORE_DIR, _sub))
sys.path.insert(0, _CORE_DIR)
```

This puts every subpackage directory directly on `sys.path`, so `import toolpath` finds
`core/reporting/toolpath.py` regardless of which subpackage is doing the importing, and
`from providers.base import get_provider` still resolves because `core/` itself (the parent of
`providers/`) is on the path too. `tests/conftest.py` and `app/dashboard_app.py` do the same
thing once, centrally, for every test/dashboard import.

**If you add a new file with a bare cross-subpackage import, copy this block in** (see
`plan_gate.py` or `bcm_pricing_calculator.py` for a live example). A file whose only local
imports stay inside its own subpackage (e.g. `patterns.py` importing `modules.py`, both in
`generation/`) doesn't need it — Python already puts a directly-run script's own directory on
`sys.path[0]`, and callers that import it as a library will have already run their own bootstrap
first.

**Two other things that break if you move a file one level deeper without checking:**
- Any `os.path.dirname(os.path.abspath(__file__))` chain that walks up to the *repo root* (not
  just `core/`) needs one more `dirname()` than it did when everything lived flat in `core/` —
  see `discovery.py`, `modules.py` (`REPO_ROOT`), `reporter.py` (`assets/architecture-icons`),
  and `bcm_pricing_calculator.py` (`examples/bcm-usage-profile.example.json`).
- `dispatcher.py`'s `INTENT_MAPPING` and `plan_gate.py`'s `SCAN` constant hardcode sibling
  script paths for `subprocess` calls, not imports — those need the subpackage segment added
  explicitly when the target script lives in a different subpackage than the caller.
