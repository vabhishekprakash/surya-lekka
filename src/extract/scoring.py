"""Answer key parsing and per-field scoring for the extraction spike.

Answer key format (plain text, written by hand):

    [Q12]
    stated_capacity: 3.3 kWp | 1
    base_price: Rs. 1,80,000 | 2
    discount: null
    panel_count: 6 + 4 | 1

A block starts with a line holding only the document id, written as [Q12],
## Q12, Q12: or Q12 (the id must contain a letter and a digit). Each key line is
"key: value | page"; the page is optional. null, none, -, n/a, not found or
an empty value mean the quote does not state it. For several panel or
inverter lines, separate the values with + or ;. Lines starting with # or //
are comments.

Each field is scored as correct, wrong, missing (the key has a value, the
extraction has none), falsely_populated (the key says not stated, the
extraction has a value) or abstained (both empty). A field the merge marked as
a conflict counts as missing or falsely_populated, and is noted.
"""

import re
from decimal import Decimal, InvalidOperation

from checks.parse import parse_amount, parse_capacity

from .wire_schema import AMOUNT_FIELDS

STATUSES = ("correct", "wrong", "missing", "falsely_populated", "abstained")
ABSENT = {"", "null", "none", "-", "--", "n/a", "na", "nil", "not found", "not stated", "absent", "not given"}
YES = {"yes", "y", "true", "dcr", "mentioned", "stated"}
NO = {"no", "n", "false", "non-dcr", "non_dcr", "not dcr"}

# key -> (where, kind). where is a top-level field, ("list", list, field) or ("flag", name).
FIELDS = {
    "stated_capacity": ("stated_capacity", "kw"),
    "panel_count": (("list", "module_groups", "count"), "count"),
    "panel_wattage": (("list", "module_groups", "wattage"), "w"),
    "module_make_model": (("list", "module_groups", "make_model"), "text"),
    "inverter_make_model": (("list", "inverters", "make_model"), "text"),
    "inverter_rating": (("list", "inverters", "rating"), "kw"),
    "dcr_declaration": ("dcr_declaration", "bool"),
    "vendor_registration": ("vendor_registration", "text"),
    **{name: (name, "amount") for name in AMOUNT_FIELDS},
    "multiple_options": (("flag", "multiple_options"), "bool"),
    "capacity_basis": (("flag", "capacity_basis"), "enum"),
    "gst_treatment": (("flag", "gst_treatment"), "enum"),
    "extra_charges_complete": (("flag", "extra_charges_complete"), "bool"),
    "net_cost_subsidy_basis": (("flag", "net_cost_subsidy_basis"), "enum"),
    "give_it_up": (("flag", "give_it_up"), "bool"),
}
ALIASES = {
    "system_capacity": "stated_capacity", "system_size": "stated_capacity", "capacity": "stated_capacity",
    "stated_kw": "stated_capacity", "kw": "stated_capacity", "kwp": "stated_capacity",
    "panels": "panel_count", "module_count": "panel_count", "no_of_panels": "panel_count",
    "number_of_panels": "panel_count", "panel_qty": "panel_count",
    "wattage": "panel_wattage", "module_wattage": "panel_wattage", "panel_wp": "panel_wattage",
    "panel_model": "module_make_model", "module_model": "module_make_model",
    "panel_make_model": "module_make_model", "module": "module_make_model", "panel_make": "module_make_model",
    "inverter": "inverter_make_model", "inverter_model": "inverter_make_model",
    "inverter_kw": "inverter_rating", "inverter_capacity": "inverter_rating",
    "dcr": "dcr_declaration", "registration": "vendor_registration", "vendor_reg": "vendor_registration",
    "base": "base_price", "price": "base_price", "system_price": "base_price",
    "gst": "gst_amount", "total": "gross_total", "grand_total": "gross_total", "total_price": "gross_total",
    "central_subsidy": "subsidy_central", "state_subsidy": "subsidy_state",
    "combined_subsidy": "subsidy_combined", "net": "net_cost", "net_price": "net_cost",
    "net_payable": "net_cost",
}

_ID = r"(?=[A-Za-z0-9_.-]*\d)(?=[A-Za-z0-9_.-]*[A-Za-z])[A-Za-z0-9_.-]+"
_HEADER = re.compile(
    rf"^(?:\[\s*(?P<a>{_ID})\s*\]|#{{1,6}}\s*(?P<b>{_ID})|=+\s*(?P<c>{_ID})\s*=+|-{{2,}}\s*(?P<d>{_ID})\s*-{{2,}}"
    rf"|doc(?:ument)?[\s_]*(?:id)?\s*[:=#-]?\s*(?P<e>{_ID})|(?P<f>{_ID})\s*:?)$", re.I)
_KEY = re.compile(r"^(?P<key>[^:=|]+?)\s*[:=]\s*(?P<rest>.*)$")


def canonical_key(key):
    k = re.sub(r"[\s\-./]+", "_", key.strip().lower()).strip("_")
    return ALIASES.get(k, k)


def parse_answer_key(text):
    """{"docs": {doc_id: {key: {"value": str|None, "pages": [int]}}}, "unknown": {doc_id: [key]},
    "orphan_lines": int, "duplicates": {doc_id: [key]}}."""
    docs, unknown, duplicates, orphans, current = {}, {}, {}, 0, None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        header = _HEADER.match(s)
        if header:
            current = next(v for v in header.groupdict().values() if v)
            docs.setdefault(current, {})
            continue
        if s.startswith("#"):
            continue
        m = _KEY.match(s)
        if not m:
            continue
        if current is None:
            orphans += 1
            continue
        key = canonical_key(m.group("key"))
        value, _, page = m.group("rest").rpartition("|") if "|" in m.group("rest") else (m.group("rest"), "", "")
        value = value.strip().strip("\"'").strip()
        entry = {"value": None if value.lower() in ABSENT else value,
                 "pages": [int(p) for p in re.findall(r"\d+", page)]}
        if key not in FIELDS:
            unknown.setdefault(current, []).append(m.group("key").strip())
            continue
        if key in docs[current]:
            duplicates.setdefault(current, []).append(key)
        docs[current][key] = entry
    return {"docs": docs, "unknown": unknown, "orphan_lines": orphans, "duplicates": duplicates}


# --- comparable keys ------------------------------------------------------------------------

def _dec(x):
    try:
        return Decimal(str(x)).normalize()
    except (InvalidOperation, ValueError):
        return None


def _norm_text(s):
    return re.sub(r"[^a-z0-9]", "", str(s).casefold())


_UNIT = {"kw": ("kw", 1), "kwp": ("kw", 1), "kva": ("kw", 1), "w": ("w", 1), "wp": ("w", 1)}


def _measure_key(parsed, unit, want):
    if parsed is None:
        return None
    unit = (unit or ("kW" if want == "kw" else "W")).lower()
    base, _ = _UNIT.get(unit, (None, None))
    if base is None:
        return None
    factor = Decimal(1)
    if base != want:
        factor = Decimal("0.001") if want == "kw" else Decimal(1000)
    if isinstance(parsed, dict):
        return ("range", _dec(Decimal(str(parsed["min"])) * factor), _dec(Decimal(str(parsed["max"])) * factor))
    return ("one", _dec(Decimal(str(parsed)) * factor))


def truth_key(kind, text):
    if kind == "amount":
        r = parse_amount(text)
        return ("amount", _dec(r["parsed"])) if r["parse_status"] == "ok" else ("text", _norm_text(text))
    if kind in ("kw", "w"):
        r = parse_capacity(text)
        if r["parse_status"] != "ok" and re.fullmatch(r"\s*\d+(\.\d+)?\s*", text):
            r = {"parsed": Decimal(text.strip()), "unit": None, "parse_status": "ok"}
        key = _measure_key(r["parsed"], r["unit"], kind) if r["parse_status"] == "ok" else None
        return key or ("text", _norm_text(text))
    if kind == "count":
        return ("count", int(text)) if text.strip().isdigit() else ("text", _norm_text(text))
    if kind == "bool":
        low = text.strip().lower()
        return ("bool", True) if low in YES else ("bool", False) if low in NO else ("text", _norm_text(text))
    if kind == "enum":
        return ("enum", re.sub(r"[\s-]+", "_", text.strip().lower()))
    return ("text", _norm_text(text))


def predicted_key(kind, value):
    if kind == "amount":
        if value.get("parse_status") == "ok":
            return ("amount", _dec(value.get("parsed")))
        return ("text", _norm_text(value.get("raw")))
    if kind in ("kw", "w"):
        key = (_measure_key(value.get("parsed"), value.get("unit"), kind)
               if value.get("parse_status") == "ok" else None)
        return key or ("text", _norm_text(value.get("raw")))
    if kind == "count":
        return ("count", value) if isinstance(value, int) else ("text", _norm_text(value))
    if kind == "bool":
        return ("bool", value) if isinstance(value, bool) else ("text", _norm_text(value))
    if kind == "enum":
        return ("enum", value)
    return ("text", _norm_text(value))


def _fields_for(quote, where):
    """The contract fields an answer-key entry is compared with."""
    if isinstance(where, str):
        return [quote.get(where)]
    if where[0] == "flag":
        return [((quote.get("flags") or {}).get("model_proposed") or {}).get(where[1])]
    _, list_name, key = where
    return [item.get(key) for item in quote.get(list_name) or []]


def score_field(quote, key, truth):
    where, kind = FIELDS[key]
    fields = [f for f in _fields_for(quote, where) if f is not None]
    conflict = any(f.get("conflict") for f in fields)
    present = [f for f in fields if not f.get("conflict") and f.get("value") is not None]
    truth_value = truth["value"]
    if truth_value is None:
        status = "falsely_populated" if present or conflict else "abstained"
        return {"status": status, "conflict": conflict, "page_ok": None}
    if conflict or not present:
        return {"status": "missing", "conflict": conflict, "page_ok": None}
    several = isinstance(where, tuple) and where[0] == "list"
    parts = [p for p in re.split(r"[+;]", truth_value) if p.strip()] if several else [truth_value]
    want = sorted(map(repr, (truth_key(kind, p) for p in parts)))
    got = sorted(map(repr, (predicted_key(kind, f["value"]) for f in present)))
    status = "correct" if want == got else "wrong"
    page_ok = None
    if status == "correct" and truth["pages"]:
        page_ok = bool({f.get("page") for f in present} & set(truth["pages"]))
    return {"status": status, "conflict": False, "page_ok": page_ok}


def score_document(quote, truth_fields):
    return {key: score_field(quote, key, truth) for key, truth in truth_fields.items()}


def summarise(doc_scores):
    """Counts over {doc: {key: score}} for one model."""
    counts = {s: 0 for s in STATUSES}
    pages_checked = pages_ok = conflicts = 0
    for scores in doc_scores.values():
        for s in scores.values():
            counts[s["status"]] += 1
            conflicts += s["conflict"]
            if s["page_ok"] is not None:
                pages_checked += 1
                pages_ok += s["page_ok"]
    stated = counts["correct"] + counts["wrong"] + counts["missing"]
    not_stated = counts["falsely_populated"] + counts["abstained"]
    return {
        **counts,
        "fields": sum(counts.values()),
        "accuracy": round(counts["correct"] / stated, 3) if stated else None,
        "false_population_rate": round(counts["falsely_populated"] / not_stated, 3) if not_stated else None,
        "pages_ok": pages_ok, "pages_checked": pages_checked, "conflicts": conflicts,
    }
