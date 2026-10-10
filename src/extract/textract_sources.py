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
_PARTY = r"(?:customer|client|owner|buyer|consumer|purchaser)(?:'?s)?"
_SCOPE = re.compile(r"\bnot\s+(?:included|applicable|available|considered|supplied|provided|offered|part\s+of"
                    r"|in\s+(?:our\s+)?scope)\b"
                    r"|\bno\s+subsidy\b|\bwithout\s+(?:the\s+)?subsidy\b|\bexcluding\s+(?!gst\b)\w"
                    r"|\bexclusive\s+of\s+(?!gst\b)\w|\bnil\b|\bexcluded\b|\boptional\b"
                    rf"|\bby\s+(?:the\s+)?{_PARTY}\b|\b{_PARTY}\s+scope\b|\bscope\s+of\s+(?:the\s+)?{_PARTY}\b",
                    re.I)
# "Total before subsidy" is a total: relations to the subsidy are read before the subsidy keyword.
_BEFORE_SUBSIDY = re.compile(r"\b(?:before|without|excluding|exclusive\s+of|prior\s+to)\s+(?:the\s+)?"
                             r"(?:central\s+|state\s+|govt\.?\s+|government\s+)?(?:subsidy|cfa)\b", re.I)
_PRICE_WORD = re.compile(r"\btotal\b|\bcost\b|\bprice\b|\bamount\b|\bpayable\b|\bvalue\b", re.I)


def out_of_scope(text):
    """True when a line, row or answer's line denies or limits what it names: not supplied,
    not provided, not included, excluded, optional, not in scope, by the customer, customer
    scope, no subsidy. Every source asks this one function."""
    return bool(_SCOPE.search(_BEFORE_SUBSIDY.sub(" ", " ".join(str(text).split()))))


_TOTAL_REFERENCE = re.compile(r"\(?\b(?:included|includes|including|inside|outside|part|not\s+part|added)\s+"
                              r"(?:in|of|to|from)?\s*(?:the\s+)?(?:grand\s+)?total(?:\s+(?:amount|price|cost))?\)?",
                              re.I)


def role(text):
    """The one price role a label or line names: base_price, gst_amount, gross_total,
    net_cost or subsidy. None when it names none, more than one, or a negation."""
    t = _TOTAL_REFERENCE.sub(" ", " ".join(str(text).split()))  # "(included in Grand Total)" is another line's
    if out_of_scope(t):
        return None
    if _BEFORE_SUBSIDY.search(t) and _NET.search(t):
        return None  # "Net cost before subsidy": two roles for one amount
    if _NET.search(t):
        return "net_cost"
    if _BEFORE_SUBSIDY.search(t):
        rest = _BEFORE_SUBSIDY.sub(" ", t)
        return "gross_total" if _PRICE_WORD.search(rest) and not _SUBSIDY.search(rest) else None
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
_UNIT_AFTER = re.compile(r"^\s*(?:kwp|kw|kva|wp|w|nos?|pcs|panels?|modules?|years?|yrs?|kg|mm|sq)\b", re.I)
_PERCENT_AFTER = re.compile(r"^\s*(?:%|per\s*cent\b|percent\b)", re.I)
# "GST @ 18% on Rs 1,00,000": the amount after a rate is the base the tax is worked out on.
_TAX_BASE_BEFORE = re.compile(r"%\s*(?:on|of)\b[^%\d]{0,30}$", re.I)
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
        if (_UNIT_AFTER.match(after) or _PERCENT_AFTER.match(after) or before.rstrip().endswith("@")
                or _TAX_BASE_BEFORE.search(before)):
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
    """The single amount printed in text, or None for none or several, equal or not: two
    occurrences can carry two roles."""
    found = amounts_in(text)
    return found[0][0] if len(found) == 1 else None


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
_SETS = re.compile(r"\b(?:sets?|kits?|lots?|lump\s*sum|l\.?s\.?)\b", re.I)
# Inverter input wording: a maximum PV or DC input is not the inverter's rated output.
_INPUT = re.compile(r"\binput\b|\bmax(?:imum)?\b|\bpv\b|\bdc\b", re.I)
_OPTION_HEADING = re.compile(r"^\s*(?:option|alternative)\s*[-:.#]?\s*(\w{1,3})\b", re.I)


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
    """Candidates from one line (or a FORMS pair read as a line). A line that denies or
    limits what it names gives nothing."""
    out = []
    if out_of_scope(text):
        return out
    price_role = role(text)
    if price_role:
        raw = one_amount(text)
        if raw:
            out.append(candidate(price_role, raw, evidence, occurrence, True, source))
    if _PANEL.search(text) and not _INVERTER.search(text):
        count = one_count(text)
        # One "module", or modules counted in sets, kits or lots, is not a panel count.
        if count and int(count) >= 2 and not _SETS.search(text):
            out.append(candidate("panel_count", count, evidence, occurrence, True, source))
        watts = one_measure(text, ("w", "wp"))
        if watts:
            out.append(candidate("panel_wattage", watts, evidence, occurrence, True, source))
    elif (_CAPACITY_LABEL.search(text) and not _INVERTER.search(text) and not _PANEL.search(text)
          and not out_of_scope(text)):
        cap = one_measure(text, ("kw", "kwp", "w", "wp"))
        if cap:
            out.append(candidate("stated_capacity", cap, evidence, occurrence, True, source))
    if re.search(r"\bdate\b", text, re.I) and not re.search(r"\b(?:valid|due|delivery|expiry|install)", text, re.I):
        date = one_date(text)
        if date:
            out.append(candidate("quote_date", date, evidence, occurrence, True, source))
    return out


def from_lines(page, threshold):
    out = []
    for line in page.lines:
        if page.line_confidence(line) < threshold:
            continue
        text = " ".join(line["Text"].split())
        out += line_candidates(text, text, (line["Id"],), "lines")
    return out


def option_headings(page):
    """Distinct option labels the page's lines head sections with ("Option A", "Option B")."""
    return {m.group(1).upper() for line in page.lines if (m := _OPTION_HEADING.match(line["Text"]))}


# --- tables ----------------------------------------------------------------------------------

HEADER_KINDS = [
    ("skip", re.compile(r"s\.?\s*no|sr\.?\s*no|\bsl\b|hsn|sac|\brate\b|unit\s+price|per\s+unit", re.I)),
    ("qty", re.compile(r"\bqty\b|quantity|\bnos?\b|no\.?\s*of", re.I)),
    ("make", re.compile(r"\bmake\b|brand|manufacturer", re.I)),
    ("option", re.compile(r"\boption\b|alternative", re.I)),
    ("input", _INPUT),
    ("rating", re.compile(r"rated|output|\bac\b|rating|capacity|spec|wattage|size", re.I)),
    ("desc", re.compile(r"description|particulars|\bitem|material|component", re.I)),
    ("amount", re.compile(r"amount|value|price|cost|\brs\b|₹|\binr\b|total", re.I)),
]


def column_kinds(header):
    kinds = {}
    for col, text in header.items():
        kinds[col] = next((kind for kind, rx in HEADER_KINDS if rx.search(text)), None)
    return kinds


def bom_candidates(grid, header, confidence, threshold, table_no, row_merged=frozenset(), header_cells=()):
    """(module groups, inverters) from a bill-of-materials table: one bound group per row.
    Every cell a value comes from, and its column's header, must meet the threshold. A row
    that denies or limits what it names gives nothing; a quantity in sets, kits or lots, or
    in a cell merged across rows, gives no count; an option column binds the row's group to
    that option. An inverter's rating comes only from output wording, never an input column."""
    kinds = column_kinds(header)
    cols = {k: [c for c, kind in kinds.items() if kind == k] for k in ("desc", "qty", "make", "rating", "option")}
    if not cols["desc"] or len(cols["qty"]) != 1:
        return [], []
    qty_col = cols["qty"][0]
    if any(confidence.get(k, 0) < threshold for k in header_cells if k[1] in cols["desc"] + cols["qty"]):
        return [], []
    groups, inverters = [], []
    for r in sorted({r for r, _ in grid}):
        cells = {c: t for (rr, c), t in grid.items() if rr == r}
        desc = " ".join(cells.get(c, "") for c in cols["desc"])
        row_text = " | ".join(t for _, t in sorted(cells.items()) if t)
        if out_of_scope(row_text):
            continue

        def sure(col_list):
            """The text of these columns, or "" when a cell or its header is below the threshold."""
            out = []
            for c in col_list:
                if not cells.get(c):
                    continue
                heads = [k for k in header_cells if k[1] == c]
                if confidence.get((r, c), 0) < threshold or any(confidence.get(k, 0) < threshold for k in heads):
                    return ""
                out.append(cells[c])
            return " ".join(out)
        if confidence.get((r, cols["desc"][0]), 0) < threshold:
            continue
        rating, make = sure(cols["rating"]), sure(cols["make"]).strip() or None
        option = sure(cols["option"]).strip() or None
        unreadable = bool(cols["option"]) and option is None  # empty or below the threshold
        occurrence = ("table", table_no, r)
        if _PANEL.search(desc) and not _INVERTER.search(desc):
            qty = sure([qty_col])
            m = re.fullmatch(r"\s*(\d{1,3})\s*(?:nos?\.?|pcs\.?|numbers?)?\s*", qty, re.I)
            count = m.group(1) if m else None
            if (count is not None and int(count) < 2) or (r, qty_col) in row_merged \
                    or _SETS.search(f"{header.get(qty_col, '')} {qty} {desc}"):
                count = None  # one "module", a set or a quantity shared across rows: no count
            watts = one_measure(rating, ("w", "wp")) or one_measure(desc, ("w", "wp"))
            if count or watts:
                groups.append({"count": count, "wattage": watts, "make_model": make, "evidence": row_text,
                               "occurrence": occurrence, "option_id": option, "option_unreadable": unreadable})
        elif _INVERTER.search(desc) and not _PANEL.search(desc):
            kw = one_measure(rating, ("kw", "kva", "w"))
            if not kw and not _INPUT.search(desc):
                kw = one_measure(desc, ("kw", "kva", "w"))
            if kw or make:
                inverters.append({"rating": kw, "make_model": make, "evidence": row_text, "occurrence": occurrence,
                                  "option_id": option, "option_unreadable": unreadable})
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


_SUPPLIER = re.compile(r"\bsupplier\b|\bvendor\b|\bseller\b|\bour\b", re.I)
_BUYER = re.compile(r"\bbuyer\b|\bcustomer\b|\bclient\b|\brecipient\b|\bconsignee\b|\bbill(?:ed)?\s+to\b"
                    r"|\bship(?:ped)?\s+to\b|\bpurchaser\b|\bconsumer\b", re.I)
# A line that opens a section: "Supplier", "From", "Our details" or "Buyer", "Bill to", "To,".
_SUPPLIER_HEADING = re.compile(r"^\s*(?:supplier|vendor|seller|from|our)\b", re.I)
_BUYER_HEADING = re.compile(r"^\s*(?:buyer|customer|client|recipient|consignee|purchaser|consumer|bill(?:ed)?\s+to"
                            r"|ship(?:ped)?\s+to|to\s*[,:]?\s*$)", re.I)
SAME_ROW = 0.02  # headings this close in height sit side by side: which block a line is in is unclear


def _sections(lines):
    """Each line with the section it sits in: "letterhead" before the first heading, then
    "supplier" or "buyer" after a heading of that kind, or "unclear" after two headings of
    different kinds side by side. A page with no heading at all is unclear throughout: page
    position alone never decides."""
    section, last, out = "letterhead", None, []
    for line in lines:
        text = " ".join(line["Text"].split())
        short = len(text.split()) <= 4
        kind = None
        if short:
            kind = "buyer" if _BUYER_HEADING.match(text) else "supplier" if _SUPPLIER_HEADING.match(text) else None
        if kind:
            top = (line.get("Geometry") or {}).get("BoundingBox", {}).get("Top")
            beside = (last is not None and last[0] != kind and None not in (top, last[1])
                      and abs(top - last[1]) < SAME_ROW)
            section, last = ("unclear" if beside else kind), (kind, top)
        out.append((line, text, section))
    if all(s == "letterhead" for _, _, s in out):
        return [(line, text, "unclear") for line, text, _ in out]
    return out


def gstin_state(page, threshold=0):
    """(state, line text) from a valid GSTIN the page attributes to the supplier: on a line
    that says supplier, vendor, seller or our, in a section headed Supplier, From or Our, or
    in the letterhead (the block before the page's first section heading). A GSTIN on a buyer
    line or in a Buyer, Customer, Bill to, Ship to or To block is the buyer's. Unclear blocks,
    and two supplier GSTINs from different states, give none."""
    found = {}
    for line, text, section in _sections(page.lines):
        if page.line_confidence(line) < threshold or _BUYER.search(text):
            continue
        if not (_SUPPLIER.search(text) or section in ("supplier", "letterhead")):
            continue
        for m in _GSTIN.finditer(text.upper().replace(" ", "")):
            gstin = m.group(1)
            name = state_name(gstin[:2])
            if name and gstin_valid(gstin):
                found.setdefault(name, text)
    return next(iter(found.items())) if len(found) == 1 else None
