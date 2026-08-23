"""
Deliverables and compliance vault (PRD v13 FR-06).

A document catalog is read as an inventory. Its dangerous failure is not crashing -- it is
listing `proving_report.json` for a run that was never proven, because the reader concludes
the proof exists. So the catalog always describes what COULD exist for a run (that is what
makes a gap visible) and marks each entry `present` strictly from the filesystem.

The bundle follows the same rule: it archives only files that are there, refuses to produce
an empty zip, and its manifest digests are computed over the bytes that actually shipped.
A signature over something other than the archived content is worse than no signature,
because it converts "unverified" into "verified wrong".

Depends on: nothing (standard library only -- PRD v13 invariant 4)
Shells out to: nothing
Used by: app/console_app.py (View 4), tests/test_vault.py
"""
import datetime
import hashlib
import json
import os
import zipfile

MANIFEST_NAME = "vault_manifest.json"

# `preview` is how the browser should present it: inline in an iframe, rendered as text, or
# handed over as a download. A binary workbook marked "text" renders as mojibake.
CATEGORIES = (
    {"key": "pdf", "title": "Executive PDFs", "preview": "inline",
     "documents": ("plan.pdf", "cost.pdf", "inspect.pdf")},
    {"key": "html", "title": "Interactive HTML reports", "preview": "inline",
     "documents": ("report.html", "cost.html")},
    {"key": "diagram", "title": "Diagrams and visual assets", "preview": "text",
     "documents": ("architecture.drawio", "architecture_url.txt",
                   "architecture.svg", "dataflow.svg")},
    {"key": "excel", "title": "FinOps workbooks", "preview": "download",
     "documents": ("executive_project_summary.xlsx", "pipeline_detailed_ledger.xlsx")},
    {"key": "evidence", "title": "Signed governance evidence", "preview": "text",
     "documents": ("proving_report.json", "manifest.json", "plan.json")},
    {"key": "package", "title": "Handoff package", "preview": "text",
     "documents": ("enterprise-package.md",)},
)

# Where each document may live inside a run workspace, in search order.
_SEARCH_DIRS = ("reports", "", os.path.join("reports", "bundle"), "bcm")


def _locate(run_root, name):
    for relative in _SEARCH_DIRS:
        candidate = os.path.join(run_root, relative, name) if relative else \
            os.path.join(run_root, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def catalog(run_root):
    """Every document the vault knows about, with whether it is actually there.

    Absent documents are listed too, and that is the point: a category that silently
    disappears when empty hides the fact that the evidence was never produced.
    """
    documents = []
    for category in CATEGORIES:
        for name in category["documents"]:
            path = _locate(run_root, name) if run_root else None
            present = bool(path)
            documents.append({
                "name": name,
                "category": category["key"],
                "category_title": category["title"],
                "preview": category["preview"] if present else "download",
                "present": present,
                "path": path or "",
                "size_bytes": os.path.getsize(path) if present else 0,
            })
    return documents


def summary(run_root):
    """Counts for the view header: how much of the expected evidence exists."""
    documents = catalog(run_root)
    present = [d for d in documents if d["present"]]
    return {
        "total": len(documents),
        "present": len(present),
        "missing": len(documents) - len(present),
        "bytes": sum(d["size_bytes"] for d in present),
        "by_category": {
            category["key"]: sum(1 for d in present if d["category"] == category["key"])
            for category in CATEGORIES
        },
    }


def bundle(run_root, out_path):
    """Write a signed compliance archive of everything that exists.

    Refuses on an empty run rather than shipping a zip containing only a manifest -- an
    archive that looks like evidence and holds none is the worst artifact to hand an
    auditor.
    """
    documents = [d for d in catalog(run_root) if d["present"]]
    if not documents:
        return {"ok": False, "path": None, "document_count": 0,
                "reason": "no documents exist for this run; nothing to bundle"}

    entries = []
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for document in documents:
            with open(document["path"], "rb") as handle:
                payload = handle.read()
            archive.writestr(document["name"], payload)
            entries.append({
                "name": document["name"],
                "category": document["category"],
                "size_bytes": len(payload),
                # Digest of the bytes written INTO the archive, not of the source file --
                # they are the same today, and computing it here keeps them the same if the
                # archive ever starts transforming content.
                "sha256": hashlib.sha256(payload).hexdigest(),
            })
        manifest = {
            "run_root": run_root,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "document_count": len(entries),
            "documents": entries,
        }
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))

    return {"ok": True, "path": out_path, "document_count": len(entries),
            "reason": "", "manifest": manifest}


def format_catalog(run_root):
    """The vault as text, for the terminal."""
    stats = summary(run_root)
    lines = ["DELIVERABLES AND COMPLIANCE VAULT", "=" * 60,
             f"{stats['present']} of {stats['total']} documents present", ""]
    current = None
    for document in catalog(run_root):
        if document["category_title"] != current:
            current = document["category_title"]
            lines.append(f"[{current}]")
        mark = "[OK]" if document["present"] else "[  ]"
        size = f"{document['size_bytes']:,} B" if document["present"] else "not produced"
        lines.append(f"  {mark} {document['name']:<34} {size}")
    return "\n".join(lines)
