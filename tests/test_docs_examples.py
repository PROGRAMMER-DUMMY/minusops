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
            module = importlib.import_module(f"cli.{module_name}")
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


@pytest.mark.parametrize("scope", ["docs", "tasks/completed", ".agents/skills"])
def test_local_markdown_links_resolve_on_disk(scope):
    """Eight links in tasks/completed/ broke when the PRDs were archived: `../` used to mean
    the repo root and now means tasks/."""
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
                if not os.path.exists(resolved):
                    broken.append(f"{os.path.relpath(path, ROOT)}:{lineno} -> {target}")

    assert not broken, f"{len(broken)} broken links in {scope}: " + "; ".join(broken)


# --- NFR-01 -----------------------------------------------------------------------------

def test_no_emoji_in_python_sources():
    """Excel renders emoji; a terminal, a CI log and a ticket paste do not, and the same
    strings are read back by operators in all four places."""
    offenders = []
    for dirpath, subdirs, files in os.walk(os.path.join(ROOT, "core")):
        subdirs[:] = [d for d in subdirs if d != "__pycache__"]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            for lineno, line in enumerate(_read(path).splitlines(), 1):
                if EMOJI.search(line):
                    offenders.append(f"{os.path.relpath(path, ROOT)}:{lineno}")

    assert not offenders, "emoji in Python sources: " + ", ".join(offenders)
