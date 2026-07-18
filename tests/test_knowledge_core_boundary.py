import ast
import glob
import os
import sys

_CORE_DIR = "core/generation"
_FILES = sorted(os.path.basename(p) for p in glob.glob(f"{_CORE_DIR}/knowledge_*.py"))
_ALLOWED = set(sys.stdlib_module_names) | {"schema_watch", "modules", "module_registry"}


def _module_level_imports(tree):
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def test_knowledge_core_imports_nothing_beyond_stdlib_and_schema_watch():
    for filename in _FILES:
        with open(f"{_CORE_DIR}/{filename}", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filename)
        for node in _module_level_imports(tree):
            names = [node.module] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names]
            for name in names:
                top = (name or "").split(".")[0]
                assert top in _ALLOWED, f"{filename} imports non-stdlib {name!r} at module level"


def test_knowledge_verifier_nli_is_never_module_level_imported_by_core():
    for filename in _FILES:
        with open(f"{_CORE_DIR}/{filename}", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filename)
        for node in _module_level_imports(tree):
            names = [node.module] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names]
            for name in names:
                assert (name or "").split(".")[0] != "knowledge_verifier_nli", (
                    f"{filename} imports knowledge_verifier_nli at module level -- "
                    "the verifier must be absent-able, not just optional by convention")
