"""Export an approved document as an image-only PDF and record it in the manifest.

The new PDF holds one raster image per page, painted from the reviewed render
with masks burnt into the pixels. Nothing from the original file (text,
fonts, attachments, annotations, metadata, layers) is copied.
"""

import io
import json
import re
from datetime import datetime, timezone

import pymupdf

from . import detect
from . import state as review_state
from .config import MAX_PDF_BYTES, ORDER

# Lossless first; JPEG only if the file would pass the size cap.
ENCODINGS = [("png", None), ("jpeg", 90), ("jpeg", 80), ("jpeg", 70)]
MAX_BYTES = MAX_PDF_BYTES


class NotReady(Exception):
    pass


class CheckFailed(Exception):
    pass


def _check_state(st, digest):
    if st is None:
        raise NotReady("no review state")
    if st["source_sha256"] != digest:
        raise NotReady("original changed since review; review it again")
    pending = [i + 1 for i, p in enumerate(st["pages"]) if not p["approved"]]
    if pending:
        raise NotReady(f"pages not approved: {pending}")
    if any(review_state.unresolved(p) or p.get("unsupported") for p in st["pages"]):
        raise NotReady("a page has unresolved masks or could not be rendered")


def masked_pixmap(page, ps):
    """Render the page exactly as reviewed and paint every box solid black."""
    pix, _ = detect.render(page, ps["zoom"])
    if (pix.width, pix.height) != (ps["width"], ps["height"]):
        raise NotReady("page size differs from review state")
    for b in ps["boxes"]:
        pix.set_rect(pymupdf.IRect(b["x0"], b["y0"], b["x1"], b["y1"]), (0, 0, 0))
    return pix


def _encode(pix, fmt, quality):
    if fmt == "png":
        return pix.tobytes("png")
    return pix.tobytes("jpeg", jpg_quality=quality)


def build_pdf(src_doc, st):
    """Return (pdf_bytes, encoding) for the masked document, within MAX_BYTES."""
    if len(src_doc) != len(st["pages"]):
        raise NotReady("page count differs from review state")
    pixmaps = [(masked_pixmap(page, ps), page.rect) for page, ps in zip(src_doc, st["pages"])]
    for fmt, quality in ENCODINGS:
        out = pymupdf.open()
        for pix, rect in pixmaps:
            new = out.new_page(width=rect.width, height=rect.height)
            new.insert_image(new.rect, stream=_encode(pix, fmt, quality))
        out.set_metadata({})
        out.del_xml_metadata()
        buf = io.BytesIO()
        out.save(buf, garbage=4, deflate=True, clean=True, no_new_id=True)
        out.close()
        data = buf.getvalue()
        if len(data) <= MAX_BYTES:
            return data, (fmt if quality is None else f"{fmt}-q{quality}")
    raise NotReady("export would exceed the 20 MB limit even at JPEG quality 70")


FORBIDDEN = re.compile(r"/(?:Font|EmbeddedFiles?|JavaScript|JS|Annots|AcroForm|OCProperties|"
                       r"OpenAction|AA|Metadata|Outlines|Names)(?![A-Za-z0-9])")


def verify_output(data, src_doc):
    """Automated checks on an exported file. Passing them does not prove the
    redaction is complete; that still needs the human final review."""
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        if len(doc) != len(src_doc):
            raise CheckFailed("page count differs from the original")
        if doc.embfile_count() or doc.get_toc() or doc.is_form_pdf:
            raise CheckFailed("output has attachments, outlines or forms")
        if any(doc.metadata.get(k) for k in ("title", "author", "subject", "keywords", "creator",
                                             "producer", "creationDate", "modDate")):
            raise CheckFailed("output has document metadata")
        if doc.get_xml_metadata():
            raise CheckFailed("output has XMP metadata")
        for page, src in zip(doc, src_doc):
            if page.get_text("text").strip():
                raise CheckFailed("output page has a text layer")
            if page.get_fonts() or page.first_annot or page.get_links() or page.first_widget:
                raise CheckFailed("output page has fonts, annotations or links")
            if len(page.get_images()) != 1:
                raise CheckFailed("output page is not a single image")
            if abs(page.rect.width - src.rect.width) > 0.5 or abs(page.rect.height - src.rect.height) > 0.5:
                raise CheckFailed("output page size differs from the original")
        for xref in range(1, doc.xref_length()):
            if FORBIDDEN.search(doc.xref_object(xref, compressed=True)):
                raise CheckFailed("output contains objects that are not page images")


def update_manifest(paths, entry):
    try:
        data = json.loads(paths.manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {"documents": []}
    docs = {d["doc_id"]: d for d in data["documents"]}
    docs[entry["doc_id"]] = entry
    rank = {d: i for i, d in enumerate(ORDER)}
    data["documents"] = sorted(docs.values(), key=lambda d: rank.get(d["doc_id"], len(rank)))
    data["note"] = ("Image-only PDFs: each page is a raster of the reviewed, masked render. "
                    "No text layer. Automated checks do not guarantee complete redaction.")
    review_state.write_json(paths.manifest, data)


def export(paths, doc_id):
    src_path = paths.original(doc_id)
    if src_path is None:
        raise NotReady("original not found")
    out_path = paths.output(doc_id)
    if out_path.resolve() == src_path.resolve():
        raise NotReady("output would overwrite the original")
    st = review_state.load(paths, doc_id)
    src_doc, digest = detect.open_source(src_path)
    with src_doc:
        _check_state(st, digest)
        data, encoding = build_pdf(src_doc, st)
        verify_output(data, src_doc)
    if review_state.sha256(src_path) != digest:
        raise CheckFailed("original changed during export")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(out_path)
    entry = {
        "doc_id": doc_id,
        "pages": len(st["pages"]),
        "image_only": True,
        "source_sha256": digest,
        "approved_by_human": True,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dpi": st["dpi"],
        "encoding": encoding,
        "bytes": len(data),
    }
    update_manifest(paths, entry)
    return entry


def verify_exported(paths, doc_id, entry):
    """Re-run the automated checks on a file already exported. Returns a list of problems."""
    problems = []
    src_path, out_path = paths.original(doc_id), paths.output(doc_id)
    if not out_path.is_file():
        return ["exported file missing"]
    if src_path is None:
        return ["original missing"]
    src_doc, digest = detect.open_source(src_path)
    with src_doc:
        if digest != entry.get("source_sha256"):
            problems.append("original hash differs from manifest")
        try:
            verify_output(out_path.read_bytes(), src_doc)
        except CheckFailed as e:
            problems.append(str(e))
    if out_path.stat().st_size > MAX_BYTES:
        problems.append("file is over 20 MB")
    return problems
