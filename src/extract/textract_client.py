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

from .nova_client import ExtractionFailure
from .textract_queries import KINDS, TARGETS, queries_config
from .wire_schema import count_value, normalise_batch, validate

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

    def tables(self):
        """Each table as (grid {(row, col): text}, header {col: text}, confidence
        {(row, col): cell confidence}). A merged cell's text (its cells' words in order)
        fills every cell it covers, with the lowest confidence among them."""
        for table in (b for b in self.blocks if b.get("BlockType") == "TABLE"):
            cells = [c for c in _children(table, self.by_id) if c.get("BlockType") == "CELL"]
            grid = {(c["RowIndex"], c["ColumnIndex"]): self.cell_text(c) for c in cells}
            confidence = {(c["RowIndex"], c["ColumnIndex"]): float(c.get("Confidence") or 0) for c in cells}
            headers = {(c["RowIndex"], c["ColumnIndex"]) for c in cells if "COLUMN_HEADER" in (c.get("EntityTypes") or [])}
            for merged in _children(table, self.by_id, "MERGED_CELL"):
                parts = sorted(_children(merged, self.by_id), key=lambda c: (c["RowIndex"], c["ColumnIndex"]))
                text = _tidy(" ".join(self.cell_text(c) for c in parts))
                lowest = min((float(c.get("Confidence") or 0) for c in parts), default=0.0)
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
            yield {k: v for k, v in grid.items() if k[0] not in header_rows}, header, confidence


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

def _option_rows(grid, header, confidence, threshold=CONFIDENCE_THRESHOLD):
    """[(capacity cell, capacity raw, price cell, row text, price column)] when this
    table offers options, else []. A row whose capacity or price cell was read below
    the threshold is left out."""
    capacity_cols = [c for c, h in header.items() if CAPACITY_HEADER.search(h)]
    price_cols = [c for c, h in header.items() if PRICE_HEADER.search(h) and c not in capacity_cols]
    if not capacity_cols or not price_cols:
        return []
    rows = []
    for r in sorted({r for r, _ in grid}):
        cells = {c: t for (rr, c), t in grid.items() if rr == r}
        text = " | ".join(t for _, t in sorted(cells.items()) if t)
        if COMPONENT.search(text):
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
        if min(confidence.get((r, col), 0), confidence.get((r, price_col), 0)) < threshold:
            continue
        rows.append((cell, raw, cells[price_col], text, price_col))
    capacities = [parse_capacity(raw) for _, raw, _, _, _ in rows]
    keys = {(Decimal(str(c["parsed"])), c["unit"].lower().rstrip("p")) for c in capacities}
    return rows if len(rows) >= 2 and len(keys) == len(rows) else []


def _price_kind(header):
    h = header.casefold()
    if "net" in h:
        return "net_cost"
    if re.search(r"before|excl|basic|base", h):
        return "base_price"
    return "gross_total"


# --- the mapping -----------------------------------------------------------------------------

def map_page(reply, page_number, threshold=CONFIDENCE_THRESHOLD):
    """(wire input for this page, confidence report). The report lists every answer
    with its alias, confidence and whether it was kept; never its text."""
    page = _Page(reply)
    wire, report = _empty_wire(), []
    groups, inverters, gstin = {}, {}, None
    seen = {}
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
        if not _parses(kind, raw):
            entry["why"] = "unparsed"
            continue
        evidence = page.evidence(answer)
        if evidence is None:
            entry["why"] = "not_on_page"
            continue
        entry["kept"] = True
        index = seen[alias] = seen.get(alias, -1) + 1  # the nth answer to this query on the page
        target, key = TARGETS[alias]
        if target == "capacities":
            wire["capacities"].append({"option_id": "All", "raw": raw, "evidence_text": evidence, "page": page_number})
            basis = _basis(f"{raw}\n{evidence}")
            current = wire.get("capacity_basis")
            if current is None:
                wire["capacity_basis"] = _flag(basis, evidence, page_number)
            elif current["value"] != basis:
                current["value"] = "unspecified"
        elif target == "prices":
            wire["prices"].append({"option_id": "All", "kind": key, "raw": raw, "evidence_text": evidence,
                                   "page": page_number})
        elif target == "subsidies":
            wire["subsidies"].append({"option_id": "All", "kind": _subsidy_kind(evidence), "raw": raw,
                                      "evidence_text": evidence, "page": page_number})
        elif target in ("module_groups", "inverters"):
            items = groups if target == "module_groups" else inverters
            item = items.setdefault(index, {"option_id": "All", "page": page_number})
            item[key], item[f"{key}_evidence"] = raw, evidence
        elif target == "dcr_declaration":
            if _dcr_value(f"{raw}\n{evidence}") is None:
                entry["kept"], entry["why"] = False, "unparsed"
            elif "dcr_declaration" not in wire:
                wire["dcr_declaration"] = _flag(_dcr_value(raw), evidence, page_number)
        elif target == "vendor_registration" and GSTIN.fullmatch(re.sub(r"\s+", "", raw).upper()):
            entry["kept"], entry["why"] = False, "gstin"
            gstin = gstin or {"value": re.sub(r"\s+", "", raw).upper(), "evidence_text": evidence, "page": page_number}
        elif target not in wire:
            wire[target] = {"value": raw, "evidence_text": evidence, "page": page_number}
    wire["module_groups"] = [groups[i] for i in sorted(groups)]
    wire["inverters"] = [inverters[i] for i in sorted(inverters)]

    included = [_tidy(l["Text"]) for l in page.lines if GST_INCLUDED.search(l["Text"])]
    excluded = [_tidy(l["Text"]) for l in page.lines if GST_EXCLUDED.search(l["Text"])]
    if included or excluded:
        value = "unclear" if included and excluded else "included" if included else "excluded"
        wire["gst_treatment"] = _flag(value, (included or excluded)[0], page_number)
    give = [_tidy(l["Text"]) for l in page.lines if GIVE_IT_UP.search(l["Text"])]
    if give:
        wire["give_it_up"] = _flag("mentioned", give[0], page_number)

    for grid, header, confidence in page.tables():
        rows = _option_rows(grid, header, confidence, threshold)
        if not rows:
            continue
        if wire["multiple_options"]["value"] != "yes":
            wire["multiple_options"] = _flag("yes", rows[0][3], page_number)
        for cell, raw, price, text, price_col in rows:
            wire["options"].append({"option_id": cell, "label": text, "page": page_number})
            wire["capacities"].append({"option_id": cell, "raw": raw, "evidence_text": text, "page": page_number})
            wire["prices"].append({"option_id": cell, "kind": _price_kind(header[price_col]), "raw": price,
                                   "evidence_text": text, "page": page_number})
    if gstin:
        wire["_vendor_gstin"] = gstin
    return wire, report


def to_contract(wire, batch):
    """Validate and normalise the mapped wire input exactly as for Nova, then add the
    GSTIN, when a registration answer was one, as its own evidence item."""
    pages = sorted({wire[k]["page"] for k in wire if isinstance(wire[k], dict) and "page" in wire[k]}
                   | {i["page"] for k in wire if isinstance(wire[k], list) for i in wire[k]}) or [None]
    gstin = wire.get("_vendor_gstin")
    cleaned, errors, _ = validate({k: v for k, v in wire.items() if k != "_vendor_gstin"}, pages)
    if errors:
        raise ExtractionFailure("invalid_mapping", "; ".join(errors[:10]))
    out = normalise_batch(cleaned, batch)
    out["vendor_gstin"] = None if gstin is None else {**gstin, "batch": batch}
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

