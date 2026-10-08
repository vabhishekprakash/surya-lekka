"""Propose redaction boxes for a page, plus protected fields and review hints.

All rectangles are pixel boxes on the page image rendered at page["zoom"],
the same image the reviewer sees and the exporter paints. Sources: the PDF
text layer, local OCR where needed, and QR/barcode detection. Recognised text
stays in memory; only boxes and match kinds are returned.
"""

import hashlib
import re

import numpy as np
import pymupdf

from .config import DPI

PAD = 4                 # pixels added around every proposed box
MAX_SIDE_PX = 8000      # cap on rendered page size
LOW_CONF = 0.75         # OCR lines below this score are flagged for review
MIN_NATIVE_CHARS = 20   # fewer native characters than this means OCR the page

# --- personal data: patterns and contextual labels ----------------------------

PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "phone": re.compile(r"(?<![\d.,])(?:\+?91[\s-]*|0)?[6-9]\d{4}[\s-]?\d{5}(?![\d.,])"),
    "aadhaar": re.compile(r"(?<![\d.,])\d{4}[\s-]\d{4}[\s-]\d{4}(?![\d.,])"),
    "pan": re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Za-z0-9])"),
    # Long bare digit runs (consumer, meter or account numbers). Digits joined
    # by commas or dots are amounts, so they are excluded.
    "id_number": re.compile(r"(?<![\d.,])\d{9,}(?![\d.,])"),
}

_SEP = r"\s*(?:no|number|num|id|#)?\.?\s*[:\-.]?"
LABELS = [
    ("name", re.compile(
        r"\b(?:customer|consumer|client|applicant|beneficiary|owner)(?:'s)?\s*name" + _SEP
        + r"|\bname\s*(?:of\s*(?:the\s*)?(?:customer|consumer|applicant|beneficiary|owner))?\s*[:\-]",
        re.I)),
    ("name", re.compile(r"\b(?:mr|mrs|ms|smt|kum)\b\.?(?=\s*[A-Z])", re.I)),
    ("contact", re.compile(
        r"\b(?:mobile|mob|phone|ph|tel|contact|cell|whats\s?app)\b" + _SEP, re.I)),
    ("email", re.compile(r"\be-?mail(?:\s*id)?\s*[:\-]", re.I)),
    ("consumer_id", re.compile(
        r"\b(?:consumer|service|usc|unique\s+service|connection|meter|ca|sc|account|a/c)\s*"
        r"(?:no|number|num|id|#)\b\.?\s*[:\-.]?", re.I)),
    ("id_doc", re.compile(r"\b(?:aadhaa?r|pan)\s*(?:card)?\s*(?:no|number)?\.?\s*[:\-]", re.I)),
    ("address", re.compile(
        r"\b(?:(?:site|installation|billing|customer|consumer|residential|postal)\s+)?"
        r"address\s*[:\-]?", re.I)),
    ("address", re.compile(r"^\s*to\s*[,:]?\s*$", re.I)),
]
BLOCK_LABELS = {"address"}   # these also take the lines just below

# --- fields that must stay readable --------------------------------------------

_NUM = r"\d+(?:[.,]\d+)*"
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
PROTECT = {
    "capacity": re.compile(_NUM + r"\s*(?:kwp|kw|kva|kwh|wp|watts?|w)\b", re.I),
    "amount": re.compile(
        r"(?:₹|\brs\.?|\binr)\s*" + _NUM + r"(?:\s*/-)?"
        r"|(?<![\d.])\d{1,3}(?:,\d{2})*,\d{3}(?:\.\d+)?(?:\s*/-)?", re.I),
    "percent": re.compile(r"\d+(?:\.\d+)?\s*%"),
    "date": re.compile(
        r"(?<!\d)\d{1,2}[./-]\d{1,2}[./-](?:\d{4}|\d{2})(?!\d)"
        r"|\b\d{1,2}(?:st|nd|rd|th)?\s+" + _MONTHS + r",?\s+\d{4}"
        r"|\b" + _MONTHS + r"\s+\d{1,2},?\s+\d{4}", re.I),
    "quantity": re.compile(
        r"\b\d+\s*(?:nos?\.?|pcs|pieces|panels?|modules?|sets?|units?)(?![a-z])", re.I),
    # Letters and digits mixed in one token, e.g. module or inverter models.
    "model": re.compile(
        r"(?<![\w@.])(?=[\w./-]*[A-Za-z])(?=[\w./-]*\d)[A-Za-z0-9][\w./-]{3,}[A-Za-z0-9](?![\w@])"),
}


def load_terms(path):
    """Return (deny, keep) term lists, or (None, []) if the file is missing.

    One term per line. Blank lines and lines starting with # are ignored.
    Lines starting with "keep:" are public vendor details that patterns and
    labels should leave alone (denylist terms are always masked).
    """
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return None, []
    deny, keep = [], []
    for raw in lines:
        t = raw.strip()
        if not t or t.startswith("#"):
            continue
        if t.lower().startswith("keep:"):
            if t[5:].strip():
                keep.append(t[5:].strip())
        else:
            deny.append(t)
    return deny, keep


def term_pattern(term):
    words = [re.escape(w) for w in term.split()]
    return re.compile(r"(?<!\w)" + r"[\s,.]+".join(words) + r"(?!\w)", re.I)


def _spans(text, pats, kind=None):
    for k, pat in pats:
        for m in pat.finditer(text):
            if m.end() > m.start():
                yield m.start(), m.end(), kind or k


def pii_spans(text, deny=(), keep=()):
    """(start, end, kind) for denylist terms and personal-data patterns."""
    keep_spans = [(s, e) for s, e, _ in _spans(text, [(None, term_pattern(t)) for t in keep])]
    yield from _spans(text, [(None, term_pattern(t)) for t in deny], "term")
    for s, e, k in _spans(text, PATTERNS.items()):
        if k == "pan" and e - s < 10:
            continue
        if not any(ks <= s and e <= ke for ks, ke in keep_spans):
            yield s, e, k


def protected_spans(text):
    for s, e, k in _spans(text, PROTECT.items()):
        if k == "model" and PATTERNS["pan"].fullmatch(text[s:e]):
            continue
        yield s, e, k


def label_matches(text):
    """(kind, value_start) for each contextual label in a line."""
    for kind, pat in LABELS:
        for m in pat.finditer(text):
            start = m.end()
            while start < len(text) and text[start] in " :-.\t":
                start += 1
            yield kind, start


# --- geometry -------------------------------------------------------------------

def _box(r, kind, size, pad=PAD, **extra):
    w, h = size
    b = {
        "x0": max(0, int(r[0]) - pad), "y0": max(0, int(r[1]) - pad),
        "x1": min(w, int(np.ceil(r[2])) + pad), "y1": min(h, int(np.ceil(r[3])) + pad),
        "kind": kind,
    }
    b.update(extra)
    return b


def _union(rects):
    rects = [r for r in rects if r[2] > r[0] or r[3] > r[1]]
    if not rects:
        return None
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


def overlap(a, b):
    ix = max(0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
    iy = max(0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]))
    return ix * iy


def area(r):
    return max(0, r["x1"] - r["x0"]) * max(0, r["y1"] - r["y0"])


class Line:
    """One line of recognised text with a way to map character spans to pixels."""

    def __init__(self, text, rect, chars=None, source="text", score=1.0, frame=None, group=None):
        self.text, self.rect, self.chars = text, rect, chars
        self.source, self.score = source, score
        # Layout questions (what is right of or below a label) are answered in
        # the line's reading frame, where its text runs left to right.
        self.frame = frame or rect
        self.group = group or source

    def span_rect(self, start, end):
        if self.chars is not None:
            return _union([c for c, ch in zip(self.chars[start:end], self.text[start:end])
                           if not ch.isspace()]) or _union(self.chars[start:end])
        # OCR gives one box per line; slice it by character position with one
        # character of margin on each side.
        x0, y0, x1, y1 = self.rect
        n = max(len(self.text), 1)
        a = x0 + (x1 - x0) * max(start - 1, 0) / n
        b = x0 + (x1 - x0) * min(end + 1, n) / n
        return (a, y0, b, y1)

    @property
    def height(self):
        return self.frame[3] - self.frame[1]


# --- page sources ---------------------------------------------------------------

def page_zoom(page, dpi=DPI):
    z = dpi / 72
    side = max(page.rect.width, page.rect.height) * z
    if side > MAX_SIDE_PX:
        z *= MAX_SIDE_PX / side
    return z


def render(page, zoom):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False,
                          colorspace=pymupdf.csRGB, annots=True)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return pix, img


def _to_px(page, zoom):
    # Text and image coordinates are in unrotated page space; map onto the render.
    return page.rotation_matrix * pymupdf.Matrix(zoom, zoom)


_UPRIGHT = {a: pymupdf.Matrix(a) for a in (0, 90, 180, 270)}


def _upright(direction):
    """Rotation that turns a writing direction into left-to-right."""
    d = pymupdf.Point(direction)
    return max(_UPRIGHT.items(), key=lambda kv: (d * kv[1]).x)


def text_lines(page, zoom):
    mat = _to_px(page, zoom)
    lines = []
    for block in page.get_text("rawdict", flags=pymupdf.TEXTFLAGS_RAWDICT)["blocks"]:
        for line in block.get("lines", []):
            chars = [c for span in line["spans"] for c in span["chars"]]
            if not chars:
                continue
            rects = [tuple(pymupdf.Rect(c["bbox"]) * mat) for c in chars]
            text = "".join(c["c"] for c in chars)
            angle, up = _upright(line["dir"])
            frame = tuple(pymupdf.Rect(line["bbox"]) * up)
            lines.append(Line(text, _union(rects) or rects[0], rects, "text",
                              frame=frame, group=f"text{angle}"))
    return lines


_ocr = None


def ocr_engine():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def ocr_lines(img):
    import cv2

    result, _ = ocr_engine()(cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    lines = []
    for quad, text, score in result or []:
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        lines.append(Line(text, (min(xs), min(ys), max(xs), max(ys)), None, "ocr", float(score)))
    return lines


def code_boxes(img, size):
    """QR codes and barcodes, found with OpenCV."""
    import cv2

    boxes = []
    found = []
    detectors = [cv2.QRCodeDetector()]
    if hasattr(cv2, "QRCodeDetectorAruco"):
        detectors.insert(0, cv2.QRCodeDetectorAruco())
    for det in detectors:
        try:
            ok, points = det.detectMulti(img)
            if not (ok and points is not None):
                ok, points = det.detect(img)
                points = points.reshape(1, -1, 2) if ok and points is not None else None
            if points is not None:
                found += [("qr", q.reshape(-1, 2)) for q in points]
        except cv2.error:
            pass
    if hasattr(cv2, "barcode"):
        try:
            res = cv2.barcode.BarcodeDetector().detectAndDecode(img)
            points = res[-1]
            if points is not None:
                found += [("barcode", q) for q in points]
        except cv2.error:
            pass
    for kind, quad in found:
        xs, ys = quad[:, 0], quad[:, 1]
        boxes.append(_box((xs.min(), ys.min(), xs.max(), ys.max()), kind, size, pad=10))
    return boxes


def image_regions(page, zoom):
    mat = _to_px(page, zoom)
    out = []
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) * mat
        if not r.is_empty:
            out.append(tuple(r))
    return out


def curve_regions(page, zoom, gap=20):
    """Bounding boxes of vector paths with curves (signatures, round stamps)."""
    mat = _to_px(page, zoom)
    rects = []
    for d in page.get_drawings():
        if any(item[0] == "c" for item in d["items"]):
            r = pymupdf.Rect(d["rect"]) * mat
            rects.append([r.x0, r.y0, r.x1, r.y1])
    merged = []
    for r in rects:
        for m in merged:
            if r[0] <= m[2] + gap and m[0] <= r[2] + gap and r[1] <= m[3] + gap and m[1] <= r[3] + gap:
                m[:] = [min(m[0], r[0]), min(m[1], r[1]), max(m[2], r[2]), max(m[3], r[3])]
                break
        else:
            merged.append(list(r))
    return [tuple(m) for m in merged if (m[2] - m[0]) * (m[3] - m[1]) > 400]


# --- proposals --------------------------------------------------------------------

def _same_row(a, b):
    top, bot = max(a.frame[1], b.frame[1]), min(a.frame[3], b.frame[3])
    return bot - top > 0.5 * min(a.height, b.height)


def _label_value_lines(line, lines, kind):
    """Lines holding a label's value when it is not on the label's own line."""
    f = line.frame
    near = [o for o in lines if o is not line and o.group == line.group]
    right = [o for o in near if _same_row(line, o) and o.frame[0] >= f[2] - 2]
    if right and kind not in BLOCK_LABELS:
        return [min(right, key=lambda o: o.frame[0])]
    below = sorted((o for o in near if o.frame[1] >= f[3] - 0.3 * line.height),
                   key=lambda o: o.frame[1])
    out, last = [], line
    for o in below:
        if o.frame[1] - last.frame[3] > 1.2 * max(line.height, 1):
            break
        if o.frame[0] < f[0] - 2 * line.height or o.frame[0] > f[2] + 2 * line.height:
            continue
        out.append(o)
        last = o
        if kind not in BLOCK_LABELS or len(out) >= 3:
            break
    return out


def propose(lines, deny, keep, size):
    boxes, protected = [], []
    for line in lines:
        for s, e, k in pii_spans(line.text, deny, keep):
            if r := line.span_rect(s, e):
                boxes.append(_box(r, k, size))
        for s, e, k in protected_spans(line.text):
            if r := line.span_rect(s, e):
                protected.append(_box(r, k, size, pad=0))
        for kind, start in label_matches(line.text):
            value = line.text[start:].strip()
            if value and not any(ks <= start and len(line.text) <= ke for ks, ke, _ in
                                 _spans(line.text, [(None, term_pattern(t)) for t in keep])):
                if r := line.span_rect(start, len(line.text)):
                    boxes.append(_box(r, kind, size))
            if not value or kind in BLOCK_LABELS:
                for o in _label_value_lines(line, lines, kind):
                    boxes.append(_box(o.rect, kind, size))
    return boxes, protected


def dedupe(boxes):
    """Drop boxes that sit almost entirely inside a larger one."""
    kept = []
    for b in sorted(boxes, key=area, reverse=True):
        if not any(overlap(b, k) >= 0.9 * area(b) for k in kept):
            kept.append(b)
    return kept


def detect_page(page, deny, keep, use_ocr=True, dpi=DPI):
    zoom = page_zoom(page, dpi)
    out = {"zoom": zoom, "rotation": page.rotation, "boxes": [], "protected": [],
           "hints": [], "warnings": [], "unsupported": False}
    try:
        pix, img = render(page, zoom)
    except Exception:
        out.update(width=0, height=0, unsupported=True, warnings=["render_failed"])
        return out
    size = (pix.width, pix.height)
    out.update(width=pix.width, height=pix.height)
    page_area = pix.width * pix.height
    warn = out["warnings"]
    if page.rotation:
        warn.append("rotated")

    lines = text_lines(page, zoom)
    native = sum(len(l.text.strip()) for l in lines)
    images = image_regions(page, zoom)
    big_images = [r for r in images if (r[2] - r[0]) * (r[3] - r[1]) > 0.02 * page_area]
    if native < MIN_NATIVE_CHARS:
        warn.append("no_text_layer")
    if any((r[2] - r[0]) * (r[3] - r[1]) > 0.6 * page_area for r in images):
        warn.append("scanned")

    if native < MIN_NATIVE_CHARS or big_images:
        if not use_ocr:
            warn.append("ocr_skipped")
        else:
            try:
                ocr = ocr_lines(img)
            except Exception:
                ocr = []
                warn.append("ocr_failed")
            low = [l for l in ocr if l.score < LOW_CONF]
            if low:
                warn.append("low_confidence")
            out["hints"] += [_box(l.rect, "low_confidence", size) for l in low]
            lines += ocr
            if not ocr and "ocr_failed" not in warn and native < MIN_NATIVE_CHARS:
                warn.append("no_text_found")

    boxes, protected = propose(lines, deny, keep, size)
    try:
        boxes += code_boxes(img, size)
    except Exception:
        warn.append("code_detect_failed")
    out["boxes"] = [dict(b, source="auto", keep=False) for b in dedupe(boxes)]
    out["protected"] = dedupe(protected)
    out["hints"] += [_box(r, "image", size, pad=0) for r in images
                     if (r[2] - r[0]) * (r[3] - r[1]) <= 0.6 * page_area]
    out["hints"] += [_box(r, "drawing", size) for r in curve_regions(page, zoom)]
    return out


def open_source(path):
    """Read the original once and open it from memory. Returns (doc, sha256)."""
    data = path.read_bytes()
    ftype = path.suffix.lower().lstrip(".")
    doc = pymupdf.open(stream=data, filetype=ftype)
    if doc.needs_pass:
        doc.close()
        raise ValueError("document is password protected")
    return doc, hashlib.sha256(data).hexdigest()
