"""
Invariants for the transport subagent manifests in `.agents/subagents/`
.

These manifests are prompts, so nothing executes them and nothing fails when one drifts from
the hook it describes. The three rules below are the ones whose absence causes real damage: a
leaked bearer token, a retried denial, and an agent reporting a delivery that never happened.

Comments are not stripped before searching because these files are entirely prose -- the
assertions look for required content, not for the absence of a directive.

Depends on: nothing (stdlib only)
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SUBAGENTS = os.path.join(_ROOT, ".agents", "subagents")

_EXPECTED = ["slack-agent", "teams-agent", "outlook-agent", "confluence-agent", "jira-agent"]


def _manifest(name):
    with open(os.path.join(_SUBAGENTS, f"{name}.md"), encoding="utf-8") as fh:
        return fh.read()


def test_every_transport_hook_has_a_manifest():
    """core/integrations/ exposes five senders. A hook with no manifest is a capability no
    agent knows it has."""
    present = sorted(f[:-3] for f in os.listdir(_SUBAGENTS) if f.endswith(".md"))
    assert present == sorted(_EXPECTED)


@pytest.mark.parametrize("name", _EXPECTED)
def test_manifest_forbids_echoing_credentials(name):
    body = _manifest(name).lower()
    assert "never" in body and ("token" in body or "webhook url" in body or "credential" in body), (
        f"{name} does not tell the agent to keep the credential out of its output"
    )


@pytest.mark.parametrize("name", _EXPECTED)
def test_manifest_states_that_ok_is_not_sent(name):
    """An unconfigured channel returns ok=True with sent=False. An agent checking `ok` alone
    reports a delivery that never left the machine."""
    body = _manifest(name)
    assert "sent" in body and "ok" in body, f"{name} does not distinguish ok from sent"


@pytest.mark.parametrize("name", _EXPECTED)
def test_manifest_treats_a_denial_as_a_denial(name):
    body = _manifest(name).lower()
    assert "deni" in body, f"{name} does not say what a denied approval means"
    assert "retr" in body, f"{name} does not say whether to retry"


@pytest.mark.parametrize("name", _EXPECTED)
def test_manifest_has_frontmatter_with_a_name_and_description(name):
    body = _manifest(name)
    assert body.startswith("---\n"), f"{name} has no frontmatter"
    front = body.split("---", 2)[1]
    assert "name:" in front and "description:" in front


# --- jira-agent specifics -------------------------------------------------------------

def test_jira_manifest_calls_the_hook_and_never_takes_a_token():
    body = _manifest("jira-agent")
    assert "create_change_ticket" in body, "must name the function it calls"
    assert "JIRA_API_TOKEN" not in body and "JIRA_TOKEN" not in body.split("resolve")[0], (
        "the manifest must not instruct the agent to handle the API token"
    )


def test_jira_manifest_explains_the_unwired_write_to_disk():
    """When Jira is not configured the hook writes the payload to a file and returns
    sent=False. An agent that reports 'ticket created' there has invented a ticket."""
    body = _manifest("jira-agent").lower()
    assert "not_configured" in body or "written to" in body or "to disk" in body, (
        "must explain that an unwired Jira produces a file, not a ticket"
    )


def test_jira_manifest_names_the_atlassian_document_format():
    body = _manifest("jira-agent")
    assert "ADF" in body or "Atlassian Document Format" in body
