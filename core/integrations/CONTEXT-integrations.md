# Core Integrations Subsystem Context — MinusOps

`core/integrations/` holds the outbound tool hooks: Slack, Microsoft Teams, executive email
over SMTP, Confluence, and Jira. The main governance loop stays lean by delegating every
outbound message to one of these single-purpose modules.

Three properties hold across every file here, and a new hook that breaks one is a bug:

1. **Standard library only.** `urllib.request`, `json`, `base64`, `smtplib`, `email.message`.
   No `requests`, no vendor SDK. The base install is dependency-free.
2. **No credential is ever a parameter.** A hook names an environment variable and optionally
   a Secrets Manager ARN. An ARN is an identifier and is safe in a plan or an audit record; a
   token passed as an argument is failure mode FM-02.
3. **Every send is approval-gated and fails soft.** `base_hook.gated()` calls
   `approval.request_approval()` before the sender runs, and every hook returns a result dict
   instead of raising, so a notification can never abort a deploy.

Imports here are flat (`import base_hook`), resolved through the `sys.path` shim at the top of
each file, matching the rest of `core/`. Do not mix in `from integrations import base_hook`:
that loads a second copy of the module that owns the approval gate, and a test that patches
one copy would leave the other ungated.

---

## Alert deduplication

`base_hook.gated()` suppresses an identical `(action, details)` pair within `DEDUP_WINDOW_SECONDS` (300). A failing job that alerts fifty times in ten seconds pages once; the failure mode being prevented is not the noise but the muting that follows it.

The check runs **before** the approval gate, so a human is not prompted fifty times. Suppression returns `ok=True, sent=False, reason="deduplicated"` -- a working cooldown is not a broken integration. Only a delivered alert opens a window: a denial or a failed send does not, because suppressing five minutes of alerts on the strength of a message that never arrived is how an outage goes unreported.

`confluence_hook`, `jira_hook` and `outlook_hook` pass `dedup_window=0`. Republishing an edited page, filing a second ticket, or resending a report are intended actions, not storms.

---

## Result dict contract

Every public hook returns the same shape. Callers branch on `reason` and `sent`, never on the
message text.

| Outcome | `ok` | `status` | `sent` | `reason` |
| :--- | :--- | :--- | :--- | :--- |
| Delivered | `True` | 2xx (250 for SMTP) | `True` | — |
| Approval denied | `False` | 403 | `False` | `not_authorized` |
| Endpoint/credentials unset | `True` | 0 | `False` | `not_configured` |
| Upstream rejected it | `False` | real HTTP code | `False` | — |
| Timed out | `False` | 504 | `False` | — |
| Transport / DNS failure | `False` | 502 | `False` | — |

`not_configured` is `ok: True` on purpose: the operator authorised the action and nothing
failed on our side. Treating "no webhook wired" as a failure would turn a clean run red on
every workstation that has not set the env var.

---

## Flow

```
caller (e.g. finops_agent --notify-slack)
        │
        ▼
  <hook>.send_*()            builds the provider-specific payload
        │
        ▼
  base_hook.gated()          request_approval(action, details, mode)  ──► audit.jsonl
        │  approved                                    │ denied
        ▼                                              ▼
  sender closure                              {"ok": False, 403, not_authorized}
        │                                     (network call never attempted)
        ├─ resolve_secret(ENV_VAR | Secrets Manager ARN)
        │        └─ unset ──► base_hook.not_configured(...)
        ▼
  base_hook.request_json()  ──►  urllib.request.urlopen  ──► result dict
        (outlook_hook is the exception: smtplib.SMTP, not HTTP)
```

---

## File Specifications

### `core/integrations/__init__.py`
- **File Link:** [`__init__.py`](./__init__.py)
- **Purpose:** Package marker; states the three properties above so they are visible at the
  package boundary.
- **Key functions / classes:** None.
- **Inputs / outputs / dependencies:** None.
- **Failure modes:** N/A.

### `core/integrations/base_hook.py`
- **File Link:** [`base_hook.py`](./base_hook.py)
- **Purpose:** Transport, credential resolution, and the approval gate shared by every hook.
  This is the only module in the package that touches `urllib` or `approval`.
- **Key functions / classes:**
  - `resolve_secret(env_var, secret_arn=None)` — env var first, then `secret_arn` or
    `<ENV_VAR>_SECRET_ARN` via `aws secretsmanager get-secret-value`; returns None when
    neither is configured. A JSON secret blob is looked up by the env var name.
  - `basic_auth_header(user_env, token_env, secret_arn=None)` — Atlassian-style Basic header,
    or None if either half is missing.
  - `request_json(url, payload=None, headers=None, method="POST", timeout=10)` — the one
    network call; returns the result dict, never raises.
  - `post_json(url, payload, headers=None, timeout=10)` — POST alias for the webhook hooks.
  - `not_configured(what)` — the `ok: True, sent: False` result for an unwired destination.
  - `gated(action, details, approval_mode, sender)` — approval first; `sender` is a
    zero-argument callable that is not invoked at all when the gate says no.
- **Inputs / outputs / dependencies:** reads any env var a caller names; imports
  `approval.request_approval` (from `core/governance/`) and, lazily and only when an ARN is in
  play, `providers.aws.run_aws`. Writes nothing itself — the audit record is written by
  `approval.py` to `.agents/logs/audit.jsonl`.
- **Failure modes:** HTTPError → the upstream status code with the response body as `error`;
  timeout (bare or wrapped in `URLError`) → 504; other `URLError` → 502; anything else → 500.
  Denied approval → 403 with `reason: not_authorized`. Never raises.

### `core/integrations/slack_hook.py`
- **File Link:** [`slack_hook.py`](./slack_hook.py)
- **Purpose:** Slack incoming-webhook dispatcher and Block Kit formatter. Single
  implementation of the Slack send that used to be inline in
  [`finops_agent.py`](../reporting/finops_agent.py).
- **Key functions / classes:**
  - `build_blocks(text, plan_hash=None, approve_url=None, reject_url=None)` — markdown
    section, optional plan-hash context line, and Approve/Reject buttons whose `value` is the
    plan hash so an interaction handler can confirm which plan was approved.
  - `send_slack_notification(payload, interactive=False, plan_hash=None, approval_mode="gatekeeper", action="send-slack-alert", details=None, secret_arn=None, timeout=10)`
    — posts `payload` (minimally `{"text": ...}`) to the resolved webhook; `interactive=True`
    adds Block Kit while leaving `text` in place as Slack's notification fallback.
- **Inputs / outputs / dependencies:** `SLACK_WEBHOOK_URL` (or
  `SLACK_WEBHOOK_URL_SECRET_ARN`); `base_hook`. Posts to Slack.
- **Failure modes:** the shared contract above. Unset webhook is `not_configured`, not an
  error. The plan's `send_slack_notification(webhook_url, ...)` signature is deliberately not
  implemented: the webhook URL is a bearer credential and is resolved, not passed.

### `core/integrations/teams_hook.py`
- **File Link:** [`teams_hook.py`](./teams_hook.py)
- **Purpose:** Adaptive Card dispatcher for data-quality and quarantine alerts.
- **Key functions / classes:**
  - `build_adaptive_card(title, facts_list, action_url=None, action_title="Open in MinusOps")`
    — wraps the card in the `attachments` envelope with contentType
    `application/vnd.microsoft.card.adaptive`; accepts `(name, value)` pairs or dicts and
    stringifies values, because a FactSet renders a non-string value as blank.
  - `send_teams_card(title, facts_list, action_url=None, approval_mode="gatekeeper", action="send-teams-card", details=None, secret_arn=None, timeout=10)`
- **Inputs / outputs / dependencies:** `TEAMS_WEBHOOK_URL` (or its `_SECRET_ARN`);
  `base_hook`. Posts to a Teams Workflows / Power Automate endpoint.
- **Failure modes:** the shared contract. Sending a bare card object instead of the envelope
  returns 202 and displays nothing — a delivered alert nobody sees — which is why the envelope
  is built here rather than left to callers.

### `core/integrations/outlook_hook.py`
- **File Link:** [`outlook_hook.py`](./outlook_hook.py)
- **Purpose:** Executive email: HTML body plus `.xlsx` attachments, over SMTP. One transport
  reaches both AWS SES (`email-smtp.<region>.amazonaws.com`) and Exchange Online, so
  `SendRawEmail` signing logic is not implemented.
- **Key functions / classes:**
  - `build_message(from_address, to_addresses, subject, body_html, attachments=())` — plain
    text alternative + HTML body + one part per attachment path, typed by `mimetypes` with an
    explicit spreadsheet fallback for `.xlsx`.
  - `send_executive_email(to_addresses, subject, body_html, attachments=(), approval_mode="gatekeeper", action="send-executive-email", details=None, secret_arn=None, timeout=30)`
- **Inputs / outputs / dependencies:** `SMTP_HOST`, `SMTP_PORT` (default 587), `SMTP_FROM`,
  `SMTP_USERNAME`, `SMTP_PASSWORD` (or `SMTP_PASSWORD_SECRET_ARN`); reads the attachment files
  named by the caller; `base_hook` for the gate and secret resolution.
- **Failure modes:** `build_message` raises `OSError` on an unreadable attachment — deliberate,
  because a "sent" executive report with a silently missing workbook is worse than a failed
  send; `send_executive_email` catches it and returns status 400. SMTP errors → 502, connect
  timeouts → 504, unset `SMTP_HOST`/`SMTP_FROM` → `not_configured`.

### `core/integrations/confluence_hook.py`
- **File Link:** [`confluence_hook.py`](./confluence_hook.py)
- **Purpose:** Publishes living architecture documentation to Confluence Cloud, and converts
  Markdown to Atlassian storage XHTML.
- **Key functions / classes:**
  - `markdown_to_storage(markdown_content)` — ATX headings, fenced code blocks (mermaid
    included), pipe tables with header detection, unordered lists, and paragraphs with inline
    code / bold / links. Unsupported syntax degrades to an escaped paragraph.
  - `publish_confluence_page(space_key, page_title, markdown_content, parent_page_id=None, approval_mode="gatekeeper", action="publish-confluence-page", details=None, secret_arn=None, timeout=10)`
    — searches the space by title, then PUTs with `version = current + 1` or POSTs a new page.
    Result carries `page_id` and `page_action` (`created` / `updated`).
- **Inputs / outputs / dependencies:** `CONFLUENCE_BASE_URL`, `CONFLUENCE_USER`,
  `CONFLUENCE_API_TOKEN` (or its `_SECRET_ARN`); `base_hook`. Calls
  `GET/POST /wiki/rest/api/content` and `PUT /wiki/rest/api/content/{id}`.
- **Failure modes:** upsert rather than blind create, because Confluence refuses a duplicate
  title in a space (the second run of a docs job would otherwise fail) and 409s on a version
  number that is not current+1. Non-JSON search results → 502. A mermaid fence becomes a code
  macro with language `mermaid`, not a Mermaid macro, since that macro is a marketplace app
  that may not be installed.

### `core/integrations/jira_hook.py`
- **File Link:** [`jira_hook.py`](./jira_hook.py)
- **Purpose:** Change-ticket creator. Submits to Jira Cloud when wired; otherwise writes the
  ticket payload to disk. Single implementation of the ticket path that used to be inline in
  [`finops_agent.py`](../reporting/finops_agent.py).
- **Key functions / classes:**
  - `_adf(text)` — wraps the description in Atlassian Document Format, which REST v3 requires;
    a plain string 400s.
  - `build_ticket(project_key, summary, description, plan_hash=None, priority="High")` — the
    flat record written to disk; `plan_hash` appears only when supplied.
  - `create_change_ticket(project_key, summary, description, plan_hash=None, priority="High", out_dir=None, filename=None, approval_mode="gatekeeper", action="create-jira-ticket", details=None, secret_arn=None, timeout=10)`
    — result carries `issue_key` when submitted, or `path` and `ticket` when written.
- **Inputs / outputs / dependencies:** `JIRA_BASE_URL`, `JIRA_USER`, `JIRA_TOKEN` (or its
  `_SECRET_ARN`), `JIRA_ISSUE_TYPE` (default `Task` — `Change` is not present in every
  project); `base_hook`. Writes `<out_dir>/<filename>` when unwired, defaulting to
  `.agents/logs/`.
- **Failure modes:** the shared contract; an unwritable payload path → 500. The
  prepare-to-disk fallback is deliberate, not a stub: an unconfigured Jira must still leave
  evidence of what was authorised and what it said.

---

## Deliberate omissions

- **No `artifactory_hook.py`.** The implementation plan lists one, but no functional
  requirement anywhere asks for binary promotion by digest, and no Artifactory instance is
  connected. A hook for a system nobody has wired is exactly the boilerplate this package
  exists to avoid. Add it when a promotion requirement names a repository.
- **No `BaseIntegrationHook` class.** The plan sketches one; there is a single implementation
  and no state to carry, so `base_hook`'s module functions do the job without a `self`.
- **No retry / backoff.** Callers get the status code and decide. Retrying an approved send
  inside the hook would re-send without re-approval on the second attempt.

## Hygiene audit

- **Dead code:** None.
- **Unwired:** `teams_hook.py`, `outlook_hook.py`, and `confluence_hook.py` are exercised by
  [`tests/test_integrations.py`](../../tests/test_integrations.py) but no production caller
  invokes them yet — Phase 1 builds the hooks, later phases wire the subagents that call them.
  `slack_hook.py` and `jira_hook.py` are live via `finops_agent.py`.
- **Duplication:** None. The Slack and Jira logic was extracted from `finops_agent.py`, which
  now calls in here rather than keeping its own copy; `finops_agent.py` no longer imports
  `urllib` or `approval` at all.
- **Broken references:** None.
