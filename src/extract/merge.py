"""Merge per-batch extractions into one contract v1 quote.

Every candidate is kept with its page and batch. When batches agree, the field
holds that value; when they disagree, the field is marked as a conflict that
the checks treat as needing confirmation, and it is listed under
flags.needs_confirmation. Totals are never summed across batches and no batch
wins by coming last.
"""

from decimal import Decimal, InvalidOperation

from checks.contract import CONTRACT_VERSION

from .wire_schema import AMOUNT_FIELDS, FLAG_FIELDS

CAPACITY_FIELDS = ("stated_capacity",)
PLAIN_FIELDS = ("vendor_registration", "dcr_declaration")
TO_KW = {"kw": Decimal(1), "kwp": Decimal(1), "kva": Decimal(1), "w": Decimal("0.001"), "wp": Decimal("0.001")}


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
        factor = TO_KW.get(str(value.get("unit")).lower())
        parsed = value.get("parsed")
        if value.get("parse_status") == "ok" and factor is not None:
            if isinstance(parsed, dict):
                return ("range", _dec(Decimal(parsed["min"]) * factor), _dec(Decimal(parsed["max"]) * factor))
            return ("kw", _dec(Decimal(parsed) * factor))
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
    return {k: f.get(k) for k in ("value", "evidence_text", "page", "batch")}


class _Merger:
    def __init__(self):
        self.conflicts = []

    def field(self, path, kind, fields):
        fields = [f for f in fields if f is not None and f.get("value") is not None]
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


def _item_key(list_name, item):
    if list_name == "module_groups":
        f = item.get("wattage")
        return _key("capacity", f["value"]) if f else None
    if list_name == "inverters":
        f = item.get("make_model") or item.get("rating")
        return _key("text" if item.get("make_model") else "capacity", f["value"]) if f else None
    f = item.get("label")
    return _key("text", f["value"]) if f else None


def _merge_items(m, list_name, path, items):
    """items from several batches (same list, same option), merged field by field."""
    fields = ITEM_KINDS[list_name]
    merged = {k: v for k, v in items[0].items() if k not in fields and k != "make_model_alternatives"}
    for key, kind in fields.items():
        merged[key] = m.field(f"{path}.{key}", kind, [i.get(key) for i in items])
    if list_name != "extra_charges":
        merged["make_model_alternatives"] = m.alternatives([i.get("make_model_alternatives") or [] for i in items])
    elif merged.get("total_label") is None:
        merged["total_label"] = next((i["total_label"] for i in items if i.get("total_label")), None)
    return merged


def _conflicting_item(item, list_name):
    out = dict(item)
    for key, kind in ITEM_KINDS[list_name].items():
        f = item.get(key)
        if f is not None:
            out[key] = {"value": _conflict_value(kind), "evidence_text": None, "page": None, "batch": None,
                        "conflict": True, "candidates": [_candidate(f)]}
    return out


def _merge_list(m, list_name, per_batch):
    """per_batch: [(batch, [items])]. Items of one option seen in several batches are
    matched, never added together."""
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
            by_label = {}
            for items in groups:
                for item in items:
                    by_label.setdefault(_item_key(list_name, item), []).append(item)
            out += [_merge_items(m, list_name, path, same) for same in by_label.values()]
            continue
        if all(len(items) == 1 for items in groups):
            out.append(_merge_items(m, list_name, path, [items[0] for items in groups]))
            continue
        keysets = [sorted(map(repr, (_item_key(list_name, i) for i in items))) for items in groups]
        unique = all(len(set(ks)) == len(ks) for ks in keysets)
        if unique and all(ks == keysets[0] for ks in keysets):
            matched = {}
            for items in groups:
                for item in items:
                    matched.setdefault(repr(_item_key(list_name, item)), []).append(item)
            out += [_merge_items(m, list_name, path, same) for same in matched.values()]
            continue
        m.conflicts.append({"field": path, "reason": "batches list different items",
                            "candidates": [{"batch": b, "items": len(items)} for b, items in batches.items()]})
        out += [_conflicting_item(i, list_name) for items in groups for i in items]
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

    for name in (*CAPACITY_FIELDS, *PLAIN_FIELDS, *AMOUNT_FIELDS):
        kind = "amount" if name in AMOUNT_FIELDS else "capacity" if name in CAPACITY_FIELDS else "plain"
        quote[name] = m.field(name, kind, [c.get(name) for _, c in parts])

    proposed = {name: m.field(f"flags.{name}", "plain",
                              [(c.get("flags") or {}).get("model_proposed", {}).get(name) for _, c in parts])
                for name in FLAG_FIELDS}
    quote["flags"] = {"model_proposed": proposed, "user_confirmed": {}, "needs_confirmation": m.conflicts}
    return quote
