import pytest

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("cv2")

from redact_synth import (  # noqa: E402
    CONSUMER, EMAIL, NAME, PHONE, PROTECTED, QUOTE_ITEMS, SENSITIVE_KEYS,
    coverage, ink, ocr_available, pdf_bytes, scan_bytes, without,
)
from tools.redact import detect  # noqa: E402

DENY = [NAME, "Sample Colony"]
needs_ocr = pytest.mark.skipif(not ocr_available(), reason="rapidocr not installed")


def detect_bytes(data, deny=DENY, keep=(), use_ocr=True):
    with pymupdf.open("pdf", data) as doc:
        return detect.detect_page(doc[0], deny, list(keep), use_ocr=use_ocr)


# --- patterns -------------------------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ("Call 98765 43210 today", "phone"),
    ("+91-9876543210", "phone"),
    ("Aadhaar 1234 5678 9012", "aadhaar"),
    ("ref ABCDE1234F", "pan"),
    ("USC 110223344556", "id_number"),
    ("write to ravi.test@example.com", "email"),
])
def test_pii_patterns(text, kind):
    assert kind in {k for _, _, k in detect.pii_spans(text)}


@pytest.mark.parametrize("text", [
    "Total Rs. 1,98,000.00", "₹1,23,456.50", "Rs.78,000/-", "3.3 kWp", "2.825 KWp",
    "6 Nos x 550 Wp", "GST 8.9%", "Date 05/10/2026", "TSM-NEG9R.28", "GSTIN 36ABCDE1234F1Z5",
    "Price 2,25,000", "Inverter 3000 W",
])
def test_quote_figures_are_not_pii(text):
    assert list(detect.pii_spans(text)) == []


@pytest.mark.parametrize("text,kind", [
    ("Total Rs. 1,98,000.00", "amount"), ("₹1,23,456.50", "amount"), ("Rs.78,000/-", "amount"),
    ("3.3 kWp", "capacity"), ("2.825 KWp", "capacity"), ("550Wp", "capacity"),
    ("6 Nos", "quantity"), ("GST 8.9%", "percent"), ("05/10/2026", "date"),
    ("5th October 2026", "date"), ("ABC-TOPCON-550M", "model"), ("XY3K-G1", "model"),
])
def test_protected_patterns(text, kind):
    assert kind in {k for _, _, k in detect.protected_spans(text)}


def test_pan_is_not_protected_as_model():
    assert "model" not in {k for _, _, k in detect.protected_spans("ABCDE1234F")}


def test_keep_terms_spare_vendor_contacts():
    text = "Sales 98765 43210"
    assert list(detect.pii_spans(text, keep=["98765 43210"])) == []
    assert list(detect.pii_spans(text, deny=["Sales"], keep=["Sales"]))  # denylist always wins


def test_denylist_file(tmp_path):
    p = tmp_path / "Q99.txt"
    p.write_text("# comment\nRavi  Testperson\n\nkeep: Sunshine Rooftop Solar\n", encoding="utf-8")
    deny, keep = detect.load_terms(p)
    assert deny == ["Ravi  Testperson"] and keep == ["Sunshine Rooftop Solar"]
    assert detect.load_terms(tmp_path / "missing.txt") == (None, [])
    assert list(detect.pii_spans("Mr RAVI TESTPERSON,", deny))


# --- native text pages ----------------------------------------------------------------

@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_sensitive_text_fully_masked(rotate):
    full = pdf_bytes(QUOTE_ITEMS, rotate=rotate)
    page = detect_bytes(full)
    for key in SENSITIVE_KEYS:
        mask = ink(full, pdf_bytes(without(QUOTE_ITEMS, key), rotate=rotate), page["zoom"])
        assert coverage(mask, page["boxes"]) == 1.0, key


@pytest.mark.parametrize("rotate", [0, 90])
def test_protected_fields_left_alone(rotate):
    full = pdf_bytes(QUOTE_ITEMS, rotate=rotate)
    page = detect_bytes(full)
    for key in PROTECTED:
        mask = ink(full, pdf_bytes(without(QUOTE_ITEMS, key), rotate=rotate), page["zoom"])
        assert coverage(mask, page["boxes"]) == 0.0, key
    kinds = {p["kind"] for p in page["protected"]}
    assert {"capacity", "quantity", "model", "date", "amount", "percent"} <= kinds


def test_vendor_letterhead_not_masked():
    full = pdf_bytes(QUOTE_ITEMS)
    page = detect_bytes(full)
    mask = ink(full, pdf_bytes(without(QUOTE_ITEMS, "vendor")), page["zoom"])
    assert coverage(mask, page["boxes"]) == 0.0


def test_rotation_flagged():
    assert "rotated" in detect_bytes(pdf_bytes(QUOTE_ITEMS, rotate=90))["warnings"]


def test_page_dimensions_follow_rotation():
    page = detect_bytes(pdf_bytes(QUOTE_ITEMS, rotate=90))
    assert page["width"] > page["height"]


# --- scans and OCR -------------------------------------------------------------------

@needs_ocr
def test_scanned_page_masked_with_ocr():
    full = scan_bytes(QUOTE_ITEMS)
    page = detect_bytes(full)
    assert "no_text_layer" in page["warnings"] and "scanned" in page["warnings"]
    for key in ["addr_name", "mob", "cons", "mail", "note"]:
        mask = ink(full, scan_bytes(without(QUOTE_ITEMS, key)), page["zoom"])
        assert coverage(mask, page["boxes"]) > 0.99, key
    for key in ["capacity", "price", "subsidy", "model"]:
        mask = ink(full, scan_bytes(without(QUOTE_ITEMS, key)), page["zoom"])
        assert coverage(mask, page["boxes"]) == 0.0, key


def test_scan_without_ocr_is_flagged():
    page = detect_bytes(scan_bytes(QUOTE_ITEMS), use_ocr=False)
    assert "ocr_skipped" in page["warnings"]
    assert page["boxes"] == []


@needs_ocr
def test_blank_scan_is_flagged_not_cleared():
    page = detect_bytes(scan_bytes([("x", 72, 72, " ")]))
    assert page["boxes"] == []
    assert "no_text_found" in page["warnings"]


def test_ocr_failure_flagged(monkeypatch):
    def boom(img):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(detect, "ocr_lines", boom)
    page = detect_bytes(scan_bytes(QUOTE_ITEMS))
    assert "ocr_failed" in page["warnings"]


def test_low_confidence_lines_become_hints(monkeypatch):
    fake = [detect.Line("smudged words", (100, 100, 400, 130), None, "ocr", 0.4)]
    monkeypatch.setattr(detect, "ocr_lines", lambda img: fake)
    page = detect_bytes(scan_bytes(QUOTE_ITEMS))
    assert "low_confidence" in page["warnings"]
    assert any(h["kind"] == "low_confidence" for h in page["hints"])


# --- signatures, stamps, codes -------------------------------------------------------

def _with_signature_stamp_qr(qr=True):
    import cv2
    import numpy as np

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for _, x, y, text in QUOTE_ITEMS:
        page.insert_text((x, y), text, fontsize=11, fontname="helv")
    # Vector signature.
    sh = page.new_shape()
    sh.draw_bezier((80, 560), (120, 520), (160, 600), (220, 550))
    sh.draw_bezier((220, 550), (250, 530), (270, 590), (300, 560))
    sh.finish(color=(0, 0, 0.6), width=1.5)
    sh.commit()
    # Round stamp image overlapping the price line.
    stamp = np.full((200, 200, 3), 255, np.uint8)
    cv2.circle(stamp, (100, 100), 90, (40, 40, 200), 6)
    cv2.putText(stamp, "PAID", (45, 115), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (40, 40, 200), 3)
    ok, png = cv2.imencode(".png", stamp)
    page.insert_image(pymupdf.Rect(150, 360, 230, 440), stream=png.tobytes(), overlay=True)
    if not qr:
        return doc.tobytes()
    # QR code holding a fake consumer number.
    qr = cv2.QRCodeEncoder.create().encode("synthetic " + CONSUMER)
    qr = cv2.resize(qr, (qr.shape[1] * 8, qr.shape[0] * 8), interpolation=cv2.INTER_NEAREST)
    qr = cv2.copyMakeBorder(qr, 32, 32, 32, 32, cv2.BORDER_CONSTANT, value=255)
    ok, png = cv2.imencode(".png", qr)
    page.insert_image(pymupdf.Rect(400, 560, 520, 680), stream=png.tobytes())
    return doc.tobytes()


def test_signature_stamp_and_qr():
    data = _with_signature_stamp_qr()
    page = detect_bytes(data)
    kinds = [h["kind"] for h in page["hints"]]
    assert "drawing" in kinds          # signature flagged for review
    assert kinds.count("image") >= 2   # stamp and QR images flagged
    qr_boxes = [b for b in page["boxes"] if b["kind"] == "qr"]
    assert qr_boxes, "QR code not proposed for masking"
    assert coverage(ink(data, _with_signature_stamp_qr(qr=False), page["zoom"]), qr_boxes) == 1.0
    # The price under the stamp stays protected and unmasked.
    price = [p for p in page["protected"] if p["kind"] == "amount"]
    assert price
    assert not any(detect.overlap(b, p) for b in page["boxes"] if b["kind"] != "qr" for p in price)
