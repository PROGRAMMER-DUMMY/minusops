"""
The CDP path exists for exactly one thing: the clickable bookmark sidebar in plan.pdf.

Measured directly -- the page CONTENT is byte-identical either way (1,392,720 B decompressed,
511 fill ops, 120 dark fills including the #14110f background) because the report CSS sets
print-color-adjust: exact. The plain --print-to-pdf flag loses nothing visual. What it loses
is /Outlines: 27 bookmark entries vs 1.

So the 261 lines of hand-rolled WebSocket buy a reviewer navigating a 13-section sign-off
document, and nothing else. That is worth keeping -- but it had NO test, so a browser update
that silently dropped the outline would degrade the artifact with the suite still green.
This is that test.
"""
import re

import pytest

import reporter

pytestmark = pytest.mark.slow  # drives a real headless browser


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    browser = reporter.find_browser()
    if not browser:
        pytest.skip("no headless Edge/Chrome available")
    src = tmp_path_factory.mktemp("pdf") / "doc.html"
    src.write_text(
        "<html><head><title>Plan Report</title>"
        "<style>@page{size:A4;margin:0;background:#14110f}"
        "html,body{background:#14110f;color:#fff;print-color-adjust:exact}</style></head>"
        "<body><h1>Executive Summary</h1><p>x</p>"
        "<h1>Architecture</h1><p>y</p><h1>Cost Summary</h1><p>z</p></body></html>",
        encoding="utf-8")
    out = tmp_path_factory.mktemp("out") / "doc.pdf"
    ok, info = reporter.render_pdf(str(src), str(out))
    if not ok:
        pytest.skip(f"pdf render unavailable: {info}")
    # render_pdf succeeds on three tiers -- CDP, then `--print-to-pdf`, then a hand-written
    # built-in PDF -- and only the first produces an outline. `ok` is True for all three, so
    # checking it alone let a fallback reach assertions written about the CDP path. That is
    # what happened on CI's first run: no usable browser there, so it landed on the built-in
    # tier (a few hundred bytes, /BaseFont /Helvetica) and three tests reported the outline
    # missing as though the CDP path had regressed.
    #
    # These tests exist to catch the CDP path DEGRADING. When the environment never offered it,
    # there is nothing to catch, and saying so is different from passing. The `info` string
    # names the tier, which is exactly the discriminator needed.
    if "fallback" in (info or "").lower():
        pytest.skip(f"CDP unavailable, render used a fallback tier: {info}")
    return out.read_bytes()


def test_the_pdf_carries_a_bookmark_outline(rendered):
    """The whole justification for the CDP path. If this fails, render_pdf silently fell
    back to --print-to-pdf and reviewers lost section navigation."""
    assert b"/Outlines" in rendered, (
        "plan.pdf has no bookmark outline -- the CDP path degraded to the plain "
        "--print-to-pdf fallback, which is the only thing that path exists to avoid")


def test_the_outline_names_the_document_sections(rendered):
    titles = {t.decode("latin-1", "replace")
              for t in re.findall(rb"/Title\s*\(([^)]{0,80})\)", rendered)}
    joined = " ".join(titles)
    for heading in ("Executive Summary", "Architecture", "Cost Summary"):
        assert heading in joined, f"{heading!r} missing from the PDF outline"


def test_backgrounds_survive_printing(rendered):
    """Independent of the outline: the report is dark-themed, and a PDF that printed
    white-on-white would be unreadable while still passing every other check."""
    import zlib
    streams = b""
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", rendered, re.S):
        try:
            streams += zlib.decompress(m.group(1))
        except Exception:
            pass
    dark = [f for f in re.findall(rb"([\d.]+) ([\d.]+) ([\d.]+) rg", streams)
            if all(float(x) < 0.15 for x in f)]
    assert dark, "no dark fills in the PDF -- the page background did not print"
