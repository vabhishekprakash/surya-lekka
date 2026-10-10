"""Read one page with Amazon Textract AnalyzeDocument (TABLES and QUERIES, page
bytes) and map the reply to the wire input Nova fills in. The result is
validated and normalised exactly as Nova's is, so the merge, the checks and
the web app see the same facts whichever engine read the page.

Mapping rules:
- raw is the query answer as Textract read it; evidence_text is the full text
  of every LINE the answer's box overlaps on that page; page is the original
  page number. Nothing that isn't on the page is added.
- An answer below the confidence threshold, one that doesn't parse as its
  field's type, or one whose text isn't in the lines it sits on (ignoring case,
  spaces, commas, Rs, ₹ and /-), is dropped (not found).
- A DCR answer counts only as a plain statement: with negation, doubt (subject
  to, on request, optional, TBC and so on) or both kinds named, in the answer
  or its line, it is not found and the household confirms it.
- Every surviving answer is kept, page by page. A value is never chosen
  across pages: different values reach merge_batches as a conflict for the
  user to resolve.
- Flags come only from positive evidence on the page. multiple_options is
  never "no", and extra_charges_complete and net_cost_subsidy_basis are never
  set: the user confirms those.
- A table gives options only when two or more of its rows each have a distinct
  system capacity, in a column whose header names capacity, size, system or
  kW, and an amount in a price column of the same row, both cells read with at
  least the threshold confidence. Each row's price kind comes from the header
  of the column it was taken from. Rows naming a component (inverter, panels,
  structure and so on) are bill-of-materials lines, never options.

The raw reply is returned to the caller under "response" for the spike's own
files; the worker keeps only the mapped facts.
"""

import re
import time
from decimal import Decimal

from checks.parse import parse_amount, parse_capacity

from . import textract_sources as src
from .nova_client import ExtractionFailure
from .textract_queries import KINDS, TARGETS, queries_config
from .wire_schema import capacity_value, count_value, normalise_batch, validate

MODEL_ID = "textract"
PRICE_PER_PAGE_USD = 0.020  # AnalyzeDocument with tables and queries, per page, Mumbai
MAX_IMAGE_BYTES = 10_000_000  # synchronous AnalyzeDocument limit for JPEG and PNG
MAX_ATTEMPTS = 4
READ_TIMEOUT_SECONDS = 60
CONNECT_TIMEOUT_SECONDS = 10
# One threshold for every answer (0 to 100), chosen on the eight development quotes
# only: of 0, 30, 40, 50, 60 and 70, 50 gave the most matches for the fewest wrong
# and falsely filled fields.
CONFIDENCE_THRESHOLD = 50.0
# A page Textract can't read fails that page only.
PAGE_ERRORS = {"BadDocumentException", "UnsupportedDocumentException", "DocumentTooLargeException",
               "InvalidParameterException"}
# Throttling left after the retries stops the job like a busy model.
THROTTLING = {"ThrottlingException", "ProvisionedThroughputExceededException", "LimitExceededException"}

GSTIN = re.compile(r"\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]")
CENTRAL = re.compile(r"\bmnre\b|pm[\s-]*surya[\s-]*ghar|\bcentral\b|\bcfa\b", re.I)
STATE = re.compile(r"\bstate\b|\btgredco\b|\bnredcap\b", re.I)
GST_INCLUDED = re.compile(r"\b(?:inclusive\s+of|including|incl\.?)\s+(?:all\s+)?(?:taxes\s+and\s+)?gst\b", re.I)
GST_EXCLUDED = re.compile(r"\b(?:plus|excluding|exclusive\s+of|excl\.?)\s+gst\b|\+\s*gst\b|\bextra\s+gst\b"
                          r"|\bgst\b[^\n]{0,25}?\b(?:extra|additional)\b", re.I)
GIVE_IT_UP = re.compile(r"\bgive\s+it\s+up\b", re.I)
NON_DCR = re.compile(r"\bnon[\s-]*(?:dcr|domestic)\b", re.I)
DCR = re.compile(r"\bdcr\b|\bdomestic\s+content\b", re.I)
# A DCR statement with any of these is not a plain statement: the household confirms it.
DCR_DOUBT = re.compile(r"\b(?:not|no|subject\s+to|if\s+available|on\s+request|optional|tbc|to\s+be\s+confirmed)\b",
                       re.I)
# What answer text and line text may differ by and still be the same text.
_LOOSE = re.compile(r"\brs\b\.?|\binr\b|₹|/-|[\s,]")
DC_BASIS = re.compile(r"kwp\b|\bdc\b", re.I)
AC_BASIS = re.compile(r"\bkva\b|\bac\b", re.I)
CAPACITY_HEADER = re.compile(r"capacity|size|system|\bkw", re.I)
HEADER_UNIT = re.compile(r"\b(kwp|kw)\b", re.I)
PRICE_HEADER = re.compile(r"price|cost|amount|total|payable|value|\brs\b|₹|\binr\b", re.I)
COMPONENT = re.compile(r"inverter|panel|module|structure|cable|wire|wiring|meter|earthing|battery|mounting"
                       r"|installation|acdb|dcdb|lightning|arrestor|civil|transport", re.I)
UNITS = {"capacity": {"kW", "kWp", "W", "Wp"}, "wattage": {"W", "Wp"}, "rating": {"kW", "W", "kVA"}}


def make_client(region, session=None):
    """Adaptive client-side rate limiting with at most four attempts per call,
    for Textract's per-account request rate. Calls are made one after another."""
    import boto3
    from botocore.config import Config

    config = Config(region_name=region, connect_timeout=CONNECT_TIMEOUT_SECONDS,
                    read_timeout=READ_TIMEOUT_SECONDS, retries={"mode": "adaptive", "total_max_attempts": MAX_ATTEMPTS})
    return (session or boto3).client("textract", config=config)


def build_request(page):
    return {"Document": {"Bytes": page.jpeg}, "FeatureTypes": ["TABLES", "QUERIES"],
            "QueriesConfig": queries_config()}


def request_size(pages):
    """Bytes sent for one page: the image itself."""
    return sum(len(p.jpeg) for p in pages)


def estimated_cost(pages):
    return round(pages * PRICE_PER_PAGE_USD, 3)


# --- reply helpers ------------------------------------------------------------------------

def _children(block, by_id, kind="CHILD"):
    return [by_id[i] for rel in block.get("Relationships") or [] if rel.get("Type") == kind
            for i in rel.get("Ids") or [] if i in by_id]


def _box(block):
    b = (block.get("Geometry") or {}).get("BoundingBox")
    return None if not b else (b["Left"], b["Top"], b["Left"] + b["Width"], b["Top"] + b["Height"])


def _overlaps(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _tidy(text):
    return " ".join(str(text).split())


class _Page:
    def __init__(self, reply):
        self.blocks = reply.get("Blocks") or []
        self.by_id = {b["Id"]: b for b in self.blocks if "Id" in b}
        lines = [b for b in self.blocks if b.get("BlockType") == "LINE" and _tidy(b.get("Text", ""))]
        self.lines = sorted(lines, key=lambda b: (_box(b) or (0, 0))[1::-1])

    def children(self, block, kind="CHILD"):
        return _children(block, self.by_id, kind)

    def evidence_lines(self, answer):
        """The LINE blocks evidence() quotes, or [] when the answer isn't on them."""
        wanted = _loose(answer.get("Text", ""))
        box = _box(answer)
        if box:
            found = [l for l in self.lines if _box(l) and _overlaps(box, _box(l))]
        else:
            found = [l for l in self.lines if wanted and wanted in _loose(l["Text"])]
        if not wanted or wanted not in _loose("".join(l["Text"] for l in found)):
            return []
        return found

    def evidence(self, answer):
        """The full text of every LINE the answer's box overlaps, top to bottom. Without
        a box, the lines that contain the answer's text. None when the answer's text
        isn't in those lines (overlap alone isn't enough): it is then not on the page."""
        wanted = _loose(answer.get("Text", ""))
        box = _box(answer)
        if box:
            found = [_tidy(l["Text"]) for l in self.lines if _box(l) and _overlaps(box, _box(l))]
        else:
            found = [_tidy(l["Text"]) for l in self.lines if wanted and wanted in _loose(l["Text"])]
        if not wanted or wanted not in _loose("".join(found)):
            return None
        return "\n".join(found)

    def answers(self):
        """(alias, answer block) for every answer, in reply order."""
        for block in self.blocks:
            if block.get("BlockType") != "QUERY":
                continue
            alias = (block.get("Query") or {}).get("Alias")
            for result in _children(block, self.by_id, "ANSWER"):
                if result.get("BlockType") == "QUERY_RESULT" and _tidy(result.get("Text", "")):
                    yield alias, result

    def cell_text(self, cell):
        return _tidy(" ".join(w.get("Text", "") for w in _children(cell, self.by_id) if w.get("BlockType") == "WORD"))

    def block_confidence(self, block):
        """The lowest confidence of a block and the words it holds."""
        words = [float(w.get("Confidence") or 0) for w in _children(block, self.by_id) if w.get("BlockType") == "WORD"]
        return min([float(block.get("Confidence", 100) or 0)] + words)

    def line_confidence(self, line):
        return self.block_confidence(line)

    def tables(self):
        """Each table as (grid {(row, col): text}, header {col: text}, confidence
        {(row, col): lowest confidence of the cell and its words}, merged_header,
        row_merged {cells merged across rows}, header_cells). A merged cell's text (its
        cells' words in order) fills every cell it covers, with the lowest confidence
        among them."""
        for table in (b for b in self.blocks if b.get("BlockType") == "TABLE"):
            cells = [c for c in _children(table, self.by_id) if c.get("BlockType") == "CELL"]
            grid = {(c["RowIndex"], c["ColumnIndex"]): self.cell_text(c) for c in cells}
            confidence = {(c["RowIndex"], c["ColumnIndex"]): self.block_confidence(c) for c in cells}
            row_merged = set()
            headers = {(c["RowIndex"], c["ColumnIndex"]) for c in cells if "COLUMN_HEADER" in (c.get("EntityTypes") or [])}
            for merged in _children(table, self.by_id, "MERGED_CELL"):
                parts = sorted(_children(merged, self.by_id), key=lambda c: (c["RowIndex"], c["ColumnIndex"]))
                text = _tidy(" ".join(self.cell_text(c) for c in parts))
                lowest = min((self.block_confidence(c) for c in parts), default=0.0)
                if len({c["RowIndex"] for c in parts}) > 1:  # shared by several rows: belongs to none
                    row_merged |= {(c["RowIndex"], c["ColumnIndex"]) for c in parts}
                for c in parts:
                    grid[(c["RowIndex"], c["ColumnIndex"])] = text
                    confidence[(c["RowIndex"], c["ColumnIndex"])] = lowest
                    if "COLUMN_HEADER" in (merged.get("EntityTypes") or []):
                        headers.add((c["RowIndex"], c["ColumnIndex"]))
            if not grid:
                continue
            if not headers:  # no header cells marked: the first row is the header
                first = min(r for r, _ in grid)
                headers = {k for k in grid if k[0] == first}
            header = {}
            for r, c in sorted(headers):
                header[c] = _tidy(f"{header.get(c, '')} {grid[(r, c)]}")
            header_rows = {r for r, _ in headers}
            merged_header = any(any(c["RowIndex"] in header_rows for c in _children(m, self.by_id))
                                and (m.get("ColumnSpan") or 1) > 1
                                for m in _children(table, self.by_id, "MERGED_CELL"))
            yield ({k: v for k, v in grid.items() if k[0] not in header_rows}, header, confidence, merged_header,
                   frozenset(row_merged), frozenset(headers))


# --- parsing answers -----------------------------------------------------------------------

def _parses(kind, text):
    if kind == "amount":
        return parse_amount(text)["parse_status"] == "ok"
    if kind in UNITS:
        r = parse_capacity(text)
        return r["parse_status"] == "ok" and r["unit"] in UNITS[kind]
    if kind == "count":
        return isinstance(count_value(text), int)
    if kind == "dcr":
        return _dcr_value(text) is not None
    return True


def _loose(text):
    return _LOOSE.sub("", str(text).casefold())


def _dcr_value(text):
    """dcr or non_dcr for a plain statement; None for doubt, negation or both kinds."""
    negative = bool(NON_DCR.search(text))
    positive = bool(DCR.search(NON_DCR.sub(" ", text)))
    if (negative and positive) or DCR_DOUBT.search(NON_DCR.sub(" ", text)):
        return None
    return "non_dcr" if negative else "dcr" if positive else None


def _subsidy_kind(text):
    central, state = bool(CENTRAL.search(text)), bool(STATE.search(text))
    return "combined" if central and state else "central" if central else "state" if state else "unspecified"


def _basis(text):
    dc, ac = bool(DC_BASIS.search(text)), bool(AC_BASIS.search(text))
    return "dc_kwp" if dc and not ac else "ac_kw" if ac and not dc else "unspecified"


def _flag(value, evidence, page):
    return {"value": value, "evidence_text": evidence, "page": page}


def _empty_wire():
    return {"multiple_options": {"value": "not_stated"}, "options": [], "prices": [], "subsidies": [],
            "capacities": [], "module_groups": [], "inverters": [], "extra_charges": []}


# --- tables ----------------------------------------------------------------------------------

def _option_rows(grid, header, confidence, threshold=CONFIDENCE_THRESHOLD, header_cells=()):
    """[(capacity cell, capacity raw, price cell, row text, price column)] when this
    table offers options, else []. A row whose capacity or price cell, or either column's
    header, was read below the threshold is left out."""
    capacity_cols = [c for c, h in header.items() if CAPACITY_HEADER.search(h)]
    price_cols = [c for c, h in header.items() if PRICE_HEADER.search(h) and c not in capacity_cols]
    if not capacity_cols or not price_cols:
        return []
    rows = []
    for r in sorted({r for r, _ in grid}):
        cells = {c: t for (rr, c), t in grid.items() if rr == r}
        text = " | ".join(t for _, t in sorted(cells.items()) if t)
        if COMPONENT.search(text) or src.out_of_scope(text):
            continue
        for col in capacity_cols:
            cell = cells.get(col, "")
            raw = cell
            if cell and parse_capacity(cell)["parse_status"] != "ok" and (unit := HEADER_UNIT.search(header[col])):
                raw = f"{cell} {unit.group(1)}"
            if cell and _parses("capacity", raw):
                break
        else:
            continue
        price_col = next((c for c in price_cols if cells.get(c) and _parses("amount", cells[c])), None)
        if price_col is None:
            continue
        heads = [k for k in header_cells if k[1] in (col, price_col)]
        if min([confidence.get((r, col), 0), confidence.get((r, price_col), 0)]
               + [confidence.get(k, 0) for k in heads]) < threshold:
            continue
        rows.append((cell, raw, cells[price_col], text, price_col,
                     min(confidence.get((r, col), 0), confidence.get((r, price_col), 0))))
    capacities = [parse_capacity(row[1]) for row in rows]
    keys = {(Decimal(str(c["parsed"])), c["unit"].lower().rstrip("p")) for c in capacities}
    return rows if len(rows) >= 2 and len(keys) == len(rows) else []


def _price_kind(header):
    """The role an option table's price column header names, or None: no default."""
    return src.role(header)


# --- the mapping -----------------------------------------------------------------------------

# Every source the reader can use; SOURCES are the ones the deployed reader uses, chosen on
# the development quotes by the acceptance rule (more matches, nothing new wrong).
ALL_SOURCES = frozenset({"queries", "options_table", "answer_values", "lines", "bom_table", "amount_table",
                         "forms", "expense", "gstin_state"})
# Kept on the 8 development quotes (more matches, nothing new wrong, falsely filled or falsely
# "doesn't match"): line patterns and bill-of-materials rows. The GSTIN's state is information
# only. Dropped: FORMS and AnalyzeExpense (nothing beyond these, at 3.5 times the price per page
# for FORMS), label | amount rows and typed values inside answers (no gain).
SOURCES = frozenset({"queries", "options_table", "lines", "bom_table", "gstin_state"})
QUERY_FIELDS = {"SYSTEM_CAPACITY": "stated_capacity", "PANEL_COUNT": "panel_count", "PANEL_WATTAGE": "panel_wattage",
                "PANEL_MAKE_MODEL": "panel_make", "INVERTER_MAKE_MODEL": "inverter_make",
                "INVERTER_CAPACITY": "inverter_rating", "PRICE_BEFORE_GST": "base_price", "GST_AMOUNT": "gst_amount",
                "TOTAL_PAYABLE": "gross_total", "SUBSIDY_AMOUNT": "subsidy", "NET_COST": "net_cost",
                "VENDOR_NAME": "vendor_name", "QUOTE_DATE": "quote_date"}
PRICE_FIELDS = ("base_price", "gst_amount", "gross_total", "net_cost", "subsidy")
GROUP_KEYS = {"panel_count": "count", "panel_wattage": "wattage", "panel_make": "make_model"}
INVERTER_KEYS = {"inverter_rating": "rating", "inverter_make": "make_model"}


def _typed_value(kind, raw):
    """A typed value inside an answer with extra words, when exactly one candidate fits."""
    if kind == "amount":
        return src.one_amount(raw)
    if kind in UNITS:
        found = src.one_measure(raw, tuple(u.lower() for u in UNITS[kind]))
        return found if found and _parses(kind, found) else None
    if kind == "count":
        found = src.one_count(raw) or (re.fullmatch(r"\s*(\d{1,3})\s*(?:solar\s+)?(?:panels?|modules?)\s*", raw, re.I)
                                       or [None, None])[1]
        return found
    return None


def _amount_on_its_line(raw, evidence, alias):
    """A query amount counts only when it is the one amount its line prints under the same
    rules as a line: not a percentage or a tax base, and the only amount on a subsidy line
    that names both the central and the state subsidy."""
    r = parse_amount(raw)
    if r["parse_status"] != "ok":
        return False
    on_line = [v for _, v in src.amounts_in(evidence)]
    if Decimal(str(r["parsed"])) not in on_line:
        return False
    if alias == "SUBSIDY_AMOUNT" and len(on_line) > 1 and _subsidy_kind(evidence) == "combined":
        return False
    return True


def _value_key(field, raw):
    if field in PRICE_FIELDS:
        r = parse_amount(raw)
        return ("amount", Decimal(str(r["parsed"])).normalize()) if r["parse_status"] == "ok" else ("raw", _loose(raw))
    if field in ("stated_capacity", "panel_wattage", "inverter_rating"):
        return src.measure_key(parse_capacity(raw))
    if field == "panel_count":
        value = count_value(raw)
        return ("count", value) if isinstance(value, int) else ("raw", _loose(raw))
    return ("text", " ".join(str(raw).casefold().split()))


def _resolve_occurrences(cands):
    """One printed occurrence fills at most one price field: when several fields claim it,
    only the field its own line or row names keeps it; otherwise none does."""
    by_occurrence = {}
    for c in cands:
        if c["field"] in PRICE_FIELDS and c["occurrence"]:
            by_occurrence.setdefault(c["occurrence"], []).append(c)
    dropped = set()
    for claims in by_occurrence.values():
        fields = {c["field"] for c in claims}
        if len(fields) < 2:
            continue
        named = {c["field"] for c in claims if c["named"]}
        keep = named.pop() if len(named) == 1 else None
        dropped |= {id(c) for c in claims if c["field"] != keep}
    return [c for c in cands if id(c) not in dropped]


def _distinct(field, cands):
    """Candidates with distinct values, first evidence kept for each."""
    out, seen = [], set()
    for c in cands:
        key = _value_key(field, c["raw"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _items(table_items, loose, keys, page_number, conflicts, list_name):
    """Panel groups (or inverters) for the page. Rows from a table bind their values. A
    loose value (a query answer or a line) that matches no row's value makes that key a
    conflict on every row; without table rows, loose values form the items, and different
    values for one key are a conflict."""
    items, options = [], []
    if table_items:
        for row in table_items:
            items.append({k: [(row[k], row["evidence"])] for k in keys if row.get(k)})
            options.append(row.get("option_id") or "All")  # a row in an option belongs to it, never to All
        for key, values in loose.items():
            for raw, evidence in values:
                if not any(key in it and _value_key(_field_of(key), it[key][0][0]) == _value_key(_field_of(key), raw)
                           for it in items):
                    # It disagrees with each row that has the key; it never fills a row that lacks
                    # it when there are several rows to choose from.
                    for it in items:
                        if key in it or len(items) == 1:
                            it.setdefault(key, []).append((raw, evidence))
    else:
        indexed = {}
        for key, values in loose.items():
            for index, (raw, evidence) in values:
                indexed.setdefault(index, {}).setdefault(key, []).append((raw, evidence))
        items = [indexed[i] for i in sorted(indexed)]
    out = []
    for n, item in enumerate(items):
        wire_item = {"option_id": options[n] if options else "All", "page": page_number}
        for key, values in item.items():
            distinct, seen = [], set()
            for raw, evidence in values:
                k = _value_key(_field_of(key), raw)
                if k not in seen:
                    seen.add(k)
                    distinct.append((raw, evidence))
            wire_item[key], wire_item[f"{key}_evidence"] = distinct[0]
            if len(distinct) > 1:
                conflicts.append(("item", list_name, n, key, distinct))
        if len(wire_item) > 2:
            out.append(wire_item)
    return out


def _field_of(key):
    return {"count": "panel_count", "wattage": "panel_wattage", "rating": "inverter_rating"}.get(key, "text")


def map_page(reply, page_number, threshold=CONFIDENCE_THRESHOLD, sources=None, expense=None):
    """(wire input for this page, confidence report). The report lists every answer
    with its alias, confidence and whether it was kept; never its text. sources picks
    the readers to use (SOURCES by default); expense is the page's AnalyzeExpense reply."""
    sources = SOURCES if sources is None else sources
    page = _Page(reply)
    wire, report = _empty_wire(), []
    cands, gstin, conflicts = [], None, []
    prov = []  # (slot, raw, evidence, rule, confidence) for every value written

    def note(slot, raw, evidence, rule, confidence):
        prov.append((slot, raw, evidence, rule, confidence))
    group_loose, inverter_loose = {}, {}
    seen = {}
    if "queries" in sources:
        for alias, answer in page.answers():
            if alias not in TARGETS:
                continue
            kind, raw = KINDS[alias], _tidy(answer["Text"])
            confidence = float(answer.get("Confidence") or 0)
            entry = {"alias": alias, "confidence": round(confidence, 1), "kept": False, "why": None}
            report.append(entry)
            if confidence < threshold:
                entry["why"] = "low_confidence"
                continue
            lines = page.evidence_lines(answer)
            evidence = page.evidence(answer)
            if not _parses(kind, raw):
                typed = _typed_value(kind, raw) if "answer_values" in sources else None
                if typed is None or evidence is None:
                    entry["why"] = "unparsed"
                    continue
                raw = typed
            if evidence is None:
                entry["why"] = "not_on_page"
                continue
            if any(page.line_confidence(l) < threshold for l in lines):
                entry["why"] = "low_confidence"
                continue
            if src.out_of_scope(evidence):  # "Subsidy not applicable", "not supplied", "optional"
                entry["why"] = "negated"
                continue
            if kind == "amount" and not _amount_on_its_line(raw, evidence, alias):
                entry["why"] = "not_an_amount_on_its_line"
                continue
            if alias == "INVERTER_CAPACITY" and src._INPUT.search(evidence) and not re.search(
                    r"\brated\b|\boutput\b|\bac\b", evidence, re.I):
                entry["why"] = "input_not_output"
                continue
            entry["kept"] = True
            target, key = TARGETS[alias]
            field = QUERY_FIELDS.get(alias)
            answer_confidence = min([confidence] + [page.line_confidence(l) for l in lines])
            occurrence = next((tuple([l["Id"]]) for l in lines if _loose(raw) in _loose(l["Text"])),
                              tuple(l["Id"] for l in lines))
            if target == "dcr_declaration":
                if _dcr_value(f"{raw}\n{evidence}") is None:
                    entry["kept"], entry["why"] = False, "unparsed"
                elif "dcr_declaration" not in wire:
                    wire["dcr_declaration"] = _flag(_dcr_value(raw), evidence, page_number)
                    note("dcr_declaration", None, evidence, "queries.dcr_declaration", answer_confidence)
            elif target == "vendor_registration":
                if GSTIN.fullmatch(re.sub(r"\s+", "", raw).upper()):
                    entry["kept"], entry["why"] = False, "gstin"
                    gstin = gstin or {"value": re.sub(r"\s+", "", raw).upper(), "evidence_text": evidence,
                                      "page": page_number}
                elif "vendor_registration" not in wire:
                    wire["vendor_registration"] = {"value": raw, "evidence_text": evidence, "page": page_number}
                    note("vendor_registration", raw, evidence, "queries.vendor_registration", answer_confidence)
            elif field in GROUP_KEYS or field in INVERTER_KEYS:
                index = seen[alias] = seen.get(alias, -1) + 1  # the nth answer to this query on the page
                loose = group_loose if field in GROUP_KEYS else inverter_loose
                loose.setdefault(key, []).append((index, (raw, evidence)))
                note(key, raw, evidence, f"queries.{field}", answer_confidence)
            else:
                named = src.role(evidence) == field if field in PRICE_FIELDS else True
                c = src.candidate(field, raw, evidence, occurrence, named, "queries")
                c["confidence"] = answer_confidence
                cands.append(c)

    if "lines" in sources:
        cands += src.from_lines(page, threshold)
    if "forms" in sources:
        for key_text, value_text, value in src.form_pairs(page):
            if float(value.get("Confidence") or 0) < threshold:
                continue
            lines = page.evidence_lines({"Text": value_text, "Geometry": value.get("Geometry")})
            evidence = page.evidence({"Text": value_text, "Geometry": value.get("Geometry")})
            # Like a line pattern, a pair counts only when its key is on the value's own line.
            if evidence and _loose(key_text) and _loose(key_text) in _loose(evidence):
                cands += src.line_candidates(f"{key_text} {value_text}", evidence, tuple(l["Id"] for l in lines),
                                             "forms")
    table_groups, table_inverters = [], []
    if "expense" in sources and expense:
        summary, items = src.expense_fields(expense)
        for kind, label, value, geometry, confidence in summary:
            if confidence < threshold:
                continue
            pseudo = {"Text": value, "Geometry": geometry}
            evidence = page.evidence(pseudo)
            if not evidence:
                continue
            occurrence = tuple(l["Id"] for l in page.evidence_lines(pseudo))
            if kind == "VENDOR_NAME":
                cands.append(src.candidate("vendor_name", _tidy(value), evidence, occurrence, True, "expense"))
            elif kind == "INVOICE_RECEIPT_DATE":
                date = src.one_date(value)
                if date:
                    cands.append(src.candidate("quote_date", date, evidence, occurrence, True, "expense"))
            else:
                price_role = src.role(label or evidence)
                raw = src.one_amount(value) if "%" not in value else None
                if price_role and raw:
                    cands.append(src.candidate(price_role, raw, evidence, occurrence, True, "expense"))
        for row in items:
            item_text, geometry, confidence = row.get("ITEM", ("", None, 0))
            evidence = page.evidence({"Text": item_text, "Geometry": geometry}) if item_text else None
            if not evidence or confidence < threshold:
                continue
            if src._PANEL.search(item_text) and not src._INVERTER.search(item_text):
                qty = re.fullmatch(r"\s*(\d{1,3})(?:\.0+)?\s*(?:nos?\.?)?\s*", row.get("QUANTITY", ("",))[0] or "")
                if qty:
                    table_groups.append({"count": qty.group(1), "wattage": src.one_measure(item_text, ("w", "wp")),
                                         "make_model": None, "evidence": evidence})
            elif src._INVERTER.search(item_text):
                kw = src.one_measure(item_text, ("kw", "kva", "w"))
                if kw:
                    table_inverters.append({"rating": kw, "make_model": None, "evidence": evidence})

    included = [_tidy(l["Text"]) for l in page.lines if GST_INCLUDED.search(l["Text"])]
    excluded = [_tidy(l["Text"]) for l in page.lines if GST_EXCLUDED.search(l["Text"])]
    if included or excluded:
        value = "unclear" if included and excluded else "included" if included else "excluded"
        wire["gst_treatment"] = _flag(value, (included or excluded)[0], page_number)
        note("flag:gst_treatment", None, (included or excluded)[0], "lines.gst_treatment",
             _lines_confidence(page, [l for l in page.lines if GST_INCLUDED.search(l["Text"])
                                      or GST_EXCLUDED.search(l["Text"])]))
    give = [_tidy(l["Text"]) for l in page.lines if GIVE_IT_UP.search(l["Text"])]
    if give:
        wire["give_it_up"] = _flag("mentioned", give[0], page_number)
        note("flag:give_it_up", None, give[0], "lines.give_it_up",
             _lines_confidence(page, [l for l in page.lines if GIVE_IT_UP.search(l["Text"])]))

    headings = src.option_headings(page)
    for table_no, (grid, header, confidence, merged_header, row_merged, header_cells) in enumerate(page.tables()):
        rows = (_option_rows(grid, header, confidence, threshold, header_cells)
                if "options_table" in sources else [])
        if rows:
            if wire["multiple_options"]["value"] != "yes":
                wire["multiple_options"] = _flag("yes", rows[0][3], page_number)
                note("flag:multiple_options", None, rows[0][3], "options_table.multiple_options",
                     min(row[5] for row in rows))
            price_role = None
            for cell, raw, price, text, price_col, row_confidence in rows:
                wire["options"].append({"option_id": cell, "label": text, "page": page_number})
                wire["capacities"].append({"option_id": cell, "raw": raw, "evidence_text": text, "page": page_number})
                note("capacity", raw, text, "options_table.stated_capacity", row_confidence)
                price_role = _price_kind(header[price_col])
                if price_role == "subsidy":
                    wire["subsidies"].append({"option_id": cell, "kind": _subsidy_kind(f"{header[price_col]} {text}"),
                                              "raw": price, "evidence_text": text, "page": page_number})
                    note("subsidy", price, text, "options_table.subsidy", row_confidence)
                elif price_role:  # a header that names no role leaves the amount unresolved
                    wire["prices"].append({"option_id": cell, "kind": price_role, "raw": price,
                                           "evidence_text": text, "page": page_number})
                    note(f"price:{price_role}", price, text, f"options_table.{price_role}", row_confidence)
            continue
        if merged_header:  # a merged header cell can't be tied to one column
            continue
        if "bom_table" in sources:
            g, i = src.bom_candidates(grid, header, confidence, threshold, table_no, row_merged, header_cells)
            if len(headings) >= 2:  # option sections on the page: a row without its option binds to none
                g = [x for x in g if x.get("option_id")]
                i = [x for x in i if x.get("option_id")]
            quarantined = [x for x in g + i if x.get("option_unreadable")]
            if quarantined:  # an option column whose cell can't be read: the row binds to nothing
                g = [x for x in g if not x.get("option_unreadable")]
                i = [x for x in i if not x.get("option_unreadable")]
                kept = [("yes", (g + i)[0]["evidence"])] if g + i else []
                conflicts.append(("flag", "multiple_options",
                                  kept + [("not_stated", x["evidence"]) for x in quarantined]))
            for row in g + i:
                option = row.get("option_id")
                if option and not any(o["option_id"] == option for o in wire["options"]):
                    wire["options"].append({"option_id": option, "label": option, "page": page_number})
                    wire["multiple_options"] = _flag("yes", row["evidence"], page_number)
                    note("flag:multiple_options", None, row["evidence"], "bom_table.multiple_options",
                         row["confidence"])
                for key in ("count", "wattage", "make_model", "rating"):
                    if row.get(key):
                        note(key, row[key], row["evidence"], f"bom_table.{key}", row["confidence"])
            table_groups += g
            table_inverters += i
        if "amount_table" in sources:
            cands += src.amount_table_candidates(grid, header, confidence, threshold, table_no)

    # Panel and inverter values from lines join the loose values; table rows bind theirs.
    for c in [c for c in cands if c["field"] in GROUP_KEYS or c["field"] in INVERTER_KEYS]:
        loose = group_loose if c["field"] in GROUP_KEYS else inverter_loose
        key = (GROUP_KEYS if c["field"] in GROUP_KEYS else INVERTER_KEYS)[c["field"]]
        loose.setdefault(key, []).append((0, (c["raw"], c["evidence"])))
        note(key, c["raw"], c["evidence"], f"{c['source']}.{c['field']}", c.get("confidence"))
    cands = [c for c in cands if c["field"] not in GROUP_KEYS and c["field"] not in INVERTER_KEYS]
    for loose, rows in ((group_loose, table_groups), (inverter_loose, table_inverters)):
        if rows:
            for key in loose:
                loose[key] = [value for _, value in loose[key]]
    wire["module_groups"] = _items(table_groups, group_loose, ("count", "wattage", "make_model"), page_number,
                                   conflicts, "module_groups")
    wire["inverters"] = _items(table_inverters, inverter_loose, ("rating", "make_model"), page_number,
                               conflicts, "inverters")

    cands = _resolve_occurrences(cands)
    by_field = {}
    for c in cands:
        by_field.setdefault(c["field"], []).append(c)
    for c in cands:
        slot = ("capacity" if c["field"] == "stated_capacity" else "subsidy" if c["field"] == "subsidy"
                else f"price:{c['field']}" if c["field"] in PRICE_FIELDS else c["field"])
        note(slot, c["raw"], c["evidence"], f"{c['source']}.{c['field']}", c.get("confidence"))
    for field, found in by_field.items():
        distinct = _distinct(field, found)
        if field == "stated_capacity":
            for c in distinct:
                wire["capacities"].append({"option_id": "All", "raw": c["raw"], "evidence_text": c["evidence"],
                                           "page": page_number})
            asked = [c for c in found if c["source"] == "queries"]  # only query answers propose the basis
            bases = {_basis(f"{c['raw']}\n{c['evidence']}") for c in asked}
            if asked:
                wire["capacity_basis"] = _flag(bases.pop() if len(bases) == 1 else "unspecified",
                                               asked[0]["evidence"], page_number)
                note("flag:capacity_basis", None, asked[0]["evidence"], "queries.capacity_basis",
                     min(c.get("confidence") or 0 for c in asked))
        elif field == "subsidy":
            for c in distinct:
                wire["subsidies"].append({"option_id": "All", "kind": _subsidy_kind(c["evidence"]), "raw": c["raw"],
                                          "evidence_text": c["evidence"], "page": page_number})
        elif field in PRICE_FIELDS:
            for c in distinct:
                wire["prices"].append({"option_id": "All", "kind": field, "raw": c["raw"],
                                       "evidence_text": c["evidence"], "page": page_number})
        else:  # vendor_name, quote_date
            wire[field] = {"value": distinct[0]["raw"], "evidence_text": distinct[0]["evidence"], "page": page_number}
            if len(distinct) > 1:
                conflicts.append(("plain", field, [(c["raw"], c["evidence"]) for c in distinct]))

    if gstin:
        wire["_vendor_gstin"] = gstin
    if "gstin_state" in sources and (state := src.gstin_state(page, threshold)):
        wire["_supplier_gst_state"] = {"value": state[0], "evidence_text": state[1], "page": page_number,
                                       "source": "gst_registration_state"}
    if conflicts:
        wire["_conflicts"] = conflicts
    wire["_provenance"] = prov
    return wire, report


PRIVATE = ("_vendor_gstin", "_supplier_gst_state", "_conflicts", "_provenance")


def _lines_confidence(page, lines):
    return min((page.line_confidence(l) for l in lines), default=None)


_FACT_SLOTS = {"stated_capacity": "capacity", "subsidy_central": "subsidy", "subsidy_state": "subsidy",
               "subsidy_combined": "subsidy", "subsidy_unspecified": "subsidy"}


def _raw_of(value):
    if isinstance(value, dict):
        return value.get("raw")
    return None if value is None or isinstance(value, bool) else value


def _attach_provenance(out, prov):
    """Give each value the source rules that wrote it and the lowest confidence among
    them. A value no rule claims gets the rule "unmatched", so it is never trusted."""
    def attach(f, slot):
        if not f or f.get("conflict") or f.get("value") is None:
            return
        raw = _raw_of(f["value"])
        hits = [(rule, conf) for s, r, evidence, rule, conf in prov
                if s == slot and evidence == f.get("evidence_text")
                and (r is None or raw is None or _loose(str(r)) == _loose(str(raw)))]
        f["rules"] = sorted({rule for rule, _ in hits}) or ["unmatched"]
        confidences = [conf for _, conf in hits if conf is not None]
        f["confidence"] = round(min(confidences), 1) if confidences else None

    for fact in out.get("facts") or []:
        attach(fact["field"], _FACT_SLOTS.get(fact["name"], f"price:{fact['name']}"))
    for list_name, keys in (("module_groups", ("count", "wattage", "make_model")),
                            ("inverters", ("rating", "make_model"))):
        for item in out.get(list_name) or []:
            for key in keys:
                attach(item.get(key), key)
    for name in ("dcr_declaration", "vendor_registration", "vendor_name", "quote_date"):
        attach(out.get(name), name)
    for name, f in ((out.get("flags") or {}).get("model_proposed") or {}).items():
        attach(f, f"flag:{name}")


def _conflict_field(list_key, candidates, page, batch):
    convert = {"count": count_value, "wattage": capacity_value, "rating": capacity_value}.get(list_key, str.strip)
    kind_value = {"wattage": {"raw": None, "parsed": None, "unit": None, "parse_status": "conflict"},
                  "rating": {"raw": None, "parsed": None, "unit": None, "parse_status": "conflict"}}.get(list_key)
    return {"value": kind_value, "evidence_text": None, "page": None, "batch": None, "conflict": True,
            "candidates": [{"value": convert(raw), "evidence_text": evidence, "page": page, "batch": batch}
                           for raw, evidence in candidates]}


def to_contract(wire, batch):
    """Validate and normalise the mapped wire input exactly as for Nova, then add the
    GSTIN (when a registration answer was one) and the supplier's GST registration
    state as evidence items, and mark values the page's own sources disagree on as
    conflicts for the household to resolve."""
    pages = sorted({wire[k]["page"] for k in wire if isinstance(wire[k], dict) and "page" in wire[k]}
                   | {i["page"] for k in wire if isinstance(wire[k], list) and k not in PRIVATE for i in wire[k]})
    pages = pages or [None]
    cleaned, errors, _ = validate({k: v for k, v in wire.items() if k not in PRIVATE}, pages)
    if errors:
        raise ExtractionFailure("invalid_mapping", "; ".join(errors[:10]))
    out = normalise_batch(cleaned, batch)
    gstin, state = wire.get("_vendor_gstin"), wire.get("_supplier_gst_state")
    out["vendor_gstin"] = None if gstin is None else {**gstin, "batch": batch}
    # Information only: where the supplier is registered for GST, never the household's state.
    out["supplier_gst_state"] = None if state is None else {**state, "batch": batch}
    _attach_provenance(out, wire.get("_provenance") or [])
    page = pages[0]
    for conflict in wire.get("_conflicts") or []:
        if conflict[0] == "item":
            _, list_name, index, key, candidates = conflict
            out[list_name][index][key] = _conflict_field(key, candidates, page, batch)
        elif conflict[0] == "flag":
            _, name, candidates = conflict
            out["flags"]["model_proposed"][name] = _conflict_field(name, candidates, page, batch)
        else:
            _, name, candidates = conflict
            out[name] = _conflict_field(name, candidates, page, batch)
    return out


def read_page(client, page, batch, threshold=CONFIDENCE_THRESHOLD):
    """One AnalyzeDocument call for one page. Returns a record with the contract facts,
    or raises ExtractionFailure (with .record) as a Nova batch would."""
    from botocore.exceptions import BotoCoreError, ClientError

    record = {"batch": batch, "model_id": MODEL_ID, "pages": [page.page], "request_bytes": len(page.jpeg)}
    if len(page.jpeg) > MAX_IMAGE_BYTES:
        failure = ExtractionFailure("size_limit", f"page is {len(page.jpeg)} bytes, over the {MAX_IMAGE_BYTES} limit")
        failure.record = record
        raise failure
    started = time.perf_counter()
    try:
        reply = client.analyze_document(**build_request(page))
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        failure = ExtractionFailure("api_error", code or "", code="ThrottlingException" if code in THROTTLING else code)
        record["seconds"] = round(time.perf_counter() - started, 3)
        failure.record = record
        raise failure from None
    except BotoCoreError as e:
        failure = ExtractionFailure("connection", type(e).__name__)
        record["seconds"] = round(time.perf_counter() - started, 3)
        failure.record = record
        raise failure from None
    record.update({"seconds": round(time.perf_counter() - started, 3), "usage": {"pages": 1}, "response": reply})
    wire, report = map_page(reply, page.page, threshold)
    record["confidence"] = report
    try:
        record["contract"] = to_contract(wire, batch)
    except ExtractionFailure as failure:
        failure.record = record
        raise
    return record


class TextractEngine:
    """The worker's and the spike's reader: one page per call, up to 10 MB a page."""

    model = MODEL_ID
    max_pages = 1
    request_limit = MAX_IMAGE_BYTES

    def __init__(self, client, threshold=CONFIDENCE_THRESHOLD):
        self.client, self.threshold = client, threshold

    def request_size(self, batch):
        return request_size(batch)

    def read(self, batch, number):
        (page,) = batch
        return read_page(self.client, page, number, self.threshold)

