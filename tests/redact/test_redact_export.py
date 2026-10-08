import hashlib
import json
import re
import zlib

import numpy as np
import pytest

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("cv2")

from redact_synth import (  # noqa: E402
    CONSUMER, EMAIL, NAME, PHONE, QUOTE_ITEMS, make_page, ocr_available, paths, root, scan_bytes,
)
from tools.redact import export as exporter  # noqa: E402
from tools.redact import state as review_state  # noqa: E402
from tools.redact.config import REPO, ConfigError, Paths  # noqa: E402

SECRETS = [NAME, PHONE, CONSUMER, EMAIL, "Sample Colony", "attachment-secret", "Synthetic Author"]


def messy_source():
    """Three pages (portrait, rotated, landscape) with text, an annotation, a link,
    an attachment and metadata, none of which may reach the output."""
    doc = pymupdf.open()
    p1 = make_page(doc, QUOTE_ITEMS)
    p1.add_text_annot((400, 100), "note about " + NAME)
    p1.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 50, 300, 65),
                    "uri": "https://example.com/" + CONSUMER})
    make_page(doc, QUOTE_ITEMS, rotate=90)
    make_page(doc, QUOTE_ITEMS, width=842, height=595)
    doc.embfile_add("notes.txt", b"attachment-secret " + NAME.encode())
    doc.set_metadata({"author": "Synthetic Author", "title": NAME})
    return doc.tobytes()


def approve_all(paths, doc_id):
    st = review_state.get_or_build(paths, doc_id)
    for i, p in enumerate(st["pages"]):
        boxes = [dict(b, keep=True) for b in p["boxes"]]
        review_state.update_page(st, i, boxes, True)
    review_state.save(paths, doc_id, st)
    return st


def write_source(paths, doc_id, data):
    src = paths.originals / f"{doc_id}.pdf"
    src.write_bytes(data)
    paths.terms(doc_id).write_text(f"# synthetic\n{NAME}\nSample Colony\n", encoding="utf-8")
    return src


def all_stream_bytes(data):
    """Raw file bytes plus every decompressed stream."""
    chunks = [data]
    with pymupdf.open("pdf", data) as doc:
        for xref in range(1, doc.xref_length()):
            if doc.xref_is_stream(xref):
                chunks.append(doc.xref_stream(xref) or b"")
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            chunks.append(zlib.decompress(m.group(1)))
        except zlib.error:
            pass
    return b"".join(chunks)


# --- paths ----------------------------------------------------------------------

def test_data_root_inside_repo_rejected():
    with pytest.raises(ConfigError):
        Paths.from_root(REPO / "data")


def test_output_must_differ_from_originals(tmp_path):
    p = Paths(tmp_path / "o", tmp_path / "o", tmp_path / "r", tmp_path / "t")
    with pytest.raises(ConfigError):
        p.validate()


# --- review state -------------------------------------------------------------------

def test_state_resumes_and_holds_no_text(paths):
    write_source(paths, "Q10", messy_source())
    st = review_state.get_or_build(paths, "Q10")
    review_state.update_page(st, 0, st["pages"][0]["boxes"][:1], True)
    review_state.save(paths, "Q10", st)
    again = review_state.get_or_build(paths, "Q10")
    assert again["pages"][0]["approved"] and not again["pages"][1]["approved"]
    raw = paths.state("Q10").read_text(encoding="utf-8")
    for s in SECRETS:
        assert s.lower() not in raw.lower()


def test_edit_without_approval_unapproves(paths):
    write_source(paths, "Q10", messy_source())
    st = approve_all(paths, "Q10")
    review_state.update_page(st, 0, st["pages"][0]["boxes"], None)
    assert not st["pages"][0]["approved"]


def test_mask_over_protected_field_needs_resolution(paths):
    write_source(paths, "Q10", messy_source())
    st = review_state.get_or_build(paths, "Q10")
    page = st["pages"][0]
    price = next(p for p in page["protected"] if p["kind"] == "amount")
    bad = dict(price, kind="id_number", source="auto", keep=False)
    boxes = page["boxes"] + [bad]
    with pytest.raises(review_state.Rejected):
        review_state.update_page(st, 0, boxes, True)
    assert not st["pages"][0]["approved"]
    review_state.update_page(st, 0, boxes[:-1] + [dict(bad, keep=True)], True)  # kept on purpose
    assert st["pages"][0]["approved"]
    review_state.update_page(st, 0, boxes[:-1] + [dict(bad, source="manual")], True)  # drawn by hand
    assert st["pages"][0]["approved"]


def test_source_change_resets_review(paths):
    src = write_source(paths, "Q10", messy_source())
    approve_all(paths, "Q10")
    doc = pymupdf.open()
    make_page(doc, QUOTE_ITEMS[:3])
    src.write_bytes(doc.tobytes())
    with pytest.raises(exporter.NotReady):
        exporter.export(paths, "Q10")
    st = review_state.get_or_build(paths, "Q10")
    assert not any(p["approved"] for p in st["pages"])


# --- export ----------------------------------------------------------------------

def test_export_requires_every_page_approved(paths):
    write_source(paths, "Q10", messy_source())
    st = review_state.get_or_build(paths, "Q10")
    review_state.update_page(st, 0, st["pages"][0]["boxes"], True)
    review_state.save(paths, "Q10", st)
    with pytest.raises(exporter.NotReady):
        exporter.export(paths, "Q10")
    assert not paths.output("Q10").exists()


def test_export_is_image_only_and_source_untouched(paths):
    data = messy_source()
    src = write_source(paths, "Q10", data)
    before = hashlib.sha256(data).hexdigest()
    approve_all(paths, "Q10")
    entry = exporter.export(paths, "Q10")
    out = paths.output("Q10")

    assert out.name == "Q10.pdf"
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before == entry["source_sha256"]
    assert out.stat().st_size < 20 * 1024 * 1024
    with pymupdf.open(out) as doc, pymupdf.open("pdf", data) as orig:
        assert len(doc) == 3
        assert doc.embfile_count() == 0
        assert not any(v for k, v in doc.metadata.items() if k not in ("format", "encryption"))
        for page, o in zip(doc, orig):
            assert page.get_text().strip() == ""
            assert page.get_fonts() == [] and page.first_annot is None and page.get_links() == []
            assert len(page.get_images()) == 1
            assert abs(page.rect.width - o.rect.width) < 0.5
            assert abs(page.rect.height - o.rect.height) < 0.5
    blob = all_stream_bytes(out.read_bytes()).lower()
    for s in SECRETS + ["example.com", "Sunshine"]:
        assert s.lower().encode() not in blob

    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    (e,) = manifest["documents"]
    assert e["doc_id"] == "Q10" and e["pages"] == 3 and e["image_only"] is True
    assert e["approved_by_human"] is True and e["source_sha256"] == before and e["exported_at"]
    raw = paths.manifest.read_text(encoding="utf-8").lower()
    for s in SECRETS:
        assert s.lower() not in raw
    assert exporter.verify_exported(paths, "Q10", e) == []


def test_masks_are_burnt_into_pixels(paths):
    write_source(paths, "Q10", messy_source())
    st = approve_all(paths, "Q10")
    exporter.export(paths, "Q10")
    with pymupdf.open(paths.output("Q10")) as doc:
        for page, ps in zip(doc, st["pages"]):
            (img,) = page.get_images()
            pix = pymupdf.Pixmap(doc.extract_image(img[0])["image"])
            arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
            assert (pix.width, pix.height) == (ps["width"], ps["height"])
            assert ps["boxes"]
            for b in ps["boxes"]:
                region = arr[b["y0"]:b["y1"], b["x0"]:b["x1"]]
                assert region.max() <= 8, "masked region is not solid black"


@pytest.mark.skipif(not ocr_available(), reason="rapidocr not installed")
def test_ocr_of_export_cannot_read_masked_values(paths):
    from tools.redact import detect

    write_source(paths, "Q10", messy_source())
    approve_all(paths, "Q10")
    exporter.export(paths, "Q10")
    with pymupdf.open(paths.output("Q10")) as doc:
        _, img = detect.render(doc[0], 200 / 72)
        text = " ".join(l.text for l in detect.ocr_lines(img)).lower()
    for s in [NAME, PHONE, CONSUMER, EMAIL, "Sample Colony"]:
        assert s.lower() not in text
        assert s.lower().replace(" ", "") not in text.replace(" ", "")
    assert "3.3" in text and "kwp" in text.replace(" ", "")  # protected fields survive


def test_jpeg_fallback_and_size_cap(paths, monkeypatch):
    write_source(paths, "Q11", scan_bytes(QUOTE_ITEMS, noise=40))
    approve_all(paths, "Q11")
    st = review_state.load(paths, "Q11")
    from tools.redact import detect
    with detect.open_source(paths.original("Q11"))[0] as src:
        png_size = len(exporter.build_pdf(src, st)[0])
    monkeypatch.setattr(exporter, "MAX_BYTES", png_size - 1)
    entry = exporter.export(paths, "Q11")
    assert entry["encoding"].startswith("jpeg-q")
    assert paths.output("Q11").stat().st_size <= png_size - 1
    monkeypatch.setattr(exporter, "MAX_BYTES", 1000)
    with pytest.raises(exporter.NotReady):
        exporter.export(paths, "Q11")


def test_verify_detects_tampered_output(paths):
    write_source(paths, "Q10", messy_source())
    approve_all(paths, "Q10")
    entry = exporter.export(paths, "Q10")
    with pymupdf.open(paths.output("Q10")) as doc:
        doc[0].insert_text((72, 72), "leaked text")
        doc.save(paths.output("Q10").with_name("tmp.pdf"))
    paths.output("Q10").with_name("tmp.pdf").replace(paths.output("Q10"))
    assert exporter.verify_exported(paths, "Q10", entry)
