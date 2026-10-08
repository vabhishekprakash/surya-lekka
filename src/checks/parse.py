"""Turn amount and capacity text copied from a quote into numbers.

Nothing here guesses. Text that could be read more than one way is reported
as ambiguous and left for the user to confirm.
"""

import re
import unicodedata
from decimal import Decimal

OK = "ok"
AMBIGUOUS = "ambiguous"
UNPARSEABLE = "unparseable"
EMPTY = "empty"
PARSE_STATUSES = (OK, AMBIGUOUS, UNPARSEABLE, EMPTY)

LAKH = Decimal(100000)

_CURRENCY = re.compile(r"^(?:₹|rs\.?|inr)\s*|\s*(?:₹|rs\.?|inr)$", re.I)
_SUFFIX = re.compile(r"\s*(?:/-|/=)?\s*(?:only)?\s*$", re.I)
_LAKH = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:lakhs?|lacs?)$", re.I)
_PLAIN = re.compile(r"^\d+(?:\.\d+)?$")
_INDIAN = re.compile(r"^\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d+)?$")
_WESTERN = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_WORDS = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|hundred|thousand|lakhs?|lacs?|crores?|rupees)\b", re.I)


def _clean(raw):
    text = unicodedata.normalize("NFKC", str(raw))
    return " ".join(text.split())


def _amount_result(raw, parsed, status):
    return {"raw": raw, "parsed": parsed, "parse_status": status}


def parse_amount(raw):
    """Parse an INR amount as written on a quote.

    Returns {"raw", "parsed", "parse_status"}. parsed is a Decimal only when
    parse_status is "ok". Accepts Indian ("2,16,000") and western ("482,200.00")
    grouping, leading zeros, Rs / Rs. / the rupee sign / INR, a trailing "/-"
    or "only", and lakh multipliers. Anything else with digits in it (extra
    words, "+ GST", two numbers) is ambiguous; amounts in words are ambiguous.
    """
    if raw is None or not _clean(raw):
        return _amount_result(raw, None, EMPTY)
    text = _clean(raw)
    body = _SUFFIX.sub("", text)
    body = _CURRENCY.sub("", body).strip()
    body = _SUFFIX.sub("", body).strip()

    if m := _LAKH.match(body):
        return _amount_result(raw, Decimal(m.group(1)) * LAKH, OK)
    if _PLAIN.match(body) or _INDIAN.match(body) or _WESTERN.match(body):
        return _amount_result(raw, Decimal(body.replace(",", "")), OK)
    if re.search(r"\d", text) or _WORDS.search(text):
        return _amount_result(raw, None, AMBIGUOUS)
    return _amount_result(raw, None, UNPARSEABLE)


UNITS = {"kw": "kW", "kwp": "kWp", "kva": "kVA", "w": "W", "wp": "Wp"}
_NUMBER = r"(\d+(?:\.\d+)?)"
_UNIT = r"(kwp|kva|kw|wp|w)"
_SINGLE = re.compile(rf"^{_NUMBER}\s*{_UNIT}$", re.I)
_RANGE = re.compile(rf"^{_NUMBER}\s*{_UNIT}?\s*(?:-|–|—|to)\s*{_NUMBER}\s*{_UNIT}$", re.I)


def _capacity_result(raw, value, unit, status):
    return {"raw": raw, "parsed": value, "unit": unit, "parse_status": status}


def parse_capacity(raw):
    """Parse a capacity or rating such as "3.3 kWp", "550Wp" or "595-600Wp".

    Returns {"raw", "parsed", "unit", "parse_status"}. parsed is a Decimal, or
    {"min": Decimal, "max": Decimal} for a range (never a midpoint). Units:
    kW, kWp, kVA, W, Wp. A number without a unit is ambiguous.
    """
    if raw is None or not _clean(raw):
        return _capacity_result(raw, None, None, EMPTY)
    text = _clean(raw)
    if m := _SINGLE.match(text):
        return _capacity_result(raw, Decimal(m.group(1)), UNITS[m.group(2).lower()], OK)
    if m := _RANGE.match(text):
        lo, first_unit, hi, unit = m.groups()
        if first_unit and first_unit.lower() != unit.lower():
            return _capacity_result(raw, None, None, AMBIGUOUS)
        lo, hi = Decimal(lo), Decimal(hi)
        if lo > hi:
            return _capacity_result(raw, None, None, AMBIGUOUS)
        value = lo if lo == hi else {"min": lo, "max": hi}
        return _capacity_result(raw, value, UNITS[unit.lower()], OK)
    if re.search(r"\d", text):
        return _capacity_result(raw, None, None, AMBIGUOUS)
    return _capacity_result(raw, None, None, UNPARSEABLE)
