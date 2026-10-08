"""Apply a user's corrections and confirmations to a contract v1 quote.

user_inputs = {
    "corrections": {path: value},     # e.g. "base_price": "1,85,000",
                                      #      "module_groups[G1].wattage": "550 Wp"
    "confirmations": {name: value},   # e.g. "state": "Telangana", "gst_treatment": "excluded"
}

A corrected field keeps its page and evidence text, gets provenance
"user_corrected", and holds the original extracted field under "original".
The input quote is never changed. Applying the same inputs again gives the
same result.
"""

import copy
import re

from .parse import parse_amount, parse_capacity
from .common import jsonable

USER_CORRECTED = "user_corrected"

AMOUNT_PATHS = {"base_price", "gst_amount", "discount", "gross_total", "subsidy_central", "subsidy_state",
                "subsidy_combined", "subsidy_unspecified", "net_cost"}
CAPACITY_PATHS = {"stated_capacity"}
PLAIN_PATHS = {"dcr_declaration", "vendor_registration"}
ITEM_LISTS = {
    "module_groups": ("group_id", {"count": "plain", "wattage": "capacity", "make_model": "plain"}),
    "inverters": ("inverter_id", {"make_model": "plain", "rating": "capacity"}),
    "extra_charges": ("charge_id", {"label": "plain", "amount": "amount", "included_in_total": "plain"}),
}
CONFIRMABLE = {"selected_option", "state", "consumer_type", "portal_application_on_or_after_cutoff",
               "multiple_options", "capacity_basis", "gst_treatment", "extra_charges_complete",
               "net_cost_subsidy_basis"}
_ITEM_PATH = re.compile(r"^(\w+)\[([^\]]+)\]\.(\w+)$")


def _parsed_value(kind, value):
    if kind == "plain":
        return value
    raw = None if value is None else str(value)
    if kind == "amount":
        r = parse_amount(raw)
        out = {"raw": raw, "parsed": r["parsed"], "parse_status": r["parse_status"]}
    else:
        r = parse_capacity(raw)
        out = {"raw": raw, "parsed": r["parsed"], "unit": r["unit"], "parse_status": r["parse_status"]}
    return jsonable(out)


def _locate(quote, path):
    """(container, key, kind) for a correction path; raises KeyError if unknown."""
    if path in AMOUNT_PATHS:
        return quote, path, "amount"
    if path in CAPACITY_PATHS:
        return quote, path, "capacity"
    if path in PLAIN_PATHS:
        return quote, path, "plain"
    m = _ITEM_PATH.match(path)
    if m and m.group(1) in ITEM_LISTS:
        list_name, item_id, key = m.groups()
        id_key, kinds = ITEM_LISTS[list_name]
        if key in kinds:
            for item in quote.get(list_name) or []:
                if item.get(id_key) == item_id:
                    return item, key, kinds[key]
    raise KeyError(f"unknown correction path: {path}")


def _corrected(old, value):
    original = old.get("original") if old and old.get("provenance") == USER_CORRECTED else old
    original = copy.deepcopy(original)
    return {
        "value": value,
        "evidence_text": (original or {}).get("evidence_text"),
        "page": (original or {}).get("page"),
        "batch": (original or {}).get("batch"),
        "provenance": USER_CORRECTED,
        "original": original,
    }


def apply_user_inputs(quote, user_inputs=None):
    """Return (effective_quote, corrected_fields). corrected_fields is sorted by path."""
    q = copy.deepcopy(quote)
    user_inputs = user_inputs or {}
    corrected = []
    for path in sorted((user_inputs.get("corrections") or {})):
        container, key, kind = _locate(q, path)
        value = _parsed_value(kind, user_inputs["corrections"][path])
        container[key] = _corrected(container.get(key), value)
        original = container[key]["original"]
        corrected.append({
            "path": path,
            "original_value": None if original is None else original.get("value"),
            "corrected_value": value,
        })
    confirmations = user_inputs.get("confirmations") or {}
    unknown = sorted(set(confirmations) - CONFIRMABLE)
    if unknown:
        raise KeyError(f"unknown confirmation: {', '.join(unknown)}")
    if confirmations:
        flags = q.setdefault("flags", {})
        flags.setdefault("model_proposed", {})
        flags.setdefault("user_confirmed", {}).update(confirmations)
    return q, corrected
