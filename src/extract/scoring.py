"""Answer key parsing and per-field scoring for the extraction spike.

Answer key format (plain text, written by hand):

    [Q12]
    capacity_stated: 3.3 kWp | 1
    total_price: Rs. 1,97,000 | 2
    gst: extra | 2
    subsidy: Rs. 78,000 | 2
    subsidy_type: central
    panel_count: 6 + 4 | 1

A block starts with a line holding only the document id, written as [Q12],
## Q12, Q12: or Q12 (the id must contain a letter and a digit). Each key line is
"key: value | page"; the page is optional. "not stated" (or null, none, -,
n/a, not found, or an empty value) means the correct answer is null. For
several panel or inverter lines, separate the values with + or ;. Lines
starting with # or // are comments.

Matching: amounts through parse_amount, capacities through parse_capacity (kVA
only ever matches kVA), text ignoring case and whitespace.

Each field is scored as correct, wrong, missing (the key has a value, the
extraction has none), falsely_populated (the key says not stated, the
extraction has a value) or abstained (both empty). A field the merge marked as
a conflict counts as missing or falsely_populated, and is noted. For a quote
with several options, every option's value is compared.
"""

import re
from decimal import Decimal, InvalidOperation

from checks.parse import parse_amount, parse_capacity

from .wire_schema import SUBSIDY_KINDS

STATUSES = ("correct", "wrong", "missing", "falsely_populated", "abstained")
ABSENT = {"", "null", "none", "-", "--", "n/a", "na", "nil", "not found", "not stated", "absent", "not given"}
YES = {"yes", "y", "true", "dcr", "mentioned", "stated", "more than 1", "more than one", ">1"}
NO = {"no", "n", "false", "non-dcr", "non_dcr", "not dcr", "1", "one", "single"}

# Scored fields: key -> (where, kind). where is a top-level field, ("list", list, field),
# ("flag", name) or a special scorer name.
FIELDS = {
    "stated_capacity": ("stated_capacity", "kw"),
    "capacity_basis": (("flag", "capacity_basis"), "enum"),
    "panel_count": (("list", "module_groups", "count"), "count"),
    "panel_wattage": (("list", "module_groups", "wattage"), "w"),
    "module_make_model": (("list", "module_groups", "make_model"), "text"),
    "inverter_rating": (("list", "inverters", "rating"), "kw"),
    "inverter_make_model": (("list", "inverters", "make_model"), "text"),
    "quote_date": ("quote_date", "text"),
    "vendor_name": ("vendor_name", "text"),
    "vendor_state": ("vendor_state", "text"),
    "vendor_registration": ("vendor_registration", "text"),
    "base_price": ("base_price", "amount"),
    "gst_amount": ("gst_amount", "amount"),
    "discount": ("discount", "amount"),
    "gross_total": ("gross_total", "amount"),
    "net_cost": ("net_cost", "amount"),
    **{name: (name, "amount") for name in SUBSIDY_KINDS.values()},
    "subsidy": ("subsidy", "special"),
    "subsidy_type": ("subsidy_type", "special"),
    "gst_treatment": (("flag", "gst_treatment"), "enum"),
    "extra_charges_outside_total": ("extra_charges_outside_total", "special"),
    "multiple_options": (("flag", "multiple_options"), "bool"),
    "dcr_declaration": ("dcr_declaration", "bool"),
    "dcr_text": ("dcr_text", "special"),
    "net_cost_subsidy_basis": (("flag", "net_cost_subsidy_basis"), "enum"),
    "extra_charges_complete": (("flag", "extra_charges_complete"), "bool"),
    "give_it_up": (("flag", "give_it_up"), "bool"),
}
# The hand-written key's names -> scored fields.
ALIASES = {
    "quote_date": "quote_date",
    "vendor_name": "vendor_name",
    "vendor_state": "vendor_state",
    "options": "multiple_options",
    "capacity_stated": "stated_capacity",
    "capacity_basis": "capacity_basis",
    "panel_count": "panel_count",
    "panel_wattage_w": "panel_wattage",
    "panel_make": "module_make_model",
    "inverter_rating": "inverter_rating",
    "inverter_make": "inverter_make_model",
    "total_price": "gross_total",
    "gst": "gst_treatment",
    "extra_charges_outside_total": "extra_charges_outside_total",
    "subsidy": "subsidy",
    "subsidy_type": "subsidy_type",
    "net_cost_stated": "net_cost",
    "dcr_text": "dcr_text",
}
# Kept with the key but not scored.
KEPT = ("pages_total", "contradictions")
# Key values -> contract enum values.
ENUM_VALUES = {
    "capacity_basis": {"dc": "dc_kwp", "dc_kwp": "dc_kwp", "kwp": "dc_kwp", "dc_kw": "dc_kwp",
                       "ac": "ac_kw", "ac_kw": "ac_kw", "inverter": "ac_kw",
                       "unclear": "unclear", "unspecified": "unclear", "not_clear": "unclear", "kva": "unclear"},
    "gst_treatment": {"included": "included", "inclusive": "included", "extra": "excluded",
                      "excluded": "excluded", "exclusive": "excluded", "unclear": "unclear"},
}
# Predicted values that mean the quote does not say; they count as empty when the key is null.
NOT_SAID = {"capacity_basis": {"unspecified", "unclear"}, "gst_treatment": {"unclear"},
            "net_cost_subsidy_basis": {"unspecified"}, "subsidy_type": {"unspecified"}}
PREDICTED_ENUM = {"capacity_basis": {"unspecified": "unclear"}}

_ID = r"(?=[A-Za-z0-9_.-]*\d)(?=[A-Za-z0-9_.-]*[A-Za-z])[A-Za-z0-9_.-]+"
_HEADER = re.compile(
    rf"^(?:\[\s*(?P<a>{_ID})\s*\]|#{{1,6}}\s*(?P<b>{_ID})|=+\s*(?P<c>{_ID})\s*=+|-{{2,}}\s*(?P<d>{_ID})\s*-{{2,}}"
    rf"|doc(?:ument)?[\s_]*(?:id)?\s*[:=#-]?\s*(?P<e>{_ID})|(?P<f>{_ID})\s*:?)$", re.I)
_KEY = re.compile(r"^(?P<key>[^:=|]+?)\s*[:=]\s*(?P<rest>.*)$")


def key_name(key):
    return re.sub(r"[\s\-./]+", "_", key.strip().lower()).strip("_")


def canonical_key(key):
    k = key_name(key)
    return ALIASES.get(k, k)


def parse_answer_key(text):
    """{"docs": {doc: {field: {"value", "pages"}}}, "kept": {doc: {key: {...}}},
    "unknown": {doc: [key]}, "orphan_lines": int, "duplicates": {doc: [key]}}."""
    docs, kept, unknown, duplicates, orphans, current = {}, {}, {}, {}, 0, None
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
        rest = m.group("rest")
        value, _, page = rest.rpartition("|") if "|" in rest else (rest, "", "")
        value = value.strip().strip("\"'").strip()
        entry = {"value": None if value.lower() in ABSENT else value,
                 "pages": [int(p) for p in re.findall(r"\d+", page)], "key": m.group("key").strip()}
        name = key_name(m.group("key"))
        if name in KEPT:
            kept.setdefault(current, {})[name] = entry
            continue
        key = canonical_key(m.group("key"))
        if key not in FIELDS:
            unknown.setdefault(current, []).append(m.group("key").strip())
            continue
        if key in docs[current]:
            duplicates.setdefault(current, []).append(key)
        docs[current][key] = entry
    return {"docs": docs, "kept": kept, "unknown": unknown, "orphan_lines": orphans, "duplicates": duplicates}


# --- comparable keys ------------------------------------------------------------------------

def _dec(x):
    try:
        return Decimal(str(x)).normalize()
    except (InvalidOperation, ValueError):
        return None


def norm_text(s):
    """Case- and whitespace-insensitive."""
    return re.sub(r"\s+", "", str(s).casefold())


# kW, kWp, W and Wp compare as one dimension (in kW); kVA only ever with kVA.
_DIMENSIONS = {"kw": ("kw", Decimal(1)), "kwp": ("kw", Decimal(1)), "w": ("kw", Decimal("0.001")),
               "wp": ("kw", Decimal("0.001")), "kva": ("kva", Decimal(1))}
_MEASURE = re.compile(r"\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?\s*(?:kwp|kva|kw|wp|w)\b", re.I)
_AMOUNT = re.compile(r"(?:rs\.?|₹|inr)?\s*\d[\d,]*(?:\.\d+)?(?:\s*/-)?", re.I)


def _measure_key(parsed, unit, want):
    """want ("kw" or "w") is the unit assumed when none is written."""
    if parsed is None:
        return None
    unit = (unit or ("kW" if want == "kw" else "W")).lower()
    dimension, factor = _DIMENSIONS.get(unit, (None, None))
    if dimension is None:
        return None
    if isinstance(parsed, dict):
        return (dimension, "range", _dec(Decimal(str(parsed["min"])) * factor),
                _dec(Decimal(str(parsed["max"])) * factor))
    return (dimension, "one", _dec(Decimal(str(parsed)) * factor))


def _parse_measure(text, want):
    """parse_capacity on the text, on the text with the default unit added when it has
    none, or on the first number-and-unit inside it."""
    r = parse_capacity(text)
    if r["parse_status"] != "ok" and not re.search(r"[a-z]", text, re.I):
        r = parse_capacity(f"{text.strip()} {'kW' if want == 'kw' else 'W'}")
    if r["parse_status"] != "ok" and (m := _MEASURE.search(text)):
        r = parse_capacity(m.group(0))
    return r


def _amount_key_of_text(text):
    r = parse_amount(text)
    if r["parse_status"] != "ok":
        found = [m.group(0) for m in _AMOUNT.finditer(text) if re.search(r"\d", m.group(0))]
        if found:
            r = parse_amount(found[-1].strip())
    return ("amount", _dec(r["parsed"])) if r["parse_status"] == "ok" else None


def truth_key(kind, text, field=None):
    if kind == "amount":
        return _amount_key_of_text(text) or ("text", norm_text(text))
    if kind in ("kw", "w"):
        r = _parse_measure(text, kind)
        key = _measure_key(r["parsed"], r["unit"], kind) if r["parse_status"] == "ok" else None
        return key or ("text", norm_text(text))
    if kind == "count":
        return ("count", int(text)) if text.strip().isdigit() else ("text", norm_text(text))
    if kind == "bool":
        low = " ".join(text.strip().lower().split())
        if low in YES or (low.isdigit() and int(low) > 1):
            return ("bool", True)
        return ("bool", False) if low in NO else ("text", norm_text(text))
    if kind == "enum":
        low = re.sub(r"[\s-]+", "_", text.strip().lower())
        return ("enum", ENUM_VALUES.get(field, {}).get(low, low))
    return ("text", norm_text(text))


def predicted_key(kind, value, field=None):
    if kind == "amount":
        if value.get("parse_status") == "ok":
            return ("amount", _dec(value.get("parsed")))
        return ("text", norm_text(value.get("raw")))
    if kind in ("kw", "w"):
        key = (_measure_key(value.get("parsed"), value.get("unit"), kind)
               if value.get("parse_status") == "ok" else None)
        return key or ("text", norm_text(value.get("raw")))
    if kind == "count":
        return ("count", value) if isinstance(value, int) else ("text", norm_text(value))
    if kind == "bool":
        return ("bool", value) if isinstance(value, bool) else ("text", norm_text(value))
    if kind == "enum":
        return ("enum", PREDICTED_ENUM.get(field, {}).get(value, value))
    return ("text", norm_text(value))


# --- what the extraction said ------------------------------------------------------------

def _scalar(quote, name):
    """A top-level field plus the same field for every option."""
    fields = [quote.get(name)]
    fields += [of[name] for of in (quote.get("option_fields") or {}).values() if name in of]
    return fields


def _fields_for(quote, where):
    if isinstance(where, str):
        return _scalar(quote, where)
    if where[0] == "flag":
        return [((quote.get("flags") or {}).get("model_proposed") or {}).get(where[1])]
    _, list_name, key = where
    return [item.get(key) for item in quote.get(list_name) or []]


def _subsidies(quote):
    """[(type, field)] for every subsidy the extraction gives."""
    return [(kind, f) for kind, name in SUBSIDY_KINDS.items() for f in _scalar(quote, name) if f is not None]


def _result(status, conflict=False, page_ok=None, notes=None):
    out = {"status": status, "conflict": conflict, "page_ok": page_ok}
    if notes:
        out["notes"] = notes
    return out


def _compare(truth, fields, want, got_key, field=None):
    """Score a list of predicted fields against the truth keys in want."""
    conflict = any(f.get("conflict") for f in fields)
    present = [f for f in fields if not f.get("conflict") and f.get("value") is not None]
    if truth["value"] is None:
        said = [f for f in present if f["value"] not in NOT_SAID.get(field, ())]
        return _result("falsely_populated" if said or conflict else "abstained", conflict)
    if conflict or not present:
        return _result("missing", conflict)
    got = sorted(map(repr, (got_key(f) for f in present)))
    status = "correct" if sorted(map(repr, want)) == got else "wrong"
    page_ok = None
    if status == "correct" and truth["pages"]:
        page_ok = bool({f.get("page") for f in present} & set(truth["pages"]))
    return _result(status, conflict, page_ok)


def _split(text):
    return [p.strip() for p in re.split(r"[+;]", text) if p.strip()]


def score_field(quote, key, truth, doc_truth=None):
    where, kind = FIELDS[key]
    if kind == "special":
        return _SPECIAL[key](quote, truth, doc_truth or {})
    fields = [f for f in _fields_for(quote, where) if f is not None]
    field = where[1] if isinstance(where, tuple) and where[0] == "flag" else where
    notes = None
    if truth["value"] is None:
        parts = []
    elif key == "stated_capacity":
        first, *rest = [p.strip() for p in re.split(r"[;,\n]", truth["value"]) if p.strip()]
        parts, notes = [first], rest
    elif isinstance(where, tuple) and where[0] == "list":
        parts = _split(truth["value"])
    else:
        parts = [truth["value"]]
    want = [truth_key(kind, p, field) for p in parts]
    result = _compare(truth, fields, want, lambda f: predicted_key(kind, f["value"], field), field)
    if notes:
        result["notes"] = notes
    return result


def _score_subsidy(quote, truth, doc_truth):
    kind_truth = (doc_truth.get("subsidy_type") or {}).get("value")
    subsidy_type = truth_key("enum", kind_truth, "subsidy_type")[1] if kind_truth else None
    fields = [f for kind, f in _subsidies(quote) if subsidy_type in (None, kind)]
    want = [] if truth["value"] is None else [truth_key("amount", truth["value"])]
    return _compare(truth, fields, want, lambda f: predicted_key("amount", f["value"]))


def _score_subsidy_type(quote, truth, doc_truth):
    pairs = _subsidies(quote)
    fields = [dict(f, value=kind) for kind, f in pairs]
    want = [] if truth["value"] is None else [("enum", truth_key("enum", truth["value"], "subsidy_type")[1])]
    return _compare(truth, fields, want, lambda f: ("enum", f["value"]), "subsidy_type")


def _score_outside(quote, truth, doc_truth):
    charges = [c for c in quote.get("extra_charges") or []
               if (c.get("included_in_total") or {}).get("value") == "no" or c.get("conflict")]
    fields = []
    for c in charges:
        amount = c.get("amount")
        conflict = bool(c.get("conflict") or (amount or {}).get("conflict"))
        fields.append({"value": c, "conflict": conflict, "page": (c.get("label") or {}).get("page")})

    def got(f):
        amount = f["value"].get("amount")
        if amount and amount["value"].get("parse_status") == "ok":
            return ("amount", _dec(amount["value"]["parsed"]))
        return ("text", norm_text((f["value"].get("label") or {}).get("value")))
    want = [] if truth["value"] is None else [
        _amount_key_of_text(p) or ("text", norm_text(p)) for p in _split(truth["value"])]
    return _compare(truth, fields, want, got)


def _score_dcr_text(quote, truth, doc_truth):
    f = quote.get("dcr_declaration")
    fields = [] if f is None else [dict(f, value=f.get("evidence_text") or "")]
    if truth["value"] is None or not fields or f.get("conflict"):
        return _compare(truth, fields, [], lambda x: None)
    want, said = norm_text(truth["value"]), norm_text(fields[0]["value"])
    status = "correct" if want and said and (want in said or said in want) else "wrong"
    page_ok = None
    if status == "correct" and truth["pages"]:
        page_ok = f.get("page") in truth["pages"]
    return _result(status, False, page_ok)


_SPECIAL = {"subsidy": _score_subsidy, "subsidy_type": _score_subsidy_type,
            "extra_charges_outside_total": _score_outside, "dcr_text": _score_dcr_text}


def score_document(quote, truth_fields):
    return {key: score_field(quote, key, truth, truth_fields) for key, truth in truth_fields.items()}


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
