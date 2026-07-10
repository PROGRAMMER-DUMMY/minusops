# Terraform MCP server — agent-neutral wiring

MinusOps uses HashiCorp's official Terraform MCP server (`hashicorp/terraform-mcp-server`) as an
**authoring-time** aid: live Terraform Registry docs, module search, and provider schema lookups
so modules get written against current provider versions instead of stale training data (see
`docs/project_plan.md`'s Phase E addendum for why this was wired). It is not part of any
plan/apply path and has no credentials.

The server itself is just a container speaking MCP — completely agent-agnostic. Only the
*registration* (telling a given CLI agent that this server exists) differs per tool. This
directory exists so that registration is reproducible from one file, not re-derived per agent.

## Canonical definition

**[`terraform-mcp.json`](terraform-mcp.json)** is the single source of truth:

```json
{
  "mcpServers": {
    "terraform": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "hashicorp/terraform-mcp-server:1.0.0", "--toolsets=registry"],
      "env": {},
      "transport": "stdio"
    }
  }
}
```

- **Pinned image** (`:1.0.0`, not `:latest`) — every agent resolves the identical container.
- **`--toolsets=registry` only** — docs/module-search/provider-schema tools exposed; no
  `create_run`/`apply_run`/`discard_run`/`cancel_run` (those live in the `terraform` toolset,
  not requested here), so this server cannot touch any Terraform Cloud/Enterprise workspace.
- **No credentials** — `TFE_TOKEN` is only for private-registry/HCP Terraform operations, unused
  here; public Terraform Registry lookups need no auth.
- **`ENABLE_TF_OPERATIONS` deliberately unset** (defaults off) — belt-and-suspenders on top of
  the toolset restriction above.

Every per-agent snippet below is a direct transcription of this file into that agent's config
syntax — same `command`/`args`, no substantive changes. **They are derived copies, not
independently maintained ones: `terraform-mcp.json` is updated first, and every snippet below is
regenerated from it — never edit a per-agent snippet in isolation, or it will silently drift from
the source of truth the next time the docker args change.**

**Maintenance:** the image is pinned to `1.0.0` (deliberately, not `:latest` — see above), so when
HashiCorp ships a new Terraform MCP release this pin goes stale until bumped by hand. To update:
bump the version in `terraform-mcp.json`, re-verify against a live registration (e.g. `claude mcp
get terraform` should match the new `command`/`args` byte-for-byte), then propagate the same bump
into every per-agent snippet below. Done.

## Per-agent registration

### Claude Code

CLI (writes to this project's local scope):
```bash
claude mcp add terraform -- docker run -i --rm hashicorp/terraform-mcp-server:1.0.0 --toolsets=registry
```

Or drop `terraform-mcp.json`'s `mcpServers` block into a project-scoped `.mcp.json` at the repo
root (shareable/commit-able, prompts each user for approval on first use — do this instead of
the CLI form if you want the registration to travel with the repo rather than living in each
contributor's local Claude Code config).

### Codex (OpenAI Codex CLI)

`~/.codex/config.toml` (or project-scoped `.codex/config.toml`):
```toml
[mcp_servers.terraform]
command = "docker"
args = ["run", "-i", "--rm", "hashicorp/terraform-mcp-server:1.0.0", "--toolsets=registry"]
```

Or via CLI: `codex mcp add terraform -- docker run -i --rm hashicorp/terraform-mcp-server:1.0.0 --toolsets=registry`

Verify with `/mcp` inside the Codex TUI.

### Generic `mcpServers` JSON (most clients)

Claude Desktop, Cursor, Windsurf, VS Code's built-in MCP support, and Cline all read this exact
shape — point them at `terraform-mcp.json` directly, or copy its `mcpServers` block into
whichever file the client expects:

- **Cline**: paste into `cline_mcp_settings.json` (open via the Cline panel → MCP Servers icon →
  Configure).
- **Continue**: either copy `terraform-mcp.json` itself into `.continue/mcpServers/` (Continue
  auto-picks up Claude-Desktop-style JSON files dropped there), or add the equivalent block under
  `mcpServers:` in `config.yaml`:
  ```yaml
  mcpServers:
    - name: terraform
      command: docker
      args: ["run", "-i", "--rm", "hashicorp/terraform-mcp-server:1.0.0", "--toolsets=registry"]
  ```

### Goose

`~/.config/goose/config.yaml`, under `extensions:`:
```yaml
extensions:
  terraform:
    cmd: docker
    args: ["run", "-i", "--rm", "hashicorp/terraform-mcp-server:1.0.0", "--toolsets=registry"]
    enabled: true
    type: stdio
```

### Google Antigravity CLI ("Agy CLI")

Antigravity CLI and IDE share one config: `~/.gemini/config/mcp_config.json`. Same
`mcpServers` shape as the canonical file — copy it in directly. Manage/verify with `/mcp` inside
the CLI.

### Aider

**Status, stated plainly rather than guessed:** Aider's native MCP support is unsettled as of
this writing — some 2025 builds added it, but other reports as of mid-2026 say the config
reference lists no MCP options and the relevant PRs were closed unmerged. Don't trust a single
snippet here without checking your installed version's own `--help`/config docs first.

- If your Aider build has native MCP support: it's YAML-based (`.aider.conf.yml`), with
  `mcp-server` entries specifying `name`/`command`/`args`/env — transcribe the canonical
  definition's `command`/`args` directly into that shape.
- If it doesn't: the community bridge [`mcpm-aider`](https://github.com/lutzleonhardt/mcpm-aider)
  is the documented workaround, not something this repo can configure for you directly.

## Transport: stdio by default, HTTP as an optional shared-instance upgrade

**Default is stdio** (`docker run -i --rm ...`) for a reason: every MCP client supports it, no
setup beyond the snippets above, and each agent gets its own ephemeral container with no shared
state and no open port. This is the right default for a single developer running one or more
CLI agents locally.

**Streamable HTTP is an optional upgrade**, only worth it when multiple agents or people want to
share one long-lived instance instead of each spinning up its own container. The Terraform MCP
server supports it (`terraform-mcp-server http` instead of `stdio`, or the equivalent Docker
`-p` port mapping) — not configured here because it's a different operational posture, not a
drop-in replacement:

- If the HTTP endpoint is bound to `localhost` only, it's roughly equivalent in risk to stdio —
  fine for "I run three agents against one shared container on my own machine."
- **If it's exposed beyond localhost** (another machine, a team-shared host), it needs auth, TLS,
  and an IP allowlist before anything connects to it — none of which the canonical stdio
  definition in this directory provides. Set that up deliberately and document it separately;
  don't casually flip a stdio config to HTTP and open a port.
