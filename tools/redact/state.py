"""Per-document review state: proposed and manual boxes, page approvals.

Stored as JSON under the review folder. Holds coordinates, match kinds and
flags only, never document text.
"""

import hashlib
import json
import os

from . import detect
from .config import DPI

VERSION = 2
BOX_KINDS = {"term", "email", "phone", "aadhaar", "pan", "id_number", "name", "contact",
             "consumer_id", "id_doc", "address", "qr", "barcode", "manual"}


class Rejected(Exception):
    """An approval the page is not ready for."""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def load(paths, doc_id):
    try:
        st = json.loads(paths.state(doc_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    return st if st.get("version") == VERSION else None


def save(paths, doc_id, state):
    write_json(paths.state(doc_id), state)


def build(paths, doc_id, use_ocr=True):
    """Run detection on every page and return a fresh, unapproved state."""
    src = paths.original(doc_id)
    deny, keep = detect.load_terms(paths.terms(doc_id))
    pages = []
    doc, digest = detect.open_source(src)
    with doc:
        for page in doc:
            p = detect.detect_page(page, deny or [], keep, use_ocr=use_ocr)
            p["approved"] = False
            mark_conflicts(p)
            pages.append(p)
    return {
        "version": VERSION,
        "doc_id": doc_id,
        "source_sha256": digest,
        "source_ext": src.suffix.lower(),
        "dpi": DPI,
        "denylist_found": deny is not None,
        "denylist_terms": len(deny or []),
        "keep_terms": len(keep),
        "pages": pages,
    }


def get_or_build(paths, doc_id, use_ocr=True):
    """Load saved state, or (re)build it if missing or the source file changed."""
    state = load(paths, doc_id)
    if state is None or state.get("source_sha256") != sha256(paths.original(doc_id)):
        state = build(paths, doc_id, use_ocr=use_ocr)
        save(paths, doc_id, state)
    return state


def mark_conflicts(page):
    """Flag boxes that cover part of a protected field (capacity, price, model...)."""
    for b in page["boxes"]:
        hits = sorted({p["kind"] for p in page.get("protected", [])
                       if detect.overlap(b, p) > 0.15 * max(detect.area(p), 1)})
        b["conflict"] = hits


def unresolved(page):
    """Automatic boxes over protected fields that the reviewer has not confirmed."""
    return [b for b in page["boxes"] if b["conflict"] and b["source"] == "auto" and not b["keep"]]


def clean_boxes(boxes, width, height):
    out = []
    for b in boxes:
        x0, x1 = sorted((int(b["x0"]), int(b["x1"])))
        y0, y1 = sorted((int(b["y0"]), int(b["y1"])))
        x0, x1 = max(0, x0), min(width, x1)
        y0, y1 = max(0, y0), min(height, y1)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        kind = b.get("kind") if b.get("kind") in BOX_KINDS else "manual"
        source = "auto" if b.get("source") == "auto" and kind != "manual" else "manual"
        out.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "kind": kind,
                    "source": source, "keep": b.get("keep") is True})
    return out


def update_page(state, index, boxes, approved):
    """Replace a page's boxes. Approval always travels with the exact boxes the
    reviewer saw, so an edit sent without approved=True leaves the page unapproved."""
    page = state["pages"][index]
    new = dict(page, boxes=clean_boxes(boxes, page["width"], page["height"]))
    mark_conflicts(new)
    if approved is True:
        if new.get("unsupported"):
            raise Rejected("page could not be rendered; it cannot be approved")
        if unresolved(new):
            raise Rejected("resolve masks over protected fields first (Delete, redraw, or K to keep)")
    new["approved"] = approved is True
    state["pages"][index] = new
    return new


def progress(state):
    pages = state["pages"]
    return sum(p["approved"] for p in pages), len(pages)
