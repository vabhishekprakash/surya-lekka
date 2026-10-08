"""Quote contract v1 and the internal view the checks read.

The extraction step writes contract v1 (see tests/fixtures/quote_contract_v1.json).
A field the merge step marks "conflict": true (batches disagreed) is never
read as a value.
normalise() turns it into the flat view the checks use: amounts and
capacities become Decimals in fixed units, flags resolve to the user's answer
when there is one and to the model's reading otherwise, and items that belong
to an option the user did not select are dropped.
"""

from decimal import Decimal, InvalidOperation

CONTRACT_VERSION = "v1"

AMOUNT_FIELDS = (
    "base_price", "gst_amount", "discount", "gross_total",
    "subsidy_central", "subsidy_state", "subsidy_combined", "subsidy_unspecified", "net_cost",
)
FLAG_NAMES = (
    "multiple_options", "capacity_basis", "gst_treatment", "extra_charges_complete",
    "net_cost_subsidy_basis", "consumer_type", "give_it_up",
)
USER_PROVENANCE = "user_confirmed"

# Multipliers into the unit each check works in. kVA (apparent power) is its own
# dimension: it is never converted to or compared as kW or kWp.
TO_WATTS = {"W": Decimal(1), "Wp": Decimal(1), "kW": Decimal(1000), "kWp": Decimal(1000)}
TO_KW = {"kW": Decimal(1), "kWp": Decimal(1), "W": Decimal("0.001"), "Wp": Decimal("0.001")}
KVA = {"kVA": Decimal(1)}


class Unparsed:
    """A value present on the quote that did not parse cleanly. Checks treat it
    as needing confirmation, never as a number."""

    __slots__ = ("raw", "parse_status")

    def __init__(self, raw, parse_status):
        self.raw, self.parse_status = raw, parse_status

    def __eq__(self, other):
        return isinstance(other, Unparsed) and (self.raw, self.parse_status) == (other.raw, other.parse_status)

    def __hash__(self):
        return hash((self.raw, self.parse_status))

    def __repr__(self):
        return f"Unparsed({self.raw!r}, {self.parse_status!r})"


def _field(f, value, raw=None):
    out = {"value": value, "evidence_text": f.get("evidence_text"),
           "page": f.get("page"), "batch": f.get("batch")}
    if raw is not None:
        out["raw"] = raw
    for key in ("provenance", "original"):
        if key in f:
            out[key] = f[key]
    return out


def plain(f):
    """A field as is; a field the merge marked as a conflict between batches
    becomes Unparsed, which the checks treat as needing confirmation."""
    if f is None:
        return None
    if f.get("conflict"):
        return _field(f, Unparsed(None, "conflict"))
    return _field(f, f.get("value"))


def _decimal(text):
    try:
        number = Decimal(str(text))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def amount(f):
    """Amount field -> Decimal value, None when empty, Unparsed otherwise."""
    if f is None:
        return None
    v = f.get("value")
    if v is None:
        return _field(f, None)
    status = v.get("parse_status")
    number = _decimal(v.get("parsed")) if status == "ok" else None
    if status == "empty":
        value = None
    elif number is None:
        value = Unparsed(v.get("raw"), status or "missing_status")
    else:
        value = number
    return _field(f, value, raw=v.get("raw"))


def measure(f, scale):
    """Capacity field -> Decimal or {"min", "max"} in the target unit, else Unparsed."""
    if f is None:
        return None
    v = f.get("value")
    if v is None:
        return _field(f, None)
    status, unit, parsed = v.get("parse_status"), v.get("unit"), v.get("parsed")
    if status == "empty":
        return _field(f, None, raw=v.get("raw"))
    factor = scale.get(unit)
    value = Unparsed(v.get("raw"), "unit_not_comparable" if status == "ok" else status or "missing_status")
    if status == "ok" and factor is not None:
        if isinstance(parsed, dict):
            lo, hi = _decimal(parsed.get("min")), _decimal(parsed.get("max"))
            if lo is not None and hi is not None and lo <= hi:
                value = lo * factor if lo == hi else {"min": lo * factor, "max": hi * factor}
        elif (number := _decimal(parsed)) is not None:
            value = number * factor
    return _field(f, value, raw=v.get("raw"))


def _unit(f):
    v = (f or {}).get("value")
    return v.get("unit") if isinstance(v, dict) else None


def _flag(name, proposed, confirmed):
    if confirmed.get(name) is not None:
        return {"value": confirmed[name], "evidence_text": None, "page": None, "batch": None,
                "provenance": USER_PROVENANCE}
    return plain(proposed.get(name))


def effective_option(quote):
    """The option whose own facts apply: the user's selection, else the only option
    id on the quote, else None."""
    selected = ((quote.get("flags") or {}).get("user_confirmed") or {}).get("selected_option")
    if selected is not None:
        return selected
    ids = set(quote.get("option_fields") or {})
    ids |= {o.get("option_id") for o in quote.get("options") or []}
    for key in ("module_groups", "inverters", "extra_charges"):
        ids |= {i.get("option_id") for i in quote.get(key) or []}
    ids.discard(None)
    return ids.pop() if len(ids) == 1 else None


def normalise(quote):
    if quote.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("expected a contract v1 quote")
    flags = quote.get("flags") or {}
    proposed = flags.get("model_proposed") or {}
    confirmed = flags.get("user_confirmed") or {}
    selected = confirmed.get("selected_option")

    def chosen(items):
        return [i for i in items or [] if selected is None or i.get("option_id") in (None, selected)]

    # An option's own price, subsidy and capacity facts replace the quote-wide ones;
    # another option's facts are never used.
    option_fields = quote.get("option_fields") or {}
    own = option_fields.get(effective_option(quote)) or {}

    def pick(name):
        return own[name] if name in own else quote.get(name)

    view = {
        "contract_version": CONTRACT_VERSION,
        "processing_complete": quote.get("processing_complete") is True,
        "pages_processed": list(quote.get("pages_processed") or []),
        "options": list(quote.get("options") or []),
        "fact_options": sorted(option_fields),
        "selected_option": selected,
        "module_groups": [
            {
                "group_id": g.get("group_id"),
                "option_id": g.get("option_id"),
                "count": plain(g.get("count")),
                "wattage_w": measure(g.get("wattage"), TO_WATTS),
                "make_model": plain(g.get("make_model")),
                "make_model_alternatives": [plain(a) for a in g.get("make_model_alternatives") or []],
            }
            for g in chosen(quote.get("module_groups"))
        ],
        "inverters": [
            {
                "inverter_id": i.get("inverter_id"),
                "option_id": i.get("option_id"),
                "make_model": plain(i.get("make_model")),
                "rating_kw": None if _unit(i.get("rating")) == "kVA" else measure(i.get("rating"), TO_KW),
                "rating_kva": measure(i.get("rating"), KVA) if _unit(i.get("rating")) == "kVA" else None,
                "make_model_alternatives": [plain(a) for a in i.get("make_model_alternatives") or []],
            }
            for i in chosen(quote.get("inverters"))
        ],
        "stated_capacity_kw": measure(pick("stated_capacity"), TO_KW),
        "stated_capacity_unit": _unit(pick("stated_capacity")),
        "dcr_declaration": plain(quote.get("dcr_declaration")),
        "vendor_registration": plain(quote.get("vendor_registration")),
        "extra_charges": [
            {
                "charge_id": e.get("charge_id"),
                "option_id": e.get("option_id"),
                "label": (e.get("label") or {}).get("value"),
                "label_field": plain(e.get("label")),
                "amount": amount(e.get("amount")),
                "included_in_total": plain(e.get("included_in_total")),
                "total_label": e.get("total_label"),
            }
            for e in chosen(quote.get("extra_charges"))
        ],
        "state": confirmed.get("state"),
        "portal_application_on_or_after_cutoff": confirmed.get("portal_application_on_or_after_cutoff"),
        "first_system": confirmed.get("first_system"),
        "prior_central_subsidy": confirmed.get("prior_central_subsidy"),
        "user_confirmed": dict(confirmed),
    }
    view.update({name: amount(pick(name)) for name in AMOUNT_FIELDS})
    view.update({name: _flag(name, proposed, confirmed) for name in FLAG_NAMES})
    stated = view["stated_capacity_kw"]
    if view["stated_capacity_unit"] == "kVA" and confirmed.get("capacity_basis") is None:
        # A system size in kVA says nothing about DC panel capacity.
        view["capacity_basis"] = {"value": "unclear", "evidence_text": stated["evidence_text"],
                                  "page": stated["page"], "batch": stated["batch"]}
    return view
