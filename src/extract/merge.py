"""Merge per-batch extractions into one contract v1 quote.

Every candidate is kept with its page and batch. When batches agree, the field
holds that value; when they disagree, the field is marked as a conflict that
the checks treat as needing confirmation, and it is listed under
flags.needs_confirmation. Totals are never summed across batches and no batch
wins by coming last.
"""

from decimal import Decimal, InvalidOperation

from checks.contract import CONTRACT_VERSION

from .wire_schema import FACT_FIELDS, FLAG_FIELDS, PLAIN_FIELDS as WIRE_PLAIN_FIELDS

# vendor_gstin is evidence only (Textract keeps a GSTIN it found where it asked for the
# registration number); no check reads it.
PLAIN_FIELDS = (*WIRE_PLAIN_FIELDS, "dcr_declaration", "vendor_gstin")
# kW, kWp, W and Wp compare as one dimension; kVA only ever with kVA.
DIMENSIONS = {"kw": ("kw", Decimal(1)), "kwp": ("kw", Decimal(1)), "w": ("kw", Decimal("0.001")),
              "wp": ("kw", Decimal("0.001")), "kva": ("kva", Decimal(1))}


def _dec(text):
    try:
        return Decimal(str(text)).normalize()
    except (InvalidOperation, ValueError):
        return None


def _key(kind, value):
    """What two candidate values must share to count as the same value."""
    if kind == "amount":
        if value.get("parse_status") == "ok":
            return ("amount", _dec(value.get("parsed")))
        return ("raw", " ".join(str(value.get("raw")).casefold().split()))
    if kind == "capacity":
        dimension, factor = DIMENSIONS.get(str(value.get("unit")).lower(), (None, None))
        parsed = value.get("parsed")
        if value.get("parse_status") == "ok" and factor is not None:
            if isinstance(parsed, dict):
                return (dimension, _dec(Decimal(parsed["min"]) * factor), _dec(Decimal(parsed["max"]) * factor))
            return (dimension, _dec(Decimal(parsed) * factor))
        return ("raw", " ".join(str(value.get("raw")).casefold().split()))
    if isinstance(value, str):
        return ("text", " ".join(value.casefold().split()))
    return ("value", value)


def _conflict_value(kind):
    if kind == "amount":
        return {"raw": None, "parsed": None, "parse_status": "conflict"}
    if kind == "capacity":
        return {"raw": None, "parsed": None, "unit": None, "parse_status": "conflict"}
    return None


def _candidate(f):
    out = {k: f.get(k) for k in ("value", "evidence_text", "page", "batch")}
    if f.get("source"):  # e.g. a state read from the GSTIN, labelled as the GST registration state
        out["source"] = f["source"]
    return out


class _Merger:
    def __init__(self):
        self.conflicts = []

    def field(self, path, kind, fields):
        # A field a page's own sources already disagreed on stays a conflict.
        flagged = [c for f in fields if f is not None and f.get("conflict") for c in f.get("candidates") or []]
        fields = [f for f in fields if f is not None and f.get("value") is not None and not f.get("conflict")]
        if flagged:
            candidates = flagged + [_candidate(f) for f in fields]
            self.conflicts.append({"field": path, "reason": "sources disagree", "candidates": candidates})
            return {"value": _conflict_value(kind), "evidence_text": None, "page": None, "batch": None,
                    "conflict": True, "candidates": candidates}
        if not fields:
            return None
        candidates = [_candidate(f) for f in fields]
        if len({_key(kind, f["value"]) for f in fields}) == 1:
            return {**_candidate(fields[0]), "candidates": candidates}
        self.conflicts.append({"field": path, "reason": "batches disagree", "candidates": candidates})
        return {"value": _conflict_value(kind), "evidence_text": None, "page": None, "batch": None,
                "conflict": True, "candidates": candidates}

    def alternatives(self, lists):
        seen, out = set(), []
        for f in (f for fs in lists for f in fs):
            key = _key("text", f["value"])
            if key not in seen:
                seen.add(key)
                out.append(f)
        return out


ITEM_KINDS = {
    "module_groups": {"count": "plain", "wattage": "capacity", "make_model": "plain"},
    "inverters": {"make_model": "plain", "rating": "capacity"},
    "extra_charges": {"label": "plain", "amount": "amount", "included_in_total": "plain"},
}


# The fields that make two items from different batches the same item.
ITEM_TUPLES = {
    "module_groups": (("count", "plain"), ("wattage", "capacity"), ("make_model", "plain")),
    "inverters": (("make_model", "plain"), ("rating", "capacity")),
}


def _item_tuple(list_name, item):
    return tuple(None if item.get(key) is None else _key(kind, item[key]["value"])
                 for key, kind in ITEM_TUPLES[list_name])


def _label_key(item):
    f = item.get("label")
    return _key("text", f["value"]) if f else None


def _charge_key(item):
    """Label and parsed amount: what makes two charges from different batches the same charge."""
    f = item.get("amount")
    return repr((_label_key(item), _key("amount", f["value"]) if f else None))


def _merge_items(m, list_name, path, items):
    """items from several batches (same list, same option), merged field by field."""
    fields = ITEM_KINDS[list_name]
    merged = {k: v for k, v in items[0].items() if k not in fields and k != "make_model_alternatives"}
    for key, kind in fields.items():
        merged[key] = m.field(f"{path}.{key}", kind, [i.get(key) for i in items])
    if list_name != "extra_charges":
        merged["make_model_alternatives"] = m.alternatives([i.get("make_model_alternatives") or [] for i in items])
        return merged
    labels = [{"value": i["total_label"], "evidence_text": i["label"].get("evidence_text"),
               "page": i["label"].get("page"), "batch": i["label"].get("batch")}
              for i in items if i.get("total_label")]
    total = m.field(f"{path}.total_label", "plain", labels)
    merged["total_label"] = None if total is None or total.get("conflict") else total["value"]
    if total is not None and total.get("conflict"):
        # included_in_total means nothing until the user says which total it is.
        inc = merged.get("included_in_total") or {}
        merged["included_in_total"] = {"value": None, "evidence_text": None, "page": None, "batch": None,
                                       "conflict": True, "candidates": inc.get("candidates", [])}
    return merged


def _conflicting_item(item, list_name):
    """A flagged candidate item: every value, read or not, needs confirmation; what
    the batch read is kept under candidates."""
    out = dict(item, conflict=True)
    for key, kind in ITEM_KINDS[list_name].items():
        f = item.get(key)
        out[key] = {"value": _conflict_value(kind), "evidence_text": None, "page": None, "batch": None,
                    "conflict": True, "candidates": [] if f is None else [_candidate(f)]}
    return out


def _match_tuples(m, list_name, path, batches):
    """Panel lines or inverters of one option reported by several batches.

    Assumption: the same tuple (count, wattage and normalised make and model for
    panels; make and model and rating for inverters) in every reporting batch, the
    same number of times, is one item repeated on another page. It is kept once with
    every piece of evidence. Anything else is never combined: each version is kept
    as its own flagged candidate.
    """
    counts = {}
    for b, items in batches.items():
        for item in items:
            counts.setdefault(repr(_item_tuple(list_name, item)), {}).setdefault(b, []).append(item)
    out, flagged = [], []
    for per_batch in counts.values():
        sizes = {len(per_batch.get(b, [])) for b in batches}
        if len(sizes) == 1:
            for same in zip(*per_batch.values()):
                out.append(_merge_items(m, list_name, path, list(same)))
        else:
            for b, items in per_batch.items():
                flagged += [(b, item) for item in items]
    if flagged:
        m.conflicts.append({"field": path, "reason": "batches list different items",
                            "candidates": [{"batch": b, "page": _item_page(item)} for b, item in flagged]})
        out += [_conflicting_item(item, list_name) for _, item in flagged]
    return out


def _match_charges(m, path, batches):
    """Charges of one option reported by several batches. The same label and parsed
    amount, the same number of times in every batch that lists that label, is one
    charge with all its evidence. The same label with another amount, or a different
    number of times, is flagged. A label only one batch lists is kept as listed."""
    occurrences = {}
    labels = {}
    for b, items in batches.items():
        for item in items:
            occurrences.setdefault(_charge_key(item), {}).setdefault(b, []).append(item)
            labels.setdefault(repr(_label_key(item)), set()).add(_charge_key(item))
    out, flagged = [], []
    for key, per_batch in occurrences.items():
        sample = next(iter(per_batch.values()))[0]
        same_label = labels[repr(_label_key(sample))]
        listing = [b for b in batches if any(k in occurrences and b in occurrences[k] for k in same_label)]
        sizes = {len(per_batch.get(b, [])) for b in listing}
        if len(sizes) == 1:
            out += [_merge_items(m, "extra_charges", path, list(same)) for same in zip(*per_batch.values())]
        else:
            flagged += [(b, item) for b, items in per_batch.items() for item in items]
    if flagged:
        m.conflicts.append({"field": path, "reason": "batches give different amounts for one charge",
                            "candidates": [{"batch": b, "page": _item_page(item)} for b, item in flagged]})
        for _, item in flagged:
            f = item.get("amount")
            out.append(dict(item, conflict=True, amount={
                "value": _conflict_value("amount"), "evidence_text": None, "page": None, "batch": None,
                "conflict": True, "candidates": [] if f is None else [_candidate(f)]}))
    return out


def _item_page(item):
    return next((f["page"] for f in item.values() if isinstance(f, dict) and "page" in f), None)


def _merge_list(m, list_name, per_batch):
    """per_batch: [(batch, [items])]. Items of one option seen in several batches are
    matched, never added together. Items from one batch are kept as listed."""
    by_option = {}
    for batch, items in per_batch:
        for item in items:
            by_option.setdefault(item.get("option_id"), {}).setdefault(batch, []).append(item)
    out = []
    for option, batches in by_option.items():
        path = f"{list_name}[option={option}]"
        groups = list(batches.values())
        if len(groups) == 1:
            out += groups[0]
            continue
        if list_name == "extra_charges":
            out += _match_charges(m, path, batches)
            continue
        out += _match_tuples(m, list_name, path, batches)
    return out


def merge_batches(records, failures=(), skipped_pages=()):
    """records: successful batch records (with "contract"); failures: [{"batch", "pages", "kind", "code"}].
    Returns a contract v1 quote with processing_complete false if any batch failed or a page was skipped."""
    m = _Merger()
    records = sorted(records, key=lambda r: r["batch"])
    parts = [(r["batch"], r["contract"]) for r in records]
    quote = {"contract_version": CONTRACT_VERSION}
    pages_done = sorted(p for r in records for p in r["pages"])
    skipped = sorted(set(skipped_pages) | {p for f in failures for p in f.get("pages", [])})
    quote["processing_complete"] = not failures and not skipped
    quote["pages_processed"] = pages_done
    quote["pages_skipped"] = skipped
    quote["batches"] = (
        [{"batch": r["batch"], "pages": r["pages"], "status": "ok"} for r in records]
        + [{"batch": f["batch"], "pages": f.get("pages", []), "status": "failed", "kind": f.get("kind"),
            "code": f.get("code")} for f in failures]
    )
    quote["batches"].sort(key=lambda b: b["batch"])

    options = {}
    for _, c in parts:
        for o in c.get("options") or []:
            options.setdefault(o["option_id"], []).append(o.get("label"))
    quote["options"] = [{"option_id": oid, "label": m.field(f"options[{oid}].label", "plain", labels)}
                        for oid, labels in options.items()]

    ids = {"module_groups": ("group_id", "G"), "inverters": ("inverter_id", "I"), "extra_charges": ("charge_id", "E")}
    for list_name, (id_key, prefix) in ids.items():
        items = _merge_list(m, list_name, [(b, c.get(list_name) or []) for b, c in parts])
        for n, item in enumerate(items, 1):
            item[id_key] = f"{prefix}{n}"
        quote[list_name] = items

    for name in PLAIN_FIELDS:
        quote[name] = m.field(name, "plain", [c.get(name) for _, c in parts])

    # Prices, subsidies and stated capacities: one fact per option. Facts for All
    # are top-level fields; an option's own facts go under option_fields.
    facts = {}
    for _, c in parts:
        for fact in c.get("facts") or []:
            facts.setdefault((fact["option_id"], fact["name"]), []).append(fact["field"])
    quote.update({name: None for name in FACT_FIELDS})
    quote["option_fields"] = {}
    for (oid, name), fields in facts.items():
        kind = "capacity" if name == "stated_capacity" else "amount"
        merged = m.field(name if oid is None else f"option_fields[{oid}].{name}", kind, fields)
        if oid is None:
            quote[name] = merged
        else:
            quote["option_fields"].setdefault(oid, {})[name] = merged

    proposed = {name: m.field(f"flags.{name}", "plain",
                              [(c.get("flags") or {}).get("model_proposed", {}).get(name) for _, c in parts])
                for name in FLAG_FIELDS}
    quote["flags"] = {"model_proposed": proposed, "user_confirmed": {}, "needs_confirmation": m.conflicts}
    return quote
