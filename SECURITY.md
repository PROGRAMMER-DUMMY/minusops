# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately. Do **not** open a public issue for
security reports.

- Email: security@your-org.example  (replace with your security contact)
- Include: affected version/commit, reproduction steps, and impact.
- We aim to acknowledge within 3 business days and to provide a remediation timeline
  after triage.

## Supported versions

The latest released minor version receives security fixes. Pre-1.0 releases may require
upgrading to the newest version to receive a fix.

## Handling model

- Releases are built in CI and published with a Sigstore-backed build-provenance
  attestation plus a CycloneDX SBOM (see `.github/workflows/release.yml`). Verify the
  attestation before deploying a release artifact.
- The control plane never stores cloud credentials; see
  [`docs/security_model.md`](./docs/security_model.md) for trust boundaries and non-goals.

---

## Running MinusOps under an agentic CLI

MinusOps ships an execution guardrail at
[`core/governance/agent_guardrails.py`](./core/governance/agent_guardrails.py). It evaluates a
command before an agent runs it and refuses three classes: destructive commands, commands that
need a verified human, and any binary not on the allowlist.

**It is not a sandbox, and the distinction matters when you configure a runtime.** Measured
against one destructive action expressed five ways, it catches three. The two it misses are
interpreter paths -- `python cleanup.py` is allowed because this project runs pytest and its
own CLIs through `python`, and that script can call boto3. No allowlist of binaries closes an
interpreter. **The IAM credential the agent holds is the actual boundary**; this guardrail
gives a fast, legible refusal in front of it and reduces the blast radius of a wrong turn.

### The two parts, and which one ports

| Part | Portable? |
| :--- | :--- |
| `core/governance/agent_guardrails.py` | Yes. Standard library only, no framework imports. Takes a command string, returns `{allowed, rule, reason, requires_human}`. Any runtime in any language can call it. |
| `.claude/hooks/guardrails.py` | No. It speaks Claude Code's `PreToolUse` payload (`tool_name`, `tool_input`) and exit-code protocol. Another runtime needs its own adapter -- about 40 lines, same shape. |

The harder constraint is not the adapter. It is whether your runtime has a pre-execution hook
at all: without one there is nowhere to put this, and the IAM split is your only control.

### Adapter contract

Whatever the runtime, an adapter does three things:

1. Read the tool call. Extract the command string (a shell tool) or the target path (a write
   tool).
2. Call `evaluate(command, human_authorized=...)` or
   `evaluate_write(path, run_id=..., workspace=...)`.
3. Map the decision to the runtime's block signal, and put `reason` where the agent will read
   it -- a refusal the agent cannot see is a refusal it will retry.

`.claude/hooks/guardrails.py` uses exit `0` to allow, `2` to block, and `1` for a payload it
could not parse. That third state is deliberate: a hook that cannot read its input must not
silently allow (which hides the outage) and must not block everything (which bricks the
session).

### Permissions to configure, by runtime

Runtimes differ in where the list lives and what it is called. What follows is what to put in
it, not the file format -- check your runtime's own documentation for that.

**Allow.** The agent must be able to do its job or the guardrail gets switched off:

```
minusctl *                 the whole control plane; `gate apply` is separately gated below
minus-bcm, minus-gate, minus-runs, minus-demo, minus-resolve, minus-workflow,
minus-accelerator, minus-update-module, minus-schema-watch
terraform init|validate|fmt|plan|show|providers|output|version|state list
python, python3, pytest, pip          the dev loop, and the capabilities with no
                                      subcommand yet (synthesizer.py, patterns.py,
                                      modules.py, discovery.py, pillars.py,
                                      coverage_audit.py, health_checker.py)
git status|diff|log|add|commit|push|branch|checkout|stash|rev-parse|show
ls, cat, head, tail, grep, rg, find, wc, sort, uniq, sed, awk, diff, jq, echo,
mkdir, cp, mv, touch, which, env, date
aws sts|s3 ls|ce|cloudtrail|logs        read paths only
```

**Deny, and do not add an exception:**

```
rm -rf / rm --recursive --force         recursive force delete
rmdir /s, rmdir -r
terraform destroy
terraform state rm
terraform force-unlock
git reset --hard
git push --force (and --force-with-lease)
git clean -f / -d / -x
aws s3 rb --force
aws s3 rm --recursive
DROP TABLE, DROP DATABASE, TRUNCATE TABLE
```

**Require a human, never an agent flag:**

```
minusctl gate apply
minusctl prove --execute
```

These two are the only mutating subcommands in the CLI. Both route through `approval.py` and
land in the audit chain. A human approving a plan is not consent to `rm -rf`, so
`human_authorized` never unlocks the destructive list -- conflating the two turns one
approval into a blank cheque.

**Do not deny these**, however tempting the shape:

- `rm` on its own. A single file removal is ordinary work; `FS-01` already refuses the
  recursive force form, which is the shape that destroys work.
- `git push` without `--force`. Blocking it stops ordinary delivery, and the force variants
  are already refused.
- `terraform state list`. It reads.
- `python`. Blocking it stops the test suite, the console, and every capability that has no
  `minusctl` subcommand yet.

### Per-runtime notes

**Google Antigravity.** Use **Request Review with Allow List**, not Always Proceed. That mode
is a positive security model: nothing auto-executes except what the Allow list names, and
everything else prompts a human rather than hard-failing -- which suits this project, because
the capabilities with no `minusctl` subcommand yet are invoked by path and would otherwise
trip a narrow list constantly. Put the Allow set above into the Allow list and the Deny set
into the Deny list. For the CLI, the same lists live in
`~/.gemini/antigravity-cli/settings.json`. Prefer the **Review-driven** autonomy level for a
production repo.

One conflict to resolve deliberately: Antigravity's suggested starter Deny list includes a
blanket `rm`. Under Request Review that is survivable (it prompts), but do not carry a blanket
`rm` into a runtime that hard-blocks -- removing a single build artefact is ordinary work, and
`FS-01` already refuses the recursive force form. Deny `rm -rf`, not `rm`.

**Claude Code.** Already wired: `.claude/settings.json` registers
`.claude/hooks/guardrails.py` as a `PreToolUse` hook matching `Bash|Write|Edit|NotebookEdit`.
Nothing further to configure. The `permissions.allow` list in `settings.local.json` is a
separate, per-developer convenience and is not the guardrail.

**Cursor, Windsurf and other MCP-first editors.** These approve *tools*, not arbitrary shell
commands, so there is no equivalent chokepoint for a command allowlist. Rely on the IAM split.

**Codex.** Has its own sandbox and approval model. Work within it rather than layering this
on top.

**Anything else.** If the runtime has no pre-execution hook, this guardrail cannot be
installed there at all, and the plan-only IAM role is your only control. That is not a
degraded position -- it is the control that actually holds; the guardrail was always the
legible layer in front of it.

### Do not block the console or the dev loop

`minusctl console` binds to loopback, makes no cloud calls, and its one write path is a
reviewed architecture change through `reconciler.py`. A runtime that blocks long-running
processes will kill it -- allow `minusctl console start|stop --background`.

[`tests/test_guardrail_self_block.py`](./tests/test_guardrail_self_block.py) walks 110
commands across the whole `minusctl` surface, the console, the dev loop, the no-front-door
capabilities and terraform/aws read paths, and asserts none of them is refused while the
destructive set still is. Run it after editing the allowlist.

### Known false-positive shapes

Report these rather than working around them:

- Shell keywords -- `while`, `for`, `case`, `if` -- read as unknown binaries. A loop written
  inline is refused; put it in a `.py` file instead.
- A binary genuinely needed and not on the list is refused as `ALLOW-01` naming it. Adding it
  is a reviewed decision: append to `_ALLOWED_COMMANDS` with a comment saying why.

### What the guardrail does not do

- It only stops callers that ask it. Nothing intercepts a process that shells out directly.
- It cannot enumerate every spelling. What it catches reliably is the shapes commands
  actually arrive in: padded whitespace, reordered or long-form flags, a chained `&&`, a
  `bash -c` wrapper, a heredoc fed to a shell, a `$(...)` substitution.
- Real containment is an OS-level jail, a read-only mount, or credentials that cannot perform
  the action. Prefer a plan-only IAM role for the agent and an apply role it never holds.
