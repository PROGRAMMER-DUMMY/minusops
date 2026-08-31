"""
Deliverables and compliance vault.

The vault's job is to be honest about what evidence exists. Its failure mode is not a
crash -- it is listing `proving_report.json` for a run that was never proven, because an
auditor reads a document catalog as an inventory of what was produced.

So every entry carries `present`, nothing is listed that is not on disk, and the bundle
signs what it actually contains.

Depends on: core/reporting/vault.py
Shells out to: nothing
Used by: nothing (pytest entry point)
"""
import json
import os
import zipfile

import pytest

import vault


@pytest.fixture
def run(tmp_path):
    root = tmp_path / "run"
    (root / "reports").mkdir(parents=True)
    (root / "terraform").mkdir()
    (root / "reports" / "report.html").write_text("<html>r</html>", encoding="utf-8")
    (root / "reports" / "architecture.drawio").write_text("<mxfile/>", encoding="utf-8")
    (root / "reports" / "proving_report.json").write_text('{"status":"PASS"}', encoding="utf-8")
    return str(root)


# --- The catalog is an inventory, not a wish list ---------------------------------------

def test_every_declared_category_is_represented():
    """FR-06.1 names six categories; a missing one silently hides a class of evidence."""
    names = {c["key"] for c in vault.CATEGORIES}

    for expected in ("pdf", "html", "diagram", "excel", "evidence", "package"):
        assert expected in names


def test_only_documents_that_exist_are_listed_as_present(run):
    docs = vault.catalog(run)
    by_name = {d["name"]: d for d in docs}

    assert by_name["report.html"]["present"] is True
    assert by_name["proving_report.json"]["present"] is True
    assert by_name["cost.pdf"]["present"] is False


def test_present_documents_carry_a_size_and_absent_ones_do_not(run):
    for doc in vault.catalog(run):
        if doc["present"]:
            assert doc["size_bytes"] > 0, doc["name"]
        else:
            assert doc["size_bytes"] == 0, doc["name"]


def test_the_catalog_is_empty_rather_than_fabricated_for_a_missing_run(tmp_path):
    docs = vault.catalog(str(tmp_path / "no-such-run"))

    assert docs, "the catalog still describes what COULD exist"
    assert not any(d["present"] for d in docs), "but claims none of it is there"


def test_summary_counts_only_what_is_on_disk(run):
    summary = vault.summary(run)

    assert summary["present"] == 3
    # `len(CATEGORIES) * 0 + summary["total"]` reduces to `total == total`, which held no
    # matter what the counter did. `total` is the expected DOCUMENT count, not the category
    # count: 17 documents across 6 categories.
    assert summary["total"] == 17
    assert summary["total"] == summary["present"] + summary["missing"]
    assert summary["present"] < summary["total"]


# --- The bundle -------------------------------------------------------------------------

def test_the_bundle_contains_exactly_the_documents_that_existed(run, tmp_path):
    out = str(tmp_path / "compliance.zip")

    result = vault.bundle(run, out)

    with zipfile.ZipFile(out) as archive:
        names = set(archive.namelist())
    assert "report.html" in names
    assert "proving_report.json" in names
    assert "cost.pdf" not in names, "the bundle must not invent a document"
    assert result["document_count"] == 3


def test_the_bundle_carries_a_manifest_with_a_digest_per_document(run, tmp_path):
    out = str(tmp_path / "compliance.zip")

    vault.bundle(run, out)

    with zipfile.ZipFile(out) as archive:
        manifest = json.loads(archive.read(vault.MANIFEST_NAME).decode("utf-8"))
    assert manifest["documents"], "an unsigned bundle proves nothing about its contents"
    for entry in manifest["documents"]:
        assert len(entry["sha256"]) == 64


def test_the_manifest_digest_matches_the_archived_bytes(run, tmp_path):
    """A signature over something other than what shipped is worse than no signature."""
    import hashlib
    out = str(tmp_path / "compliance.zip")

    vault.bundle(run, out)

    with zipfile.ZipFile(out) as archive:
        manifest = json.loads(archive.read(vault.MANIFEST_NAME).decode("utf-8"))
        for entry in manifest["documents"]:
            digest = hashlib.sha256(archive.read(entry["name"])).hexdigest()
            assert digest == entry["sha256"], entry["name"]


def test_bundling_a_run_with_no_documents_refuses_rather_than_shipping_an_empty_zip(tmp_path):
    result = vault.bundle(str(tmp_path / "empty-run"), str(tmp_path / "out.zip"))

    assert result["ok"] is False
    assert not os.path.exists(str(tmp_path / "out.zip"))
    assert "no documents" in result["reason"].lower()


# --- Preview routing --------------------------------------------------------------------

def test_each_document_declares_how_the_browser_should_show_it(run):
    kinds = {d["preview"] for d in vault.catalog(run)}

    assert kinds <= {"inline", "download", "text"}, kinds


def test_binary_documents_are_never_marked_inline_text(run):
    for doc in vault.catalog(run):
        if doc["name"].endswith((".xlsx", ".zip")):
            assert doc["preview"] == "download", doc["name"]


# --- Invariants -------------------------------------------------------------------------

def test_the_module_imports_only_the_standard_library():
    import ast
    import sys
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "core", "reporting", "vault.py")
    tree = ast.parse(open(path, encoding="utf-8").read())

    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    assert roots <= set(sys.stdlib_module_names), f"non-stdlib imports: {sorted(roots)}"
