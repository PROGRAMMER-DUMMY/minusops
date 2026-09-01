"""
The documentation linter: the guide's code examples must run against the live API.

Every defect this file catches passed a structural review. `docs/extensibility_and_integration_guide.md`
had all six extension vectors, every heading, every code fence -- and three of the five
examples could not execute. Presence was verified; execution was not.

So these tests do not check that an example EXISTS. They resolve every symbol it names
against the module that actually defines it, and compare the module-registry dict against the
real `MODULES` schema key for key. A guide that drifts from the code now fails the suite
rather than costing a contributor an afternoon.

The link tests exist for the same reason. `file:///C:/Users/shubh/...` resolves on exactly one
machine, and a browser will not follow it from an https page at all -- so it is the one link
form guaranteed dead on GitHub, which is where these documents are read.

Depends on: docs/extensibility_and_integration_guide.md, core/cli/*, core/generation/modules.py,
    core/integrations/base_hook.py, .agents/skills/*/SKILL.md
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import ast
import os
import re
import subprocess
import urllib.parse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDE = os.path.join(ROOT, "docs", "extensibility_and_integration_guide.md")
SKILLS_DIR = os.path.join(ROOT, ".agents", "skills")

LINK = re.compile(r'\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
FENCE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
# Emoji and pictographs. Box drawing (U+2500..257F) is deliberately NOT here: a directory
# tree drawn with it is a documented convention in CONTEXT-MAP.md, and it is not an emoji.
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\U0001F1E6-\U0001F1FF\uFE0F\u20E3]")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _fences(lang, text=None):
    text = _read(GUIDE) if text is None else text
    return [body for kind, body in FENCE.findall(text) if kind == lang]


def _linkable(text):
    """Blank out fenced blocks and inline code spans before extracting links.

    A link inside backticks is an ILLUSTRATION of link syntax, not a link -- the guide and
    the context-graph skill both show `[main.py](./main.py)` as the form to copy. Blanking
    rather than deleting keeps every other offset on the line intact, so reported line
    numbers stay true.
    """
    text = re.sub(r"```.*?```", lambda m: " " * len(m.group(0)), text, flags=re.DOTALL)
    return re.sub(r"`[^`\n]*`", lambda m: " " * len(m.group(0)), text)


def _is_in_repo(abs_path):
    """True if the path is tracked by git (a file, or a directory holding tracked files).

    Directories are matched by prefix because a link may point at a folder, and git tracks
    files rather than folders.
    """
    try:
        rel = os.path.relpath(abs_path, ROOT).replace(os.sep, "/")
    except ValueError:
        return False
    if rel.startswith(".."):
        return False
    tracked = _tracked_set()
    return rel in tracked or any(t.startswith(rel.rstrip("/") + "/") for t in tracked)


_TRACKED_CACHE = None


def _tracked_set():
    global _TRACKED_CACHE
    if _TRACKED_CACHE is None:
        out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
        _TRACKED_CACHE = {line for line in out.splitlines() if line}
    return _TRACKED_CACHE


def _tracked(*suffixes):
    """Git-tracked files with these suffixes, excluding the user's own .claude/ tooling."""
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).split()
    return [f for f in out if f and f.endswith(suffixes) and not f.startswith(".claude/")
            and os.path.isfile(os.path.join(ROOT, f))]


def _example_under(path, heading):
    """The first markdown fence in the `## ...heading...` section of `path`."""
    # Section boundaries are the NUMBERED `## 5. ...` headings only. The manifest examples
    # contain their own `## Operating Rules` / `## Procedures` headings inside the fence, and
    # matching those cuts the section before its fence closes.
    text = _read(path)
    numbered = r"^## \d+\. "
    start = next(m.start() for m in re.finditer(numbered + r".*$", text, re.M)
                 if heading in m.group(0))
    following = [m.start() for m in re.finditer(numbered, text[start + 1:], re.M)]
    section = text[start:start + 1 + following[0]] if following else text[start:]
    fences = _fences("markdown", section)
    assert fences, f"no markdown example under {heading!r}"
    return fences[0]


def _frontmatter_keys(text):
    """Top-level YAML frontmatter keys, or an empty set when there is no frontmatter."""
    lines = text.strip().splitlines()
    if not lines or lines[0].strip() != "---":
        return set()
    end = lines.index("---", 1)
    return {ln.split(":", 1)[0].strip() for ln in lines[1:end] if ":" in ln and ln[0] not in " -"}


def _markdown_files(*dirs):
    for d in dirs:
        for dirpath, _sub, files in os.walk(d):
            for name in files:
                if name.endswith(".md"):
                    yield os.path.join(dirpath, name)


# --- The guide's code examples must resolve against the live API ----------------------

def test_guide_cli_example_imports_real_symbols():
    """Vector 1 named `resolve_context` and `format_section_header`. Neither exists anywhere
    in this repo -- the flagship copy-pasteable example failed at import."""
    import importlib

    unresolved = []
    for body in _fences("python"):
        for module_name, names in re.findall(r"from \.\.(\w+) import ([^\n]+)", body):
            # core.cli, not cli: both resolve (core/ and the repo root are both importable),
            # but they produce SEPARATE module objects for the same file. core.cli is the path
            # the console script actually uses -- see tests/test_module_identity.py.
            module = importlib.import_module(f"core.cli.{module_name}")
            for symbol in (n.strip() for n in names.split(",")):
                if not hasattr(module, symbol):
                    unresolved.append(f"core/cli/{module_name}.py has no {symbol!r}")

    assert not unresolved, "guide imports symbols that do not exist: " + "; ".join(unresolved)


def test_guide_cli_example_handles_the_refusal_the_way_the_api_raises_it():
    """`resolve_run()` RAISES ContextError; it never returns a falsy record. An example that
    tests the return value writes a refusal branch that can never fire, and the real
    fail-closed refusal escapes as an unhandled traceback instead of a clean exit 1."""
    bodies = [b for b in _fences("python") if "resolve_run" in b]
    assert bodies, "no CLI example calls resolve_run"

    for body in bodies:
        assert "except ContextError" in body, (
            "the CLI example calls resolve_run without catching ContextError")


def test_guide_module_registry_example_matches_the_real_schema():
    """The guide taught `description`/`match_keywords`/`outputs`. The registry uses
    `title`/`satisfies`/`provides` and subscripts them directly (modules.py:462,469,508,545
    and schema_watch.py:243), so a module registered from the guide raised KeyError in
    match_modules -- the core selection path -- rather than merely failing to match."""
    import modules as module_registry

    entries = []
    for body in _fences("python"):
        stripped = body.strip()
        if stripped.startswith("{") and '"id"' in stripped:
            entries.append(ast.literal_eval(stripped))
    assert entries, "the guide has no module-registry example"

    real_keys = set(module_registry.MODULES[0])
    for entry in entries:
        assert set(entry) == real_keys, (
            f"example keys {sorted(set(entry))} != registry schema {sorted(real_keys)}")


def test_guide_module_registry_example_survives_the_real_matcher():
    """The strongest form of the check: feed the example entry to the code that consumes
    registry entries and see whether it raises."""
    import modules as module_registry

    entry = next(ast.literal_eval(b.strip()) for b in _fences("python")
                 if b.strip().startswith("{") and '"id"' in b)

    for phrase in entry["satisfies"] + entry["services"]:   # schema_watch.py:243
        assert module_registry._tokens(phrase)
    assert entry["title"] and entry["provides"]             # modules.py:508


def test_guide_hook_example_uses_the_real_base_hook_api():
    """Vector 3 called `audit_logger.log_audit_event(action, details)`; the real signature
    takes a third required `log_dir`, so the example raised TypeError on the SUCCESS path --
    after the webhook had already fired."""
    import base_hook

    referenced, missing = set(), []
    for body in _fences("python"):
        for attr in set(re.findall(r"base_hook\.(\w+)", body)):
            referenced.add(attr)
            if not hasattr(base_hook, attr):
                missing.append(attr)

    # Without this the test passes vacuously on a guide that mentions base_hook nowhere --
    # an empty set satisfies any "all of them exist" claim.
    assert referenced, "no guide example references base_hook at all"
    assert not missing, f"guide references base_hook.{{{', '.join(missing)}}}, which do not exist"


def test_guide_hook_example_follows_the_flat_import_convention():
    """Every real hook in core/integrations/ does `import base_hook` off sys.path. A
    package-relative import there produces a second module object, and a monkeypatch on one
    is invisible to the other -- the bug that forced core/cli to go package-relative."""
    hook_examples = [b for b in _fences("python") if "base_hook" in b]
    assert hook_examples, "no hook example in the guide"

    for body in hook_examples:
        assert "from ..governance import" not in body, (
            "hook example uses a package-relative import; core/integrations uses flat imports")


def test_guide_subagent_example_has_registrable_frontmatter():
    """All five manifests in .agents/subagents/ open with name/description/tools/model. The
    guide's example had no frontmatter at all, so a manifest authored from it could not be
    registered by an agent runtime."""
    # A subagent and a skill are both frontmatter-first but need different keys, so the
    # example is located by the vector it sits under rather than by a word in its body.
    # Required keys are read off the real files rather than hard-coded: if the runtime's
    # contract changes, the fixture that must change is the manifest, not this test.
    subagent = _frontmatter_keys(_example_under(GUIDE, "Extension Vector 4"))
    skill = _frontmatter_keys(_example_under(GUIDE, "Extension Vector 5"))

    real_subagent = _frontmatter_keys(
        _read(os.path.join(ROOT, ".agents", "subagents", "slack-agent.md")))
    real_skill = _frontmatter_keys(
        _read(os.path.join(ROOT, ".agents", "skills", "context-graph", "SKILL.md")))

    assert real_subagent <= subagent, (
        f"subagent example is missing {sorted(real_subagent - subagent)}")
    assert real_skill <= skill, f"skill example is missing {sorted(real_skill - skill)}"


# --- Links must resolve for someone who is not on this machine ------------------------

def test_docs_and_skills_carry_no_machine_absolute_links():
    """`file:///C:/Users/shubh/...` names one developer's disk. It is dead in every clone,
    and browsers refuse to follow file:// from an https page, so it is dead on GitHub too."""
    # Link TARGETS, not the raw string: the guide and the skill both name `file://` in prose
    # to warn against it, and a substring check would flag the warning itself.
    offenders = []
    for path in _markdown_files(os.path.join(ROOT, "docs"), SKILLS_DIR):
        for lineno, line in enumerate(_linkable(_read(path)).splitlines(), 1):
            for target in LINK.findall(line):
                if target.startswith("file://") or target.startswith("/"):
                    offenders.append(f"{os.path.relpath(path, ROOT)}:{lineno} -> {target}")

    assert not offenders, "machine-absolute links: " + ", ".join(offenders)


def test_the_context_graph_skill_prescribes_repo_relative_links():
    """The skill mandated the file:// scheme, which is why 70 of them exist. Fixing the
    documents without fixing the rule that produced them regenerates the problem."""
    skill = _read(os.path.join(SKILLS_DIR, "context-graph", "SKILL.md"))

    targets = LINK.findall(_linkable(skill))
    assert not [t for t in targets if t.startswith(("file://", "/"))], (
        "the skill still carries machine-absolute links")
    assert "repo-relative" in skill.lower(), "the skill does not prescribe repo-relative links"
    assert "file://" in skill, "the skill should say explicitly that file:// is banned"


@pytest.mark.parametrize("scope", ["docs", ".agents/skills"])
def test_local_markdown_links_resolve_on_disk(scope):
    """A link that resolves on the author's disk but not in a clone is the failure this
    catches, so it checks git-tracked-ness rather than os.path.exists.

    `tasks/` is no longer a scope. It is gitignored -- building plans and PRDs are working
    documents, not deliverables -- so every link inside it points at another untracked file
    and fails a tracking check by construction. Walking it would only ever assert that
    ignored files are ignored.
    """
    broken = []
    for path in _markdown_files(os.path.join(ROOT, scope)):
        base = os.path.dirname(path)
        for lineno, line in enumerate(_linkable(_read(path)).splitlines(), 1):
            for target in LINK.findall(line):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                frag = urllib.parse.unquote(target.split("#", 1)[0])
                if not frag:
                    continue
                root = ROOT if frag.startswith("/") else base
                resolved = os.path.normpath(os.path.join(root, frag.lstrip("/")))
                # Resolved against what git TRACKS, not against this disk. Checking
                # os.path.exists let a link to docs/REPO_MAP.md pass here and fail in CI:
                # the file sits in .gitignore, so it is present for whoever generated it
                # and absent in every clone. A docs linter that only sees the author's
                # filesystem cannot catch the one bug it exists to catch.
                if not _is_in_repo(resolved):
                    broken.append(f"{os.path.relpath(path, ROOT)}:{lineno} -> {target}")

    assert not broken, f"{len(broken)} broken links in {scope}: " + "; ".join(broken)


# --- NFR-01 -----------------------------------------------------------------------------

@pytest.mark.parametrize("suffix", [".py", ".md", ".yml", ".yaml"])
def test_no_emoji_in_tracked_sources(suffix):
    """NFR-01, repo-wide.

    An emoji renders in exactly one of the places these strings are read. A terminal on
    Windows raises UnicodeEncodeError on cp1252 rather than degrading, a CI log shows boxes,
    a `grep` result shows mojibake, and a ticket paste carries whatever the clipboard did to
    it. Status carried by colour and shape is also invisible to a screen reader: `[FAIL]`
    survives all of those and a red circle survives none.

    Box drawing is deliberately allowed -- see the EMOJI pattern above.
    """
    offenders = []
    for rel in _tracked(suffix):
        for lineno, line in enumerate(_read(os.path.join(ROOT, rel)).splitlines(), 1):
            found = EMOJI.findall(line)
            if found:
                names = " ".join(f"U+{ord(c):04X}" for c in dict.fromkeys(found))
                offenders.append(f"{rel}:{lineno} ({names})")

    assert not offenders, (
        f"{len(offenders)} lines carry emoji in {suffix} files: " + "; ".join(offenders))


# --- A document may not advertise a pillar capability the registry lacks ----------------
#
# `console_app.py` claimed the 19-pillar engine covered "schema evolution rules (EVOLVE,
# FREEZE, DISCARD_ROW)". No such pillar exists and no such constant is defined anywhere. An
# external research pass read that sentence, expanded it into a four-row policy table mapping
# each constant to a medallion zone, and cited the repository as its source. A false claim
# inside the running console is worse than one in a README: it is read as the product
# describing itself.

PILLARS_SRC = os.path.join(ROOT, "core", "architecture", "pillars.py")

# Generic domain vocabulary that may appear beside a pillar sentence without naming a pillar
# constant. Additions are a reviewed one-line edit, which is the point.
_GENERIC_CAPS = {
    "AWS", "GCP", "JSON", "YAML", "HCL", "CLI", "API", "URL", "SLA", "SLO", "DAG", "ETL",
    "ELT", "IAM", "KMS", "SSE", "CMK", "PII", "PHI", "RBAC", "SQL", "DPU", "RPU", "MFA",
    "OIDC", "SHA256", "STS", "VPC", "DMS", "SFTP", "CDC", "MSK", "EMR", "BCM", "FINOPS",
    "TBAC", "DQ", "DR", "CI", "CD", "MINUS", "PRD", "ADR", "FM", "TODO", "NOTE", "OK",
    "README", "MAP", "CONTEXT", "AGENTS", "SKILL",
    "ANY", "ISO", "IEC", "FURPS", "NFR", "FR",
}
_CAPS_TOKEN = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")

# A caps token is a claim about a pillar constant only when it is prose. A lint directive
# names a rule and a filename names a file; neither asserts anything about the registry, and
# both sat on lines mentioning pillars only incidentally -- `# noqa: E402` on the import of
# `pillars`, and a `DESIGN.md` row described as "core architectural pillars".
_DIRECTIVE = re.compile(r"(?:noqa|pylint|type|mypy|ruff|flake8)\s*:\s*[A-Z0-9,\s]*$")
_FILENAME = re.compile(r"\.(?:md|py|json|txt|ya?ml|tf|drawio|html)\b")


def _is_prose_claim(line, token):
    """Whether this occurrence asserts a pillar constant rather than naming a rule or file."""
    before, _, after = line.partition(token)
    return not _DIRECTIVE.search(before) and not _FILENAME.match(after)


def _pillar_vocabulary():
    with open(PILLARS_SRC, encoding="utf-8") as handle:
        return handle.read()


@pytest.mark.parametrize("relpath", [
    os.path.join("app", "console_app.py"),
    os.path.join("core", "architecture", "pillars.py"),
    os.path.join(".agents", "skills", "grill-me", "SKILL.md"),
])
def test_no_document_advertises_a_pillar_constant_the_registry_lacks(relpath):
    path = os.path.join(ROOT, relpath)
    if not os.path.isfile(path):
        pytest.skip(f"{relpath} not present")

    vocabulary = _pillar_vocabulary()
    with open(path, encoding="utf-8") as handle:
        lines = handle.readlines()

    invented = []
    for number, line in enumerate(lines, 1):
        if "pillar" not in line.lower():
            continue
        for token in _CAPS_TOKEN.findall(line):
            if token in _GENERIC_CAPS or token in vocabulary:
                continue
            if not _is_prose_claim(line, token):
                continue
            invented.append(f"{relpath}:{number} names {token!r}, absent from pillars.py")

    assert not invented, "\n".join(invented)


def test_a_pillar_count_claim_matches_the_registry():
    """"19-Pillar" appears in the console, the skill and the agent docs. If a pillar is added
    or removed, every one of those numbers becomes wrong silently."""
    import sys
    sys.path.insert(0, os.path.join(ROOT, "core", "architecture"))
    import pillars

    claimed = re.compile(r"\b(\d+)[- ]?[Pp]illar")
    tracked = subprocess.run(["git", "ls-files", "*.py", "*.md"], cwd=ROOT,
                             capture_output=True, text=True).stdout.split()
    # An append-only ledger records what was true at each step; a count there
    # is history, not a present-tense claim.
    tracked = [f for f in tracked if not f.endswith("docs/PROGRESS.md")]
    wrong = []
    for relpath in tracked:
        path = os.path.join(ROOT, relpath)
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError):
            continue
        for count in claimed.findall(text):
            if int(count) != len(pillars.PILLARS):
                wrong.append(f"{relpath} claims {count} pillars; registry has {len(pillars.PILLARS)}")

    assert not wrong, "\n".join(sorted(set(wrong)))
