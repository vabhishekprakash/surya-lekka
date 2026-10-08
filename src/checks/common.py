"""Finding model and field helpers shared by the deterministic checks.

Extracted fields are nullable wrappers: {"value": ..., "evidence_text": ..., "page": ...}.
A field may be null, or its value may be null; both mean "not found".
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


def finding(check_id, status, message, evidence, rule_id=None, rule_date=None):
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    return {
        "check_id": check_id,
        "status": status,
        "message": message,
        "evidence": evidence,
        "rule_id": rule_id,
        "rule_date": rule_date,
    }


def value_of(field):
    if field is None:
        return None
    return field.get("value")


def quoted(name, field):
    """Evidence entry copied from an extracted field, with its source text and page."""
    return {
        "kind": "quoted",
        "field": name,
        "value": field.get("value"),
        "evidence_text": field.get("evidence_text"),
        "page": field.get("page"),
    }


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
