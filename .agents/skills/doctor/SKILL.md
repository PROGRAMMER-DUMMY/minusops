---
name: doctor
description: Day-0 environment diagnostics before any infrastructure work. Runs the full pre-flight — CLI binaries and their version floors, AWS caller identity and credential posture (ASIA temporary vs AKIA long-term), the seeded Terraform dependency lock file and shared plugin cache, and whether a G9 emulator is listening — then reports one verdict. Use when someone asks "is my setup ready?", "why is terraform init slow?", "why does the gate say G9 not configured?", before the first synthesis on a new machine, and whenever a gate fails for a reason that smells environmental rather than architectural.
---

# Doctor — Day-0 Environment Pre-Flight

Run this **before** the first synthesis on any machine, and any time a gate fails in a way that
looks like tooling rather than architecture. Nearly every "the generator is broken" report this
project has seen was an environment fact: a missing binary, static credentials, an unseeded lock
file, or an emulator nobody started.

One command does the whole thing:

```bash
python core/reporting/minusctl.py doctor          # human-readable
python core/reporting/minusctl.py doctor --json   # machine-readable, for a gate or CI step
```

Exit code is **0 unless some check is `error`**. `warn` never fails the command — a warn means
"this machine can still plan, but something is degraded and you should know which thing".

## What it checks, and why each one is here

| Check | Status meaning |
| :--- | :--- |
| **python** | `warn` below 3.10 (the floor `pyproject.toml` declares). |
| **terraform** | `error` if absent or **below 1.5** — that is the `required_version` the synthesizer writes into every composed root, so an older binary cannot plan what this repo generates. |
| **aws cli** | `error` if absent or **below v2**. |
| **cloud credentials** | Connected, account, ARN, and posture. **`warn` on long-term (`AKIA…`) keys even when they work** — an unattended auto-approve run holding static keys can apply real infrastructure. `ok` only for temporary/assumed (`ASIA…`) credentials. |
| **opa** | `warn` if absent; the Rego gate degrades to warn-only. |
| **tflint** | `warn` if absent; provider-level lint findings are skipped. |
| **policy scanners** | `warn` unless checkov or trivy is present. `MINUS_POLICY_MODE=production` requires one. |
| **terraform lock seed** | `warn` if `.agents/terraform.lock.hcl` is missing. Without it every fresh run workspace re-downloads ~855 MB per provider instead of using the shared cache, because with no lock entry Terraform must reach the registry for official checksums (MINUS-138). |
| **g9 emulator** | Five distinct states — see below. |
| **python packages** | `warn` if dash/plotly are missing; only the dashboard needs them. |

### The five G9 states

They fail independently and the fixes differ, so they are never collapsed into one line:

| `MINUS_G9_EMULATOR` | Port 4566 | Reported |
| :--- | :--- | :--- |
| a supported name | listening | `ok` |
| a supported name | dead | `warn` — **the worst case**: the gate looks configured, then fails on every plan |
| unset | listening | `warn` — something is running, it just has no name |
| unset | dead | `warn` — G9 is skipped entirely |
| an unrecognized name | either | `warn` — `ephemeral_apply` BLOCKS on one rather than guessing, so a typo disables G9 silently and forever |

## Reading the result

Treat it as a gate on your own next action:

- **any `error`** — stop. Synthesis will produce Terraform this machine cannot plan.
- **`warn` on credentials** — you may proceed, but do not run an auto-approve apply. Static keys
  plus auto-approve is the combination that applies real infrastructure without a human.
- **`warn` on lock seed** — proceed, but expect multi-minute `terraform init` on every new run.
- **`warn` on g9/opa/scanners** — proceed. These are assurance layers; their absence is disclosed
  in the gate output rather than hidden, which is the point of reporting it here first.
- **all `ok`** — the environment is ready. Go to [`grill-me`](../grill-me/SKILL.md), then
  [`architect`](../architect/SKILL.md).

Report the checks that are not `ok` and what each one blocks. Do not paste the whole table back
at someone whose environment is clean — say it is clean and move on.

## What this deliberately does NOT check

- **`configs/teams.yaml`** — no such file exists in this repo and nothing reads one. A check for
  it could only ever report on something with no consumer, which is noise, not diagnostics. If a
  team directory is added later, add the check with it.
- **Whether AWS credentials can actually do anything.** Doctor reads identity and posture; it
  never probes permissions, because that means live API calls with side effects on someone's
  CloudTrail. The plan gate is where authorization gets tested, against a real plan.
- **Docker daemon health beyond the port.** A listening 4566 is the fact that matters. Docker
  Desktop can be running with every process alive and still have a wedged daemon — observed on
  this project, 2026-08-18 — so process presence proves nothing and the port probe is the honest
  signal.

## When a check contradicts what you were told

Trust the check. The 2026-08-18 G9 ticket asserted "Docker Desktop is installed and running";
every process was alive, the named pipe existed, the WSL distro was Running — and `docker
version` hung past five minutes. `doctor` reported `nothing on localhost:4566`, which was the
accurate description of that machine. Report the discrepancy rather than working around it.
