"""
Confluence Cloud page publisher plus a Markdown -> Atlassian storage-format converter.

Publishing is upsert, not create: the hook looks the title up in the space first and PUTs a
version bump if it exists. Confluence refuses a duplicate title in a space, so a plain POST
turns the second run of a "living documentation" job into a hard failure — and version numbers
must be sent as current+1 or the API returns 409.

The converter targets storage XHTML, which is XML, not HTML: text is escaped, code fences
become `ac:structured-macro` code macros with a CDATA body, and no tag is left unclosed.
Mermaid fences are emitted as a code macro with language "mermaid" rather than a Mermaid
macro, because that macro is an app that may not be installed on the target site; the diagram
source stays readable either way.

Credentials come from CONFLUENCE_USER / CONFLUENCE_API_TOKEN (or a Secrets Manager ARN).

Depends on: core/integrations/base_hook.py
Shells out to: the Confluence Cloud REST API under CONFLUENCE_BASE_URL
    (GET /wiki/rest/api/content, POST /wiki/rest/api/content, PUT .../{id})
Used by: tests/test_integrations.py
"""
import os
import re
import sys
import json
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base_hook  # noqa: E402

BASE_URL_ENV = "CONFLUENCE_BASE_URL"
USER_ENV = "CONFLUENCE_USER"
TOKEN_ENV = "CONFLUENCE_API_TOKEN"

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_SEPARATOR_ROW = re.compile(r"^\|[\s:\-|]+\|$")


def _esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(text):
    """Escape, then apply inline Markdown. Order matters: escaping after would eat the tags."""
    out = _esc(text)
    out = _INLINE_CODE.sub(r"<code>\1</code>", out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _LINK.sub(r'<a href="\2">\1</a>', out)
    return out


def _code_macro(language, body):
    # A literal "]]>" inside the source would close the CDATA section early and produce
    # invalid XML, so split it across two sections.
    body = body.replace("]]>", "]]]]><![CDATA[>")
    lang = f'<ac:parameter ac:name="language">{_esc(language)}</ac:parameter>' if language else ""
    return (f'<ac:structured-macro ac:name="code">{lang}'
            f'<ac:plain-text-body><![CDATA[{body}]]></ac:plain-text-body>'
            f'</ac:structured-macro>')


def _table(rows):
    """Render collected pipe-table rows. A separator row marks the row above it as a header."""
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows
             if not _SEPARATOR_ROW.match(r.strip())]
    has_header = len(rows) > 1 and bool(_SEPARATOR_ROW.match(rows[1].strip()))
    out = ["<table><tbody>"]
    for i, row in enumerate(cells):
        tag = "th" if (has_header and i == 0) else "td"
        out.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in row) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def markdown_to_storage(markdown_content):
    """
    Convert a Markdown subset to Confluence storage XHTML.

    Supported: ATX headings, fenced code blocks (including mermaid), pipe tables, unordered
    lists, and paragraphs with inline code, bold, and links. Anything else passes through as
    an escaped paragraph — unsupported syntax degrades to readable text, never to broken XML.
    """
    lines = (markdown_content or "").splitlines()
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            language = stripped[3:].strip()
            i += 1
            body = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # closing fence (or end of input)
            out.append(_code_macro(language, "\n".join(body)))
            continue

        if not stripped:
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2).strip())}</h{level}>")
            i += 1
            continue

        if stripped.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(_table(rows))
            continue

        if re.match(r"^[-*]\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(_inline(re.sub(r"^[-*]\s+", "", lines[i].strip())))
                i += 1
            out.append("<ul>" + "".join(f"<li>{it}</li>" for it in items) + "</ul>")
            continue

        para = []
        while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(("|", "```", "#", "- ", "* ")):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>" + _inline(" ".join(para)) + "</p>")
    return "".join(out)


def _find_page(base, headers, space_key, page_title, timeout):
    query = urllib.parse.urlencode({"spaceKey": space_key, "title": page_title,
                                    "expand": "version"})
    res = base_hook.request_json(f"{base}/wiki/rest/api/content?{query}", headers=headers,
                                 method="GET", timeout=timeout)
    if not res["ok"]:
        return None, res
    try:
        results = json.loads(res.get("body") or "{}").get("results") or []
    except json.JSONDecodeError:
        return None, {"ok": False, "status": 502, "error": "Confluence returned non-JSON search results"}
    return (results[0] if results else None), None


def publish_confluence_page(space_key, page_title, markdown_content, parent_page_id=None,
                            approval_mode="gatekeeper", action="publish-confluence-page",
                            details=None, secret_arn=None, timeout=base_hook.DEFAULT_TIMEOUT):
    """
    Create or update a page. Returns a result dict carrying `page_id` and `page_action`
    ("created" or "updated"); `sent` is False when approval was denied or when the base URL
    or credentials are unconfigured.
    """
    storage = markdown_to_storage(markdown_content)

    def _send():
        base = (os.environ.get(BASE_URL_ENV) or "").strip().rstrip("/")
        auth = base_hook.basic_auth_header(USER_ENV, TOKEN_ENV, secret_arn)
        if not base or not auth:
            return base_hook.not_configured(f"{BASE_URL_ENV}/{USER_ENV}/{TOKEN_ENV}")
        headers = dict(auth)

        existing, err = _find_page(base, headers, space_key, page_title, timeout)
        if err:
            return err

        body = {
            "type": "page",
            "title": page_title,
            "space": {"key": space_key},
            "body": {"storage": {"value": storage, "representation": "storage"}},
        }
        if parent_page_id:
            body["ancestors"] = [{"id": str(parent_page_id)}]

        if existing:
            page_id = str(existing.get("id"))
            body["id"] = page_id
            body["version"] = {"number": int((existing.get("version") or {}).get("number", 1)) + 1}
            res = base_hook.request_json(f"{base}/wiki/rest/api/content/{page_id}", payload=body,
                                         headers=headers, method="PUT", timeout=timeout)
            res["page_action"] = "updated"
            res["page_id"] = page_id
        else:
            res = base_hook.request_json(f"{base}/wiki/rest/api/content", payload=body,
                                         headers=headers, method="POST", timeout=timeout)
            res["page_action"] = "created"
            try:
                res["page_id"] = json.loads(res.get("body") or "{}").get("id")
            except json.JSONDecodeError:
                res["page_id"] = None
        return res

    return base_hook.gated(action, details or f"{space_key}: {page_title}", approval_mode, _send)
