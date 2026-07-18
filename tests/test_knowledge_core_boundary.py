import ast
import glob
import os
import sys

# Repo-root-anchored, NOT cwd-relative (final whole-branch review, 2026-07-18): a cwd-relative
# path silently returns an empty glob if pytest is ever invoked from outside the repo root,
# which would make both tests below vacuously pass having asserted nothing -- a guard that can
# silently test nothing is worse than no guard. Same anchoring pattern as tests/conftest.py's
# own ROOT computation.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_CORE_DIR = os.path.join(_REPO_ROOT, "core", "generation")
_FILES = sorted(os.path.basename(p) for p in glob.glob(os.path.join(_CORE_DIR, "knowledge_*.py")))
_ALLOWED = set(sys.stdlib_module_names) | {"schema_watch", "modules", "module_registry"}


# Scope, disclosed: this guard checks each knowledge_*.py file's OWN direct imports only, not
# the transitive closure of what an _ALLOWED dependency (schema_watch/modules/module_registry)
# imports internally. A re-export path -- e.g. schema_watch.py importing knowledge_verifier_nli
# and knowledge_store.py accessing it as schema_watch.knowledge_verifier_nli -- would not be
# caught by this file at all, since the literal name never appears as an import in the checked
# files. Treated as out of scope: the plan's own boundary is "core imports nothing beyond
# stdlib + schema_watch" (a first-order check), not a recursive audit of schema_watch's own
# dependency graph. Extending to a transitive check is a materially bigger feature, not a fix.


def _module_level_nodes(nodes):
    """Yields every AST node reachable at module level: the given statements plus everything
    nested inside them (try/except, if/elif, with, for/while, match/case, ...), EXCEPT bodies
    of FunctionDef/AsyncFunctionDef/ClassDef -- lazy imports inside a function are the
    sanctioned escape hatch this project relies on elsewhere and must stay invisible to this
    guard. Walks via ast.iter_fields (a generic field-walk) rather than a hardcoded list of
    compound-statement types, so no statement kind is silently missed -- the only invariant to
    verify is "never descends into def/class bodies," not "did I enumerate every syntax form"
    (final whole-branch review, 2026-07-18: the original version used ast.iter_child_nodes(),
    which only saw DIRECT children of Module and silently missed any import one level deeper,
    including the try/except-wrapped case this guard exists to catch)."""
    for node in nodes:
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for _, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            children = [c for c in children if isinstance(c, ast.AST)]
            if children:
                yield from _module_level_nodes(children)


def _module_level_imports(tree_body):
    for node in _module_level_nodes(tree_body):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def _module_level_dynamic_import_calls(tree_body):
    """Yields __import__(...) / importlib.import_module(...) call sites at module level. These
    bypass Import/ImportFrom detection entirely since the imported name is an arbitrary runtime
    expression (string concatenation, a variable, ...), not a parseable identifier -- a precise
    "did this dynamically import knowledge_verifier_nli specifically" check is undecidable by
    static analysis. Instead of trying, this bans the CALL FORM itself at module level in these
    core files: a stdlib-only, disclosed-boundary design has no legitimate reason to reach for
    dynamic imports here at all."""
    for node in _module_level_nodes(tree_body):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "__import__":
            yield node
        elif isinstance(func, ast.Attribute) and func.attr == "import_module" and \
                isinstance(func.value, ast.Name) and func.value.id == "importlib":
            yield node


def test_module_level_imports_descends_into_try_except_but_not_into_function_defs():
    # Proves the fix directly: a try/except-wrapped import at module level IS caught (this is
    # the exact bypass pattern the final review found), while a lazy import inside a function
    # body is NOT caught (that remains the sanctioned escape hatch -- over-catching it would
    # make this guard reject normal, allowed code). The general field-walk also covers an
    # `if TYPE_CHECKING:`-style conditional import with no special-casing needed, since it's
    # just another ast.If under the same walk.
    source = (
        "try:\n"
        "    import knowledge_verifier_nli\n"
        "except ImportError:\n"
        "    knowledge_verifier_nli = None\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    import knowledge_verifier_nli\n"
        "\n"
        "def _lazy():\n"
        "    import knowledge_verifier_nli\n"
    )
    tree = ast.parse(source)
    names = []
    for node in _module_level_imports(tree.body):
        names += [node.module] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names]
    assert names.count("knowledge_verifier_nli") == 2  # try/except + TYPE_CHECKING, not _lazy()


def test_module_level_dynamic_import_calls_detects_both_import_forms():
    # __import__(...) and importlib.import_module(...) are both banned call forms at module
    # level; a call to either buried inside a function body stays outside this guard's scope,
    # same rule as ordinary imports.
    source = (
        "x = __import__('knowledge_verifier_nli')\n"
        "import importlib\n"
        "y = importlib.import_module('knowledge_verifier_nli')\n"
        "\n"
        "def _lazy():\n"
        "    return __import__('knowledge_verifier_nli')\n"
    )
    tree = ast.parse(source)
    calls = list(_module_level_dynamic_import_calls(tree.body))
    assert len(calls) == 2


def test_knowledge_core_imports_nothing_beyond_stdlib_and_schema_watch():
    assert _FILES, "boundary test discovered no core files -- _CORE_DIR resolution or glob pattern broke"
    for filename in _FILES:
        with open(os.path.join(_CORE_DIR, filename), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filename)
        for node in _module_level_imports(tree.body):
            names = [node.module] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names]
            for name in names:
                top = (name or "").split(".")[0]
                assert top in _ALLOWED, f"{filename} imports non-stdlib {name!r} at module level"


def test_knowledge_verifier_nli_is_never_module_level_imported_by_core():
    assert _FILES, "boundary test discovered no core files -- _CORE_DIR resolution or glob pattern broke"
    for filename in _FILES:
        with open(os.path.join(_CORE_DIR, filename), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filename)
        for node in _module_level_imports(tree.body):
            names = [node.module] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names]
            for name in names:
                assert (name or "").split(".")[0] != "knowledge_verifier_nli", (
                    f"{filename} imports knowledge_verifier_nli at module level -- "
                    "the verifier must be absent-able, not just optional by convention")


def test_knowledge_core_has_no_dynamic_import_calls_at_module_level():
    assert _FILES, "boundary test discovered no core files -- _CORE_DIR resolution or glob pattern broke"
    for filename in _FILES:
        with open(os.path.join(_CORE_DIR, filename), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filename)
        for node in _module_level_dynamic_import_calls(tree.body):
            raise AssertionError(
                f"{filename}:{node.lineno} uses a dynamic import call (__import__/"
                "importlib.import_module) at module level -- these bypass static "
                "Import/ImportFrom detection and are banned outright in core")
