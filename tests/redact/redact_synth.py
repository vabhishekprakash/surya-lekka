"""Synthetic documents for the redaction tool tests. No real data is used."""

import numpy as np
import pymupdf
import pytest

from tools.redact import detect
from tools.redact.config import Paths

# Made-up personal details.
NAME = "Ravi Testperson"
PHONE = "98765 43210"
CONSUMER = "110223344556"
EMAIL = "ravi.test@example.com"
STREET = "12-3 Sample Colony"
TOWN = "Testnagar 500001"

# Fields that must stay readable.
PROTECTED = {
    "capacity": "System capacity: 3.3 kWp",
    "quantity": "Modules: 6 Nos x 550 Wp",
    "model": "Module model: ABC-TOPCON-550M",
    "inverter": "Inverter model: XY3K-G1",
    "date": "Quotation date: 05/10/2026",
    "price": "Total price: Rs. 1,98,000.00",
    "gst": "GST 8.9% included",
    "subsidy": "Subsidy: Rs. 78,000/-",
}

NOTE = "Thank you for your enquiry, "

# (key, x, y, text). Keys let a test drop one item and diff the renders.
QUOTE_ITEMS = [
    ("vendor", 72, 60, "Sunshine Rooftop Solar Pvt Ltd"),
    ("to", 72, 100, "To,"),
    ("addr_name", 72, 116, NAME),
    ("addr_street", 72, 132, STREET),
    ("addr_town", 72, 148, TOWN),
    ("mob_label", 72, 190, "Mobile:"),
    ("mob", 160, 190, PHONE),
    ("cons_label", 72, 210, "Consumer No:"),
    ("cons", 160, 210, CONSUMER),
    ("mail_label", 72, 230, "Email:"),
    ("mail", 160, 230, EMAIL),
] + [(k, 72, 280 + 20 * i, t) for i, (k, t) in enumerate(PROTECTED.items())] + [
    ("note", 72, 480, NOTE + NAME),
]
SENSITIVE_KEYS = ["addr_name", "addr_street", "addr_town", "mob", "cons", "mail", "note"]


def without(items, key):
    """Items with one entry dropped; for the note line only the name is dropped."""
    if key == "note":
        return [(k, x, y, NOTE if k == "note" else t) for k, x, y, t in items]
    return [i for i in items if i[0] != key]


def make_page(doc, items, skip=(), rotate=0, width=595, height=842):
    page = doc.new_page(width=width, height=height)
    for key, x, y, text in items:
        if key not in skip:
            page.insert_text((x, y), text, fontsize=11, fontname="helv")
    if rotate:
        page.set_rotation(rotate)
    return page


def pdf_bytes(items, skip=(), rotate=0):
    doc = pymupdf.open()
    make_page(doc, items, skip, rotate)
    return doc.tobytes()


def scan_bytes(items, skip=(), dpi=150, noise=0):
    """A page that is only a picture of the text, like a scan."""
    src = pymupdf.open("pdf", pdf_bytes(items, skip))
    pix = src[0].get_pixmap(dpi=dpi, alpha=False)
    if noise:
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).astype(np.int16)
        rng = np.random.default_rng(0)
        img = np.clip(img + rng.integers(-noise, noise + 1, img.shape), 0, 255).astype(np.uint8)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix.width, pix.height, img.tobytes(), False)
    out = pymupdf.open()
    page = out.new_page(width=src[0].rect.width, height=src[0].rect.height)
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    return out.tobytes()


def render_array(data, zoom):
    with pymupdf.open("pdf", data) as d:
        _, img = detect.render(d[0], zoom)
        return img.copy()


def ink(full, without, zoom):
    """Pixels that belong to the dropped item: where the two renders differ."""
    a, b = render_array(full, zoom), render_array(without, zoom)
    return np.abs(a.astype(int) - b.astype(int)).max(axis=2) > 40


def coverage(mask, boxes):
    """Fraction of the mask's pixels that fall inside any box."""
    inside = np.zeros_like(mask)
    for bx in boxes:
        inside[bx["y0"]:bx["y1"], bx["x0"]:bx["x1"]] = True
    total = mask.sum()
    assert total > 0, "dropped item left no ink"
    return (mask & inside).sum() / total




def ocr_available():
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.fixture
def root(tmp_path):
    for d in ("originals", "redacted", "review", "pii_terms"):
        (tmp_path / d).mkdir()
    return tmp_path


@pytest.fixture
def paths(root):
    return Paths.from_root(root)
