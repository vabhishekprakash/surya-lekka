"""Further sources for a page Textract has read, each through the same parsing as the
query answers:

- lines: "label value" patterns anchored to a label in the same LINE, with negation
  handling ("not included", "no subsidy").
- bom_table: bill-of-materials rows (Description/Particulars, Qty/Nos, Rating/
  Capacity/Specification, Make, Rate, Amount). Panel count, wattage and make are bound
  within one module row; inverter rows stay apart. S.No., HSN/SAC and rates are never a
  quantity. A table with a merged header cell is not used.
- amount_table: label | amount rows, by the role the label names.
- answer_values: a typed value inside a query answer with extra words, only when
  exactly one candidate has an unambiguous unit or amount.
- forms: FORMS key-value pairs, as "key value" lines.
- expense: AnalyzeExpense summary fields and line items. The field type only nominates
  a value; the role its own label or line names decides where it goes.
- gstin_state: the supplier's GST registration state from a valid GSTIN.

A price whose role its line or row doesn't name, or names twice, stays unresolved. A
percentage is never an amount. Ranges and alternatives give nothing. Amounts are never
worked out from a total or a difference. Every candidate keeps the full line or row it
came from as evidence.
"""

import json
import re
from decimal import Decimal
from pathlib import Path

from checks.parse import parse_amount, parse_capacity

GST_CODES = json.loads((Path(__file__).resolve().parents[1] / "rules" / "gst_state_codes.json")
                       .read_text(encoding="utf-8"))["codes"]

# --- roles -----------------------------------------------------------------------------------

_GST_QUALIFIER = re.compile(r"\(?\b(?:incl(?:uding|usive\s+of|\.)?|excl(?:uding|usive\s+of|\.)?|with|plus|before)"
                            r"\s+(?:all\s+)?(?:taxes\s*(?:and|&)?\s*)?gst\b\)?|\+\s*gst\b", re.I)
_NET = re.compile(r"\bnet\s+(?:cost|amount|payable|price|value)\b|\bafter\s+(?:the\s+)?(?:central\s+|state\s+)?"
                  r"(?:subsidy|cfa)\b|\beffective\s+(?:cost|price)\b|\bpost[\s-]subsidy\b", re.I)
_SUBSIDY = re.compile(r"\bsubsidy\b|\bcfa\b|\bmnre\b", re.I)
_GST = re.compile(r"\b(?:gst|cgst|sgst|igst)\b", re.I)
_BASE = re.compile(r"\bbasic\b|\bbase\s+(?:price|value|cost|amount)\b|\bsub[\s-]?total\b|\bbefore\s+gst\b"
                   r"|\btaxable\s+(?:value|amount)\b|\bexcl(?:uding|usive\s+of|\.)?\s+gst\b", re.I)
_TOTAL = re.compile(r"\bgrand\s+total\b|\btotal\b|\bpayable\b", re.I)
_NEGATION = re.compile(r"\bnot\s+(?:included|applicable|available|considered)\b|\bno\s+subsidy\b"
                       r"|\bwithout\s+(?:the\s+)?subsidy\b|\bexcluding\s+(?!gst\b)\w|\bexclusive\s+of\s+(?!gst\b)\w"
                       r"|\bnil\b", re.I)


def role(text):
    """The one price role a label or line names: base_price, gst_amount, gross_total,
    net_cost or subsidy. None when it names none, more than one, or a negation."""
    t = " ".join(str(text).split())
    if _NEGATION.search(t):
        return None
    if _NET.search(t):
        return "net_cost"
    if _SUBSIDY.search(t):
        return "subsidy"
    base = bool(_BASE.search(t))
    rest = _GST_QUALIFIER.sub(" ", t)
    rest = re.sub(r"sub[\s-]?total", " ", rest, flags=re.I)
    named = {"base_price"} if base else set()
    if _GST.search(rest):
        named.add("gst_amount")
    if _TOTAL.search(rest):
        named.add("gross_total")
    return named.pop() if len(named) == 1 else None


# --- values in text --------------------------------------------------------------------------

_CUR = r"(?:rs\.?|₹|inr)"
_AMOUNT_TOKEN = re.compile(
    rf"(?<![\w/.,])(?:{_CUR}\s*)?(?:\d+(?:\.\d+)?\s*(?:lakhs?|lacs?)\b"
    rf"|\d{{1,3}}(?:,\d{{2}})*,\d{{3}}(?:\.\d{{1,2}})?|\d+(?:\.\d{{1,2}})?)(?:\s*/-)?(?![\w%/])", re.I)
_UNIT_AFTER = re.compile(r"^\s*(?:kwp|kw|kva|wp|w|nos?|pcs|panels?|modules?|%|years?|yrs?|kg|mm|sq)\b", re.I)
_CAP_TOKEN = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(kwp|kw|kva|wp|w)\b", re.I)
_ALTERNATIVE = re.compile(r"\d\s*(?:kwp|kw|wp|w)?\s*(?:-|–|to|or|/)\s*\d", re.I)
_DATE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{1,2}(?:st|nd|rd|th)?[\s-]+[A-Za-z]{3,9}[\s,-]+\d{4}\b")


def amounts_in(text):
    """Rupee amounts written in text, with their raw text. Percentages, measures,
    dates and bare small numbers are not amounts."""
    out = []
    for m in _AMOUNT_TOKEN.finditer(text):
        token = m.group(0).strip()
        after = text[m.end():]
        before = text[:m.start()]
        if _UNIT_AFTER.match(after) or before.rstrip().endswith("@"):
            continue
        has_currency = bool(re.match(_CUR, token, re.I)) or "lakh" in token.lower() or "lac" in token.lower()
        digits = re.sub(r"\D", "", token.split(".")[0])
        if not has_currency and "," not in token and len(digits) < 4:
            continue
        r = parse_amount(token)
        if r["parse_status"] == "ok":
            out.append((token, Decimal(str(r["parsed"]))))
    return out


def one_amount(text):
    """The single amount in text, or None for none or several different ones."""
    found = amounts_in(text)
    values = {v for _, v in found}
    return found[0][0] if len(values) == 1 else None


def one_measure(text, units):
    """The single capacity with one of units in text, or None for none, several, a
    range or alternatives."""
    if _ALTERNATIVE.search(text):
        return None
    found = [(m.group(0), m.group(2).lower()) for m in _CAP_TOKEN.finditer(text)]
    found = [(raw, unit) for raw, unit in found if unit in units]
    keys = set()
    for raw, _ in found:
        r = parse_capacity(raw)
        keys.add(measure_key(r))
    return found[0][0] if len(keys) == 1 else None


def measure_key(r):
    """3 kW, 3 kWp, 3000 W and 3.0 kW compare equal; kVA only with kVA."""
    factor = {"kW": 1, "kWp": 1, "W": Decimal("0.001"), "Wp": Decimal("0.001")}.get(r.get("unit"))
    if r.get("parse_status") != "ok" or isinstance(r.get("parsed"), dict):
        return ("raw", r.get("raw"))
    if factor is None:
        return (r.get("unit"), Decimal(str(r["parsed"])).normalize())
    return ("kw", (Decimal(str(r["parsed"])) * factor).normalize())


_COUNT = re.compile(r"(?:\bno\.?\s*of|\bnumber\s+of|\bqty\.?|\bquantity)\b[^\d\n]{0,25}?(\d{1,3})\b(?!\s*(?:w|wp|kw|%|/|\.\d))"
                    r"|\b(\d{1,3})\s*(?:nos?\.?|pcs\.?|pieces|numbers)(?!\w)", re.I)
_PANEL = re.compile(r"\b(?:solar\s+)?(?:pv\s+)?(?:panels?|modules?)\b", re.I)
_INVERTER = re.compile(r"\binverters?\b", re.I)
_CAPACITY_LABEL = re.compile(r"\b(?:system|plant|capacity|project|rooftop|solar\s+pv)\b", re.I)


def one_count(text):
    values = {next(g for g in m.groups() if g) for m in _COUNT.finditer(text)}
    return values.pop() if len(values) == 1 else None


def one_date(text):
    values = {m.group(0) for m in _DATE.finditer(text)}
    return values.pop() if len(values) == 1 else None


# --- candidates ------------------------------------------------------------------------------

def candidate(field, raw, evidence, occurrence, named, source):
    return {"field": field, "raw": raw, "evidence": evidence, "occurrence": occurrence, "named": named,
            "source": source}


def line_candidates(text, evidence, occurrence, source):
    """Candidates from one line (or a FORMS pair read as a line)."""
    out = []
    price_role = role(text)
    if price_role:
        raw = one_amount(text)
        if raw:
            out.append(candidate(price_role, raw, evidence, occurrence, True, source))
    if _PANEL.search(text) and not _INVERTER.search(text):
        count = one_count(text)
        if count and int(count) >= 2:  # one "module" is a set or a lot, not a panel count
            out.append(candidate("panel_count", count, evidence, occurrence, True, source))
        watts = one_measure(text, ("w", "wp"))
        if watts:
            out.append(candidate("panel_wattage", watts, evidence, occurrence, True, source))
    elif (_CAPACITY_LABEL.search(text) and not _INVERTER.search(text) and not _PANEL.search(text)
          and not _NEGATION.search(text)):
        cap = one_measure(text, ("kw", "kwp", "w", "wp"))
        if cap:
            out.append(candidate("stated_capacity", cap, evidence, occurrence, True, source))
    if re.search(r"\bdate\b", text, re.I) and not re.search(r"\b(?:valid|due|delivery|expiry|install)", text, re.I):
        date = one_date(text)
        if date:
            out.append(candidate("quote_date", date, evidence, occurrence, True, source))
    return out


def from_lines(page):
    out = []
    for line in page.lines:
        text = " ".join(line["Text"].split())
        out += line_candidates(text, text, (line["Id"],), "lines")
    return out


# --- tables ----------------------------------------------------------------------------------

HEADER_KINDS = [
    ("skip", re.compile(r"s\.?\s*no|sr\.?\s*no|\bsl\b|hsn|sac|\brate\b|unit\s+price|per\s+unit", re.I)),
    ("qty", re.compile(r"\bqty\b|quantity|\bnos?\b|no\.?\s*of", re.I)),
    ("make", re.compile(r"\bmake\b|brand|manufacturer", re.I)),
    ("rating", re.compile(r"rating|capacity|spec|wattage|size", re.I)),
    ("desc", re.compile(r"description|particulars|\bitem|material|component", re.I)),
    ("amount", re.compile(r"amount|value|price|cost|\brs\b|₹|\binr\b|total", re.I)),
]


def column_kinds(header):
    kinds = {}
    for col, text in header.items():
        kinds[col] = next((kind for kind, rx in HEADER_KINDS if rx.search(text)), None)
    return kinds


def bom_candidates(grid, header, confidence, threshold, table_no):
    """(module groups, inverters) from a bill-of-materials table: one bound group per row."""
    kinds = column_kinds(header)
    cols = {k: [c for c, kind in kinds.items() if kind == k] for k in ("desc", "qty", "make", "rating")}
    if not cols["desc"] or len(cols["qty"]) != 1:
        return [], []
    groups, inverters = [], []
    for r in sorted({r for r, _ in grid}):
        cells = {c: t for (rr, c), t in grid.items() if rr == r}
        desc = " ".join(cells.get(c, "") for c in cols["desc"])
        rating = " ".join(cells.get(c, "") for c in cols["rating"])
        make = " ".join(cells.get(c, "") for c in cols["make"]).strip() or None
        row_text = " | ".join(t for _, t in sorted(cells.items()) if t)
        used = [(r, c) for c in cols["desc"] + cols["qty"] + cols["rating"] if cells.get(c)]
        if any(confidence.get(k, 0) < threshold for k in used):
            continue
        occurrence = ("table", table_no, r)
        if _PANEL.search(desc) and not _INVERTER.search(desc):
            qty = cells.get(cols["qty"][0], "")
            m = re.fullmatch(r"\s*(\d{1,3})\s*(?:nos?\.?|pcs\.?|numbers?)?\s*", qty, re.I)
            if not m or int(m.group(1)) < 2:  # one "module" is a set or a lot, not a panel count
                continue
            watts = one_measure(rating, ("w", "wp")) or one_measure(desc, ("w", "wp"))
            groups.append({"count": m.group(1), "wattage": watts, "make_model": make, "evidence": row_text,
                           "occurrence": occurrence})
        elif _INVERTER.search(desc) and not _PANEL.search(desc):
            kw = one_measure(rating, ("kw", "kva", "w")) or one_measure(desc, ("kw", "kva", "w"))
            if kw or make:
                inverters.append({"rating": kw, "make_model": make, "evidence": row_text, "occurrence": occurrence})
    return groups, inverters


def amount_table_candidates(grid, header, confidence, threshold, table_no):
    """Prices from label | amount rows, by the role each label names."""
    kinds = column_kinds(header)
    amount_cols = [c for c, kind in kinds.items() if kind == "amount"]
    out = []
    for r in sorted({r for r, _ in grid}):
        cells = {c: t for (rr, c), t in grid.items() if rr == r}
        values = [(c, cells[c]) for c in amount_cols if cells.get(c) and amounts_in(cells[c])]
        labels = [t for c, t in sorted(cells.items()) if c not in amount_cols and t]
        if len(values) != 1 or not labels:
            continue
        col, cell = values[0]
        label = " ".join(labels)
        price_role = role(label)
        raw = one_amount(cell)
        if not price_role or not raw or "%" in cell or min(confidence.get((r, col), 0),
                                                           *(confidence.get((r, c), 0) for c in cells)) < threshold:
            continue
        row_text = " | ".join(t for _, t in sorted(cells.items()) if t)
        out.append(candidate(price_role, raw, row_text, ("table", table_no, r, col), True, "amount_table"))
    return out


# --- FORMS and AnalyzeExpense ---------------------------------------------------------------

def form_pairs(page):
    """(key text, value text, value block) for each FORMS key-value pair."""
    out = []
    for block in page.blocks:
        if block.get("BlockType") != "KEY_VALUE_SET" or "KEY" not in (block.get("EntityTypes") or []):
            continue
        key = page.cell_text(block)
        for value in page.children(block, "VALUE"):
            out.append((key, page.cell_text(value), value))
    return out


EXPENSE_TYPES = {"TOTAL", "TAX", "SUBTOTAL", "AMOUNT_PAID", "AMOUNT_DUE", "VENDOR_NAME", "INVOICE_RECEIPT_DATE"}


def expense_fields(reply):
    """(type, label text, value text, value geometry, confidence) for each summary field,
    and a list of line items as {type: (value text, geometry)}."""
    summary, items = [], []
    for doc in (reply or {}).get("ExpenseDocuments") or []:
        for f in doc.get("SummaryFields") or []:
            kind = ((f.get("Type") or {}).get("Text") or "").upper()
            value = f.get("ValueDetection") or {}
            label = (f.get("LabelDetection") or {}).get("Text") or ""
            if kind in EXPENSE_TYPES and value.get("Text"):
                summary.append((kind, label, value["Text"], value.get("Geometry"), float(value.get("Confidence") or 0)))
        for group in doc.get("LineItemGroups") or []:
            for item in group.get("LineItems") or []:
                row = {}
                for f in item.get("LineItemExpenseFields") or []:
                    kind = ((f.get("Type") or {}).get("Text") or "").upper()
                    value = f.get("ValueDetection") or {}
                    if value.get("Text"):
                        row[kind] = (value["Text"], value.get("Geometry"), float(value.get("Confidence") or 0))
                if row:
                    items.append(row)
    return summary, items


# --- the GSTIN -------------------------------------------------------------------------------

_GSTIN = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9])\b")
_ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_valid(gstin):
    """The GSTIN check character: a weighted sum of the first 14 characters, base 36."""
    total = 0
    for i, ch in enumerate(gstin[:14]):
        product = _ALNUM.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _ALNUM[(36 - total % 36) % 36] == gstin[14]


def state_name(code):
    name = GST_CODES.get(code)
    if not name:
        return None
    return " ".join(w if w in ("AND",) else w.capitalize() for w in name.split()).replace("AND", "and")


def gstin_state(page):
    """(state, line text) for the first valid GSTIN on a line, or None."""
    for line in page.lines:
        text = " ".join(line["Text"].split())
        for m in _GSTIN.finditer(text.upper().replace(" ", "")):
            gstin = m.group(1)
            name = state_name(gstin[:2])
            if name and gstin_valid(gstin):
                return name, text
    return None
