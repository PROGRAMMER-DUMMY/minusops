"""
Real-behavior tests for core/integrations/ — the outbound hooks (Slack, Teams, Outlook,
Confluence, Jira).

Nothing here reaches the network: `urllib.request.urlopen` is replaced in base_hook (the one
module that owns transport) and smtplib.SMTP is replaced in outlook_hook. The approval gate is
stubbed per test, and one test per hook asserts the gate actually blocks — a hook that sends
first and checks approval afterwards would pass a happy-path test and fail these.
"""
import json
import socket
import urllib.error

import pytest

import base_hook
import slack_hook
import teams_hook
import outlook_hook
import confluence_hook
import jira_hook


class FakeResponse:
    """Context-manager stand-in for the object urlopen returns."""

    def __init__(self, status=200, body=b"ok"):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def approve(monkeypatch):
    """Approve every request, and record what the gate was asked to authorise."""
    seen = []

    def _approve(action, details, mode="gatekeeper"):
        seen.append((action, details, mode))
        return True

    monkeypatch.setattr(base_hook, "request_approval", _approve)
    return seen


@pytest.fixture
def deny(monkeypatch):
    monkeypatch.setattr(base_hook, "request_approval", lambda *a, **k: False)


@pytest.fixture
def capture_http(monkeypatch):
    """Capture outbound requests and return a scripted response."""
    calls = []
    box = {"response": FakeResponse(200, b'{"id": "555"}'), "raise": None}

    def fake_urlopen(req, timeout=None):
        calls.append({"url": req.full_url, "method": req.get_method(), "timeout": timeout,
                      "headers": dict(req.header_items()),
                      "body": json.loads(req.data.decode("utf-8")) if req.data else None})
        if box["raise"] is not None:
            raise box["raise"]
        return box["response"]

    monkeypatch.setattr(base_hook.urllib.request, "urlopen", fake_urlopen)
    box["calls"] = calls
    return box


def http_error(code=500, body=b"boom"):
    return urllib.error.HTTPError("https://example.invalid", code, "err", {},
                                  __import__("io").BytesIO(body))


# ---------------------------------------------------------------------------
# base_hook: transport contract every other hook inherits
# ---------------------------------------------------------------------------
def test_request_json_success_returns_status_and_body(capture_http):
    res = base_hook.request_json("https://example.invalid/x", payload={"a": 1})
    assert res == {"ok": True, "status": 200, "body": '{"id": "555"}'}
    call = capture_http["calls"][0]
    assert call["method"] == "POST"
    assert call["body"] == {"a": 1}
    assert call["headers"]["Content-type"] == "application/json"


def test_request_json_http_error_returns_real_code_not_500(capture_http):
    capture_http["raise"] = http_error(429, b"rate limited")
    res = base_hook.request_json("https://example.invalid/x", payload={})
    assert res["ok"] is False
    assert res["status"] == 429          # the upstream code, not a flattened 500
    assert "rate limited" in res["error"]


def test_request_json_timeout_returns_504_and_does_not_raise(capture_http):
    capture_http["raise"] = socket.timeout("timed out")
    res = base_hook.request_json("https://example.invalid/x", payload={}, timeout=3)
    assert res == {"ok": False, "status": 504, "error": "timed out after 3s"}


def test_request_json_urlerror_wrapping_a_timeout_is_still_504(capture_http):
    capture_http["raise"] = urllib.error.URLError(socket.timeout("timed out"))
    res = base_hook.request_json("https://example.invalid/x", payload={}, timeout=5)
    assert res["status"] == 504


def test_request_json_transport_failure_returns_502(capture_http):
    capture_http["raise"] = urllib.error.URLError("name resolution failed")
    res = base_hook.request_json("https://example.invalid/x", payload={})
    assert res["ok"] is False and res["status"] == 502


def test_resolve_secret_prefers_env_var_over_secrets_manager(monkeypatch):
    monkeypatch.setenv("MINUS_TEST_TOKEN", "  from-env  ")
    assert base_hook.resolve_secret("MINUS_TEST_TOKEN") == "from-env"


def test_resolve_secret_falls_back_to_secrets_manager_arn(monkeypatch):
    monkeypatch.delenv("MINUS_TEST_TOKEN", raising=False)
    seen = {}

    import providers.aws as aws

    def fake_run_aws(args, timeout=20):
        seen["args"] = args
        return True, "from-secrets-manager", ""

    monkeypatch.setattr(aws, "run_aws", fake_run_aws)
    value = base_hook.resolve_secret("MINUS_TEST_TOKEN", "arn:aws:secretsmanager:::secret:x")
    assert value == "from-secrets-manager"
    assert "get-secret-value" in seen["args"]
    assert "arn:aws:secretsmanager:::secret:x" in seen["args"]


def test_resolve_secret_returns_none_when_nothing_configured(monkeypatch):
    monkeypatch.delenv("MINUS_TEST_TOKEN", raising=False)
    monkeypatch.delenv("MINUS_TEST_TOKEN_SECRET_ARN", raising=False)
    assert base_hook.resolve_secret("MINUS_TEST_TOKEN") is None


def test_gated_never_calls_sender_when_approval_is_denied(deny):
    called = []
    res = base_hook.gated("act", "details", "gatekeeper", lambda: called.append(1) or {"ok": True})
    assert called == []
    assert res["reason"] == "not_authorized"
    assert res["ok"] is False and res["status"] == 403


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------
def test_slack_posts_payload_to_webhook(monkeypatch, approve, capture_http):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.invalid/T/B/X")
    res = slack_hook.send_slack_notification({"text": "anomaly"}, approval_mode="auto-approve")
    assert res["ok"] is True and res["sent"] is True
    call = capture_http["calls"][0]
    assert call["url"] == "https://hooks.slack.invalid/T/B/X"
    assert call["body"] == {"text": "anomaly"}
    assert approve[0][0] == "send-slack-alert"


def test_slack_denied_sends_nothing(monkeypatch, deny, capture_http):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.invalid/T/B/X")
    res = slack_hook.send_slack_notification({"text": "anomaly"})
    assert res["reason"] == "not_authorized"
    assert capture_http["calls"] == []


def test_slack_unconfigured_webhook_is_ok_but_not_sent(monkeypatch, approve, capture_http):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL_SECRET_ARN", raising=False)
    res = slack_hook.send_slack_notification({"text": "anomaly"}, approval_mode="auto-approve")
    assert res["ok"] is True and res["sent"] is False
    assert res["reason"] == "not_configured"
    assert capture_http["calls"] == []


def test_slack_http_error_is_returned_not_raised(monkeypatch, approve, capture_http):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.invalid/T/B/X")
    capture_http["raise"] = http_error(404, b"no_service")
    res = slack_hook.send_slack_notification({"text": "anomaly"}, approval_mode="auto-approve")
    assert res["ok"] is False and res["status"] == 404 and res["sent"] is False


def test_slack_timeout_is_returned_not_raised(monkeypatch, approve, capture_http):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.invalid/T/B/X")
    capture_http["raise"] = socket.timeout("timed out")
    res = slack_hook.send_slack_notification({"text": "anomaly"}, approval_mode="auto-approve")
    assert res["status"] == 504 and res["sent"] is False


def test_slack_interactive_payload_carries_plan_hash_and_both_buttons(monkeypatch, approve,
                                                                     capture_http):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.invalid/T/B/X")
    slack_hook.send_slack_notification({"text": "plan ready"}, interactive=True,
                                       plan_hash="abc123", approval_mode="auto-approve")
    blocks = capture_http["calls"][0]["body"]["blocks"]
    actions = [b for b in blocks if b["type"] == "actions"][0]["elements"]
    assert [a["action_id"] for a in actions] == ["minusops_approve", "minusops_reject"]
    assert all(a["value"] == "abc123" for a in actions)
    assert any("abc123" in json.dumps(b) for b in blocks if b["type"] == "context")
    # text survives as the notification fallback
    assert capture_http["calls"][0]["body"]["text"] == "plan ready"


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------
def test_teams_card_is_wrapped_in_the_attachments_envelope(monkeypatch, approve, capture_http):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://teams.invalid/hook")
    res = teams_hook.send_teams_card("DQ failure", [("Rows quarantined", 42)],
                                     action_url="https://runs.invalid/r1",
                                     approval_mode="auto-approve")
    assert res["ok"] is True and res["sent"] is True
    body = capture_http["calls"][0]["body"]
    attachment = body["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard"
    facts = [b for b in card["body"] if b["type"] == "FactSet"][0]["facts"]
    assert facts == [{"title": "Rows quarantined", "value": "42"}]  # stringified, not 42
    assert card["actions"][0]["url"] == "https://runs.invalid/r1"


def test_teams_denied_sends_nothing(monkeypatch, deny, capture_http):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://teams.invalid/hook")
    res = teams_hook.send_teams_card("DQ failure", [])
    assert res["reason"] == "not_authorized"
    assert capture_http["calls"] == []


def test_teams_http_error_and_timeout_are_soft(monkeypatch, approve, capture_http):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://teams.invalid/hook")
    capture_http["raise"] = http_error(400, b"bad card")
    assert teams_hook.send_teams_card("t", [], approval_mode="auto-approve")["status"] == 400
    capture_http["raise"] = socket.timeout("timed out")
    assert teams_hook.send_teams_card("t", [], approval_mode="auto-approve")["status"] == 504


def test_teams_unconfigured_webhook_is_ok_but_not_sent(monkeypatch, approve, capture_http):
    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("TEAMS_WEBHOOK_URL_SECRET_ARN", raising=False)
    res = teams_hook.send_teams_card("t", [], approval_mode="auto-approve")
    assert res["ok"] is True and res["sent"] is False and capture_http["calls"] == []


# ---------------------------------------------------------------------------
# Outlook / SMTP
# ---------------------------------------------------------------------------
def _xlsx(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"PK\x03\x04fake-workbook")
    return str(path)


def test_build_message_attaches_xlsx_with_the_spreadsheet_mime_type(tmp_path):
    path = _xlsx(tmp_path, "executive_project_summary.xlsx")
    msg = outlook_hook.build_message("finops@company.invalid", ["cto@company.invalid"],
                                     "Monthly", "<p>hi</p>", [path])
    attachments = [p for p in msg.iter_attachments()]
    assert len(attachments) == 1
    part = attachments[0]
    assert part.get_filename() == "executive_project_summary.xlsx"
    assert part.get_content_type() == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert part.get_payload(decode=True) == b"PK\x03\x04fake-workbook"
    assert msg["To"] == "cto@company.invalid"
    body = msg.get_body(preferencelist=("html",))
    assert "<p>hi</p>" in body.get_content()


def test_build_message_carries_both_workbooks(tmp_path):
    msg = outlook_hook.build_message(
        "finops@company.invalid", ["cto@company.invalid", "cfo@company.invalid"],
        "Monthly", "<p>hi</p>",
        [_xlsx(tmp_path, "executive_project_summary.xlsx"),
         _xlsx(tmp_path, "pipeline_detailed_ledger.xlsx")])
    names = sorted(p.get_filename() for p in msg.iter_attachments())
    assert names == ["executive_project_summary.xlsx", "pipeline_detailed_ledger.xlsx"]
    assert msg["To"] == "cto@company.invalid, cfo@company.invalid"


def test_build_message_raises_on_a_missing_attachment(tmp_path):
    # A dropped workbook must not be silently omitted from an executive report.
    with pytest.raises(OSError):
        outlook_hook.build_message("a@b.invalid", ["c@d.invalid"], "s", "<p>x</p>",
                                   [str(tmp_path / "does-not-exist.xlsx")])


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.started_tls = False
        self.login_args = None
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)
        return {}


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(outlook_hook.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def _smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "email-smtp.eu-west-1.amazonaws.invalid")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_FROM", "finops@company.invalid")
    monkeypatch.setenv("SMTP_USERNAME", "AKIAFAKEUSER")
    monkeypatch.setenv("SMTP_PASSWORD", "fake-smtp-password")


def test_executive_email_sends_over_starttls_with_env_credentials(monkeypatch, approve,
                                                                  fake_smtp, tmp_path):
    _smtp_env(monkeypatch)
    res = outlook_hook.send_executive_email(
        ["cto@company.invalid"], "Monthly FinOps", "<p>report</p>",
        [_xlsx(tmp_path, "pipeline_detailed_ledger.xlsx")], approval_mode="auto-approve")
    assert res["ok"] is True and res["sent"] is True and res["status"] == 250
    smtp = fake_smtp.instances[0]
    assert smtp.started_tls is True
    assert smtp.login_args == ("AKIAFAKEUSER", "fake-smtp-password")
    assert len(smtp.sent) == 1


def test_executive_email_denied_opens_no_connection(monkeypatch, deny, fake_smtp):
    _smtp_env(monkeypatch)
    res = outlook_hook.send_executive_email(["cto@company.invalid"], "s", "<p>x</p>")
    assert res["reason"] == "not_authorized"
    assert fake_smtp.instances == []


def test_executive_email_unconfigured_host_is_ok_but_not_sent(monkeypatch, approve, fake_smtp):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    res = outlook_hook.send_executive_email(["cto@company.invalid"], "s", "<p>x</p>",
                                            approval_mode="auto-approve")
    assert res["ok"] is True and res["sent"] is False and res["reason"] == "not_configured"
    assert fake_smtp.instances == []


def test_executive_email_smtp_failure_is_returned_not_raised(monkeypatch, approve, fake_smtp):
    _smtp_env(monkeypatch)

    def boom(self, msg):
        import smtplib
        raise smtplib.SMTPSenderRefused(550, b"denied", "finops@company.invalid")

    monkeypatch.setattr(FakeSMTP, "send_message", boom)
    res = outlook_hook.send_executive_email(["cto@company.invalid"], "s", "<p>x</p>",
                                            approval_mode="auto-approve")
    assert res["ok"] is False and res["status"] == 502 and "denied" in res["error"]


def test_executive_email_timeout_is_returned_not_raised(monkeypatch, approve, fake_smtp):
    _smtp_env(monkeypatch)

    def slow(self, host, port, timeout=None):
        raise TimeoutError("connect timed out")

    monkeypatch.setattr(FakeSMTP, "__init__", slow)
    res = outlook_hook.send_executive_email(["cto@company.invalid"], "s", "<p>x</p>",
                                            approval_mode="auto-approve")
    assert res["ok"] is False and res["status"] == 504


# ---------------------------------------------------------------------------
# Confluence
# ---------------------------------------------------------------------------
def _confluence_env(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://acme.atlassian.invalid/")
    monkeypatch.setenv("CONFLUENCE_USER", "bot@company.invalid")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "fake-token")


def test_markdown_headings_lists_and_inline_convert_to_storage_xhtml():
    out = confluence_hook.markdown_to_storage(
        "# Title\n\nSome **bold** and `code` and [a link](https://x.invalid).\n\n- one\n- two\n")
    assert "<h1>Title</h1>" in out
    assert "<strong>bold</strong>" in out and "<code>code</code>" in out
    assert '<a href="https://x.invalid">a link</a>' in out
    assert "<ul><li>one</li><li>two</li></ul>" in out


def test_markdown_table_becomes_a_table_with_a_header_row():
    out = confluence_hook.markdown_to_storage(
        "| Env | Cost |\n| --- | --- |\n| prod | $10 |\n| dev | $2 |\n")
    assert "<table><tbody>" in out and "</tbody></table>" in out
    assert "<tr><th>Env</th><th>Cost</th></tr>" in out
    assert "<tr><td>prod</td><td>$10</td></tr>" in out
    assert "---" not in out          # the separator row is not a data row


def test_mermaid_fence_becomes_a_code_macro_with_the_source_intact():
    out = confluence_hook.markdown_to_storage(
        "```mermaid\ngraph TD;\n  A-->B;\n```\n")
    assert '<ac:structured-macro ac:name="code">' in out
    assert '<ac:parameter ac:name="language">mermaid</ac:parameter>' in out
    assert "A-->B;" in out           # arrows survive: CDATA, not escaped text
    assert out.count("<![CDATA[") == out.count("]]>")


def test_storage_output_escapes_html_outside_code_blocks():
    out = confluence_hook.markdown_to_storage("A <script>alert(1)</script> & more\n")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out and "&amp;" in out


def test_confluence_creates_a_page_when_the_title_is_absent(monkeypatch, approve, capture_http):
    _confluence_env(monkeypatch)
    responses = [FakeResponse(200, b'{"results": []}'), FakeResponse(200, b'{"id": "9001"}')]
    monkeypatch.setattr(base_hook.urllib.request, "urlopen",
                        lambda req, timeout=None: (capture_http["calls"].append(
                            {"url": req.full_url, "method": req.get_method(),
                             "headers": dict(req.header_items()),
                             "body": json.loads(req.data.decode()) if req.data else None}),
                            responses.pop(0))[1])
    res = confluence_hook.publish_confluence_page("PLAT", "Pipeline Architecture",
                                                  "# Title\n", approval_mode="auto-approve")
    assert res["ok"] is True and res["page_action"] == "created" and res["page_id"] == "9001"
    search, create = capture_http["calls"]
    assert search["method"] == "GET" and "spaceKey=PLAT" in search["url"]
    assert create["method"] == "POST"
    assert create["body"]["body"]["storage"]["representation"] == "storage"
    assert create["headers"]["Authorization"].startswith("Basic ")


def test_confluence_updates_and_bumps_the_version_when_the_page_exists(monkeypatch, approve,
                                                                      capture_http):
    _confluence_env(monkeypatch)
    responses = [FakeResponse(200, b'{"results": [{"id": "77", "version": {"number": 4}}]}'),
                 FakeResponse(200, b'{"id": "77"}')]
    monkeypatch.setattr(base_hook.urllib.request, "urlopen",
                        lambda req, timeout=None: (capture_http["calls"].append(
                            {"url": req.full_url, "method": req.get_method(),
                             "headers": dict(req.header_items()),
                             "body": json.loads(req.data.decode()) if req.data else None}),
                            responses.pop(0))[1])
    res = confluence_hook.publish_confluence_page("PLAT", "Pipeline Architecture", "# T\n",
                                                  parent_page_id="12", approval_mode="auto-approve")
    assert res["page_action"] == "updated" and res["page_id"] == "77"
    update = capture_http["calls"][1]
    assert update["method"] == "PUT" and update["url"].endswith("/content/77")
    assert update["body"]["version"] == {"number": 5}      # current + 1, or Confluence 409s
    assert update["body"]["ancestors"] == [{"id": "12"}]


def test_confluence_denied_makes_no_call(monkeypatch, deny, capture_http):
    _confluence_env(monkeypatch)
    res = confluence_hook.publish_confluence_page("PLAT", "T", "# T\n")
    assert res["reason"] == "not_authorized" and capture_http["calls"] == []


def test_confluence_without_credentials_is_ok_but_not_sent(monkeypatch, approve, capture_http):
    monkeypatch.delenv("CONFLUENCE_BASE_URL", raising=False)
    monkeypatch.delenv("CONFLUENCE_USER", raising=False)
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    monkeypatch.delenv("CONFLUENCE_API_TOKEN_SECRET_ARN", raising=False)
    res = confluence_hook.publish_confluence_page("PLAT", "T", "# T\n",
                                                  approval_mode="auto-approve")
    assert res["ok"] is True and res["sent"] is False and capture_http["calls"] == []


def test_confluence_http_error_and_timeout_on_search_are_soft(monkeypatch, approve, capture_http):
    _confluence_env(monkeypatch)
    capture_http["raise"] = http_error(401, b"unauthorised")
    res = confluence_hook.publish_confluence_page("PLAT", "T", "# T\n",
                                                  approval_mode="auto-approve")
    assert res["ok"] is False and res["status"] == 401
    capture_http["raise"] = socket.timeout("timed out")
    res = confluence_hook.publish_confluence_page("PLAT", "T", "# T\n",
                                                  approval_mode="auto-approve")
    assert res["status"] == 504


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------
def _jira_env(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://acme.atlassian.invalid")
    monkeypatch.setenv("JIRA_USER", "bot@company.invalid")
    monkeypatch.setenv("JIRA_TOKEN", "fake-token")


def _no_jira_env(monkeypatch):
    for var in ("JIRA_BASE_URL", "JIRA_USER", "JIRA_TOKEN", "JIRA_TOKEN_SECRET_ARN"):
        monkeypatch.delenv(var, raising=False)


def test_jira_submits_when_wired_and_returns_the_issue_key(monkeypatch, approve, capture_http,
                                                           tmp_path):
    _jira_env(monkeypatch)
    capture_http["response"] = FakeResponse(201, b'{"key": "CHG-14"}')
    res = jira_hook.create_change_ticket("CHG", "Apply plan", "plan detail",
                                         plan_hash="deadbeef", out_dir=str(tmp_path),
                                         approval_mode="auto-approve")
    assert res["ok"] is True and res["issue_key"] == "CHG-14"
    call = capture_http["calls"][0]
    assert call["url"] == "https://acme.atlassian.invalid/rest/api/3/issue"
    # v3 needs Atlassian Document Format; a plain string 400s.
    assert call["body"]["fields"]["description"]["type"] == "doc"
    assert call["body"]["fields"]["project"] == {"key": "CHG"}
    assert list(tmp_path.iterdir()) == []      # submitted, so nothing written to disk


def test_jira_writes_the_payload_when_not_wired(monkeypatch, approve, capture_http, tmp_path):
    _no_jira_env(monkeypatch)
    res = jira_hook.create_change_ticket("FINOPS", "Cost anomaly", "detail",
                                         out_dir=str(tmp_path), filename="jira_ticket_a1.json",
                                         approval_mode="auto-approve")
    assert res["ok"] is True and res["sent"] is False and res["reason"] == "not_configured"
    assert capture_http["calls"] == []
    written = json.loads((tmp_path / "jira_ticket_a1.json").read_text(encoding="utf-8"))
    assert written == {"project_key": "FINOPS", "summary": "Cost anomaly",
                       "description": "detail", "priority": "High"}


def test_jira_denied_writes_nothing_and_calls_nothing(monkeypatch, deny, capture_http, tmp_path):
    _no_jira_env(monkeypatch)
    res = jira_hook.create_change_ticket("FINOPS", "Cost anomaly", "detail",
                                         out_dir=str(tmp_path))
    assert res["reason"] == "not_authorized"
    assert list(tmp_path.iterdir()) == [] and capture_http["calls"] == []


def test_jira_http_error_and_timeout_are_soft(monkeypatch, approve, capture_http, tmp_path):
    _jira_env(monkeypatch)
    capture_http["raise"] = http_error(400, b"issue type missing")
    res = jira_hook.create_change_ticket("CHG", "s", "d", out_dir=str(tmp_path),
                                         approval_mode="auto-approve")
    assert res["ok"] is False and res["status"] == 400
    capture_http["raise"] = socket.timeout("timed out")
    res = jira_hook.create_change_ticket("CHG", "s", "d", out_dir=str(tmp_path),
                                         approval_mode="auto-approve")
    assert res["status"] == 504


def test_jira_ticket_record_keeps_the_plan_hash_when_given():
    assert jira_hook.build_ticket("CHG", "s", "d", plan_hash="abc")["plan_hash"] == "abc"
    assert "plan_hash" not in jira_hook.build_ticket("CHG", "s", "d")


# ---------------------------------------------------------------------------
# Extraction: finops_agent must use the hooks, not its own copy
# ---------------------------------------------------------------------------
def test_finops_agent_uses_the_extracted_hooks():
    import finops_agent
    assert finops_agent.send_slack_notification is slack_hook.send_slack_notification
    assert finops_agent.create_change_ticket is jira_hook.create_change_ticket
    source = open(finops_agent.__file__, encoding="utf-8").read()
    assert "urlopen" not in source          # no second Slack transport


# --- Subagent manifests ------------------------------------------------------------------

def test_subagent_manifests_import_the_package_not_a_relative_path():
    """`sys.path.insert(0, 'core/integrations')` is relative to the CURRENT DIRECTORY.

    It works from a source checkout root and nowhere else. Run from any other directory --
    which is what a pip-installed MinusOps means -- every one of these manifests raised
    ModuleNotFoundError, so five registered subagents could not dispatch a single message.
    pyproject ships `core.integrations` as a real package precisely so the import does not
    need the trick.
    """
    import glob
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    manifests = glob.glob(os.path.join(root, ".agents", "subagents", "*-agent.md"))
    assert manifests, "the subagent manifests are missing entirely"

    for path in manifests:
        text = open(path, encoding="utf-8").read()
        name = os.path.basename(path)
        assert "sys.path.insert" not in text, (
            f"{name} reaches for a cwd-relative path; use "
            f"`from core.integrations import <hook>`")
        assert "from core.integrations import" in text, (
            f"{name} does not import its hook from the package")


def test_every_subagent_manifest_is_registered_in_agents_md():
    """A manifest nothing points at is a manifest nobody activates. jira-agent.md existed for
    months with the most detailed rules of the five and appeared in none of the three places
    that list them."""
    import glob
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    registry = ""
    for doc in ("AGENTS.md", os.path.join(".agents", "AGENTS.md"),
                os.path.join(".agents", "CONTEXT-agents.md")):
        full = os.path.join(root, doc)
        if os.path.exists(full):
            registry += open(full, encoding="utf-8").read()

    for path in glob.glob(os.path.join(root, ".agents", "subagents", "*-agent.md")):
        name = os.path.splitext(os.path.basename(path))[0]
        assert name in registry, f"{name} is not registered in any manifest list"
