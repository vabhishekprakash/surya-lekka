"""Values the household must check before any finding uses them.

A value read from the quote needs checking when it is a conflict, couldn't be read
cleanly, was read with confidence below the band in src/rules/check_this.json, or was made
by a source rule that produced a wrong value on the development quotes (listed in the
same file). The rule looks only at these properties of the value, never at which quote
or value it is. A value the household verified or corrected needs nothing more, and a
value with no source rule (a saved sample or a typed-in number) is taken as given.

Until the household verifies or corrects that one value, normalise() reads it as
Unparsed, so every finding that uses it stays "needs confirmation".
"""

import json
from pathlib import Path

from .confirm import value_token
from .common import jsonable

CONFIG = json.loads((Path(__file__).resolve().parent.parent / "rules" / "check_this.json").read_text(encoding="utf-8"))
SETTLED = ("user_corrected", "user_confirmed")
REASONS = ("conflict", "unresolved", "low_confidence", "source_rule")


def reasons(field, config=None):
    """The reasons this value needs checking, in REASONS order; [] when it needs none."""
    config = CONFIG if config is None else config
    if field and field.get("provenance") == "user_corrected" and field.get("original") \
            and _same_number(field["value"], field["original"].get("value")):
        return reasons(field["original"], config)  # a format-only edit settles nothing
    if not field or field.get("provenance") in SETTLED:
        return []
    found = set()
    if field.get("conflict"):
        found.add("conflict")
    value = field.get("value")
    if isinstance(value, dict) and value.get("parse_status") not in (None, "ok", "empty", "conflict"):
        found.add("unresolved")
    sources = [field] + list(field.get("candidates") or [])
    confidences = [s["confidence"] for s in sources if s.get("confidence") is not None]
    if confidences and min(confidences) < config["low_confidence_below"]:
        found.add("low_confidence")
    rules = {r for s in sources for r in s.get("rules") or []}
    if rules & (set(config["rules_with_dev_errors"]) | {"unmatched"}):
        found.add("source_rule")
    return [r for r in REASONS if r in found]


def _same_number(a, b):
    def number(v):
        return (v.get("parsed"), v.get("unit")) if isinstance(v, dict) and v.get("parse_status") == "ok" else v
    return a is not None and number(a) == number(b)


FACTS = ("stated_capacity", "base_price", "gst_amount", "discount", "gross_total", "subsidy_central",
         "subsidy_state", "subsidy_combined", "subsidy_unspecified", "net_cost")


def _paths(quote, selected, own_option):
    """(path, option id, field) for every value a finding can use, with the path a
    correction or a verification names. Before an option is chosen, each option's own
    facts are listed under their option id."""
    options = quote.get("option_fields") or {}
    own = options.get(own_option) or {}
    for name in FACTS:
        yield name, own_option if name in own else None, own[name] if name in own else quote.get(name)
    if own_option is None:
        for option_id in sorted(options):
            for name in FACTS:
                if name in options[option_id]:
                    yield name, option_id, options[option_id][name]

    def chosen(items):
        return [i for i in items or [] if selected is None or i.get("option_id") in (None, selected)]
    for list_name, id_key, keys in (("module_groups", "group_id", ("count", "wattage", "make_model")),
                                    ("inverters", "inverter_id", ("make_model", "rating")),
                                    ("extra_charges", "charge_id", ("label", "amount", "included_in_total"))):
        for item in chosen(quote.get(list_name)):
            for key in keys:
                yield f"{list_name}[{item.get(id_key)}].{key}", item.get("option_id"), item.get(key)
    for name in ("dcr_declaration", "vendor_registration"):
        yield name, None, quote.get(name)
    flags = quote.get("flags") or {}
    confirmed = flags.get("user_confirmed") or {}
    for name, field in (flags.get("model_proposed") or {}).items():
        if confirmed.get(name) is None:
            yield f"flags.{name}", None, field


def mark(quote, selected, own_option, config=None, verified=()):
    """Mark each value that needs checking with "check_this": [reasons], in place, and
    return [{"path", "option_id", "reasons", "value", "token"}] sorted by option and path. A
    value whose token (its option, field and value) the household verified needs nothing more;
    a conflict can't be verified, only corrected."""
    out, verified = [], set(verified)
    for path, option_id, field in _paths(quote, selected, own_option):
        found = reasons(field, config)
        token = value_token(path, own_option, field) if field else None
        if found and "conflict" not in found and token in verified:
            field["verified"] = True
            found = []
        if found:
            field["check_this"] = found
            out.append({"path": path, "option_id": option_id, "reasons": found,
                        "value": jsonable(field.get("value")), "token": token})
        elif field:
            field.pop("check_this", None)
    return sorted(out, key=lambda c: (c["option_id"] or "", c["path"]))
