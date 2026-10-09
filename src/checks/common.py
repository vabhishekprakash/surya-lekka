"""Finding model and field helpers shared by the deterministic checks.

Checks read the internal view (checks.contract.normalise). Fields are nullable
wrappers: {"value": ..., "evidence_text": ..., "page": ...}, optionally with
"batch", "raw" and "provenance". A field may be null, or its value may be
null; both mean "not found".
"""

import re
from decimal import Decimal, InvalidOperation

CONSISTENT = "consistent"
INCONSISTENT = "inconsistent"
MISSING = "missing"
NEEDS_CONFIRMATION = "needs_confirmation"
OUT_OF_SCOPE = "out_of_scope"
STATUSES = (CONSISTENT, INCONSISTENT, MISSING, NEEDS_CONFIRMATION, OUT_OF_SCOPE)


class UnusableNumber(ValueError):
    """A value is present but is not an already-parsed number."""


def finding(check_id, status, message, evidence, rule_id=None, rule_date=None,
            item=None, notes=None, question=None, question_params=None):
    """One check result. question names a fixed vendor-question template
    (checks.questions); it is None when there is nothing to ask the vendor."""
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    return {
        "check_id": check_id,
        "item": item,
        "status": status,
        "message": message,
        "evidence": evidence,
        "rule_id": rule_id,
        "rule_date": rule_date,
        "notes": list(notes or []),
        "question": question,
        "question_params": dict(question_params or {}),
    }


def jsonable(value):
    """Evidence values as JSON-friendly data: Decimals become strings."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if hasattr(value, "parse_status") and hasattr(value, "raw"):
        return {"raw": value.raw, "parse_status": value.parse_status}
    return value


def format_inr(amount):
    """Indian digit grouping, e.g. Decimal("185000") -> "1,85,000"."""
    amount = Decimal(amount)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount == amount.to_integral_value():
        whole, frac = str(int(amount)), ""
    else:
        q = amount.quantize(Decimal("0.01"))
        whole, frac = str(int(q)), "." + str(q).split(".")[1]
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join([head] + groups + [tail])
    return sign + whole + frac


def rupees(amount):
    """₹ and Indian digit grouping with no space, so the sign never wraps away from
    the number: Decimal("163350") -> "₹1,63,350"."""
    return "₹" + format_inr(amount)


# Field names in the internal view -> words a household reads.
FIELD_WORDS = {
    "base_price": "base price", "gst_amount": "GST", "discount": "discount", "gross_total": "total",
    "net_cost": "net cost", "subsidy_central": "central subsidy", "subsidy_state": "state subsidy",
    "subsidy_combined": "subsidy", "subsidy_unspecified": "subsidy", "stated_capacity_kw": "system size",
    "count": "number of panels", "wattage_w": "panel wattage", "make_model": "make and model",
    "rating_kw": "inverter rating", "rating_kva": "inverter rating", "amount": "amount",
}
_ITEM = re.compile(r"^(\w+)\[(\d+)\](?:\.(\w+))?$")


def field_words(name, quote=None, quoted_label=True):
    """Plain words for a field path, e.g. "module_groups[0].count" -> "number of panels",
    "extra_charges[1].amount" -> the charge's own label in quotes."""
    m = _ITEM.match(name)
    if not m:
        return FIELD_WORDS.get(name, name.replace("_", " "))
    list_name, index, key = m.group(1), int(m.group(2)), m.group(3)
    items = (quote or {}).get(list_name) or []
    if list_name == "extra_charges":
        label = items[index].get("label") if index < len(items) else None
        if not label:
            return "an extra charge"
        return f'"{label}"' if quoted_label else label
    key = key or "item"
    words = FIELD_WORDS.get(key, key.replace("_", " "))
    return f"{words} (line {index + 1})" if len(items) > 1 else words


def join_words(words):
    words = list(words)
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def format_number(value):
    """Plain decimal text without trailing zeros, e.g. Decimal("3.300") -> "3.3"."""
    value = Decimal(value)
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def value_of(field):
    if field is None:
        return None
    return field.get("value")


def unresolved(field):
    """True when a field is present but its value could not be read, for example
    because extraction batches disagreed."""
    return hasattr(value_of(field), "parse_status")


USER_KINDS = ("user_corrected", "user_confirmed")


def quoted(name, field):
    """Evidence entry for a field. kind is "quoted" for a value read from the
    quote, or the provenance ("user_corrected", "user_confirmed") for a value
    the user supplied; those are never presented as quoted."""
    provenance = field.get("provenance")
    entry = {
        "kind": provenance if provenance in USER_KINDS else "quoted",
        "field": name,
        "value": jsonable(field.get("value")),
        "evidence_text": field.get("evidence_text"),
        "page": field.get("page"),
    }
    for key in ("batch", "raw", "provenance"):
        if key in field:
            entry[key] = field[key]
    if "original" in field:
        entry["original_value"] = jsonable((field["original"] or {}).get("value"))
    return entry


def computed(name, value, formula):
    """Evidence entry for a value we calculated. Never quoted from the document."""
    return {"kind": "computed", "name": name, "value": str(value), "formula": formula}


def to_decimal(value):
    """Decimal from an already-parsed number; None if absent. No text parsing here."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise UnusableNumber(repr(value))
    if isinstance(value, float):
        value = repr(value)
    if not isinstance(value, (int, str, Decimal)):
        raise UnusableNumber(repr(value))
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise UnusableNumber(repr(value)) from None
    if not number.is_finite():
        raise UnusableNumber(repr(value))
    return number


def option_state(quote):
    """"selected", "single", "multiple" or "unknown".

    "single" needs an explicit no to multiple options and at most one option
    id on the quote. An unknown option count is never treated as single.
    """
    if quote.get("selected_option") is not None:
        return "selected"
    ids = {o.get("option_id") for o in quote.get("options") or []}
    ids |= set(quote.get("fact_options") or [])
    for key in ("module_groups", "inverters", "extra_charges"):
        ids |= {i.get("option_id") for i in quote.get(key) or []}
    ids.discard(None)
    flag = value_of(quote.get("multiple_options"))
    if flag is True or len(ids) > 1:
        return "multiple"
    return "single" if flag is False else "unknown"


def options_unresolved(quote):
    """True unless the quote has one option or the user selected one."""
    return option_state(quote) in ("multiple", "unknown")


def options_finding(check_id, quote):
    field = quote.get("multiple_options")
    if option_state(quote) == "multiple":
        message = "The quote lists more than one option. Select the option you are considering first."
    else:
        message = ("It isn't clear whether the quote has one option or several. Please confirm this before "
                   "the figures are checked.")
    return finding(check_id, NEEDS_CONFIRMATION, message,
                   [quoted("multiple_options", field)] if field is not None else [])
