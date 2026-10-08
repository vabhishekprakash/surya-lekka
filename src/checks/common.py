"""Finding model and field helpers shared by the deterministic checks.

Checks read the internal view (checks.contract.normalise). Fields are nullable
wrappers: {"value": ..., "evidence_text": ..., "page": ...}, optionally with
"batch", "raw" and "provenance". A field may be null, or its value may be
null; both mean "not found".
"""

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


def quoted(name, field):
    """Evidence entry copied from an extracted field, with its source text and page."""
    entry = {
        "kind": "quoted",
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


def options_unresolved(quote):
    """True when the quote has alternative options and none is selected yet."""
    return value_of(quote.get("multiple_options")) is True and quote.get("selected_option") is None


def options_finding(check_id, quote):
    return finding(
        check_id,
        NEEDS_CONFIRMATION,
        "The quote lists more than one option. Select the option you are considering first.",
        [quoted("multiple_options", quote["multiple_options"])],
    )
