"""
Outbound integration hooks (Slack, Teams, Outlook/SMTP, Confluence, Jira).

Every module here is stdlib-only and fails soft: a hook returns a result dict and never
raises into a caller mid-deploy. Every outbound send is a side effect and is routed through
`base_hook.gated()` -> `approval.request_approval()` first, so a notification is audited on
the same tamper-evident chain as an infrastructure mutation.

Credentials are never function parameters (failure mode FM-02). A hook names an environment
variable and optionally a Secrets Manager ARN; `base_hook.resolve_secret()` reads it at call
time so no token can land in a plan, a log line, or an audit record.

Depends on: nothing (package marker only)
Shells out to: nothing
Used by: core/reporting/finops_agent.py, tests/test_integrations.py
"""
