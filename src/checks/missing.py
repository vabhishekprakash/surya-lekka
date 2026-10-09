"""C4: details a household should ask the vendor about.

Absence is reported as "not found on the quote", never as a fact about the
vendor. Nothing here verifies the details that are present.
"""

import re
from decimal import Decimal

from .common import (
    CONSISTENT,
    MISSING,
    NEEDS_CONFIRMATION,
    UnusableNumber,
    finding,
    format_inr,
    rupees,
    options_finding,
    options_unresolved,
    quoted,
    to_decimal,
    unresolved,
    value_of,
)

CHECK_ID = "C4_missing_details"
NET_METER = re.compile(r"net[\s-]*meter", re.I)


def _item(item, status, message, evidence, question, params=None):
    return finding(CHECK_ID, status, message, evidence, item=item, question=question,
                   question_params=params)


def _choices(main, alternatives):
    names = [value_of(f) for f in [main, *alternatives] if value_of(f)]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " or " + names[-1]


def _amount_text(field):
    v = value_of(field)
    if isinstance(v, Decimal):
        return f"{rupees(v)}"
    if field is not None and field.get("raw"):
        return field["raw"]
    return "amount not given"


def _modules(quote, out):
    groups = quote.get("module_groups") or []
    ev = {"count": [], "wattage": [], "model": []}
    missing = {"count": [], "wattage": [], "model": []}
    unsure = {"count": [], "wattage": [], "model": []}
    ranges, choices = [], []
    for i, g in enumerate(groups):
        name = f"module_groups[{i}]"
        count_f, watt_f, model_f = g.get("count"), g.get("wattage_w"), g.get("make_model")
        alts = g.get("make_model_alternatives") or []
        for key, f in (("count", count_f), ("wattage", watt_f), ("model", model_f)):
            if f is not None:
                ev[key].append(quoted(f"{name}.{key if key != 'model' else 'make_model'}", f))
        try:
            if to_decimal(value_of(count_f)) is None:
                missing["count"].append(name)
        except UnusableNumber:
            unsure["count"].append(name)
        watt = value_of(watt_f)
        if watt is None:
            missing["wattage"].append(name)
        elif isinstance(watt, dict):
            ranges.append(name)
        elif not isinstance(watt, Decimal):
            unsure["wattage"].append(name)
        if unresolved(model_f):
            unsure["model"].append(name)
        elif not value_of(model_f):
            missing["model"].append(name)
        elif alts:
            choices.append(_choices(model_f, alts))
            ev["model"] += [quoted(f"{name}.make_model_alternatives[{j}]", a) for j, a in enumerate(alts)]

    if not groups or missing["wattage"]:
        out.append(_item("panel_wattage", MISSING, "Panel wattage: not found on the quote.",
                         ev["wattage"], "panel_wattage"))
    elif ranges:
        out.append(_item("panel_wattage", NEEDS_CONFIRMATION,
                         "Panel wattage is given as a range, so the exact panel capacity is not fixed.",
                         ev["wattage"], "exact_capacity"))
    elif unsure["wattage"]:
        out.append(_item("panel_wattage", NEEDS_CONFIRMATION, "Please confirm the panel wattage.",
                         ev["wattage"], "panel_wattage"))
    if not groups or missing["count"]:
        out.append(_item("panel_count", MISSING, "Number of panels: not found on the quote.",
                         ev["count"], "panel_count"))
    elif unsure["count"]:
        out.append(_item("panel_count", NEEDS_CONFIRMATION, "Please confirm the number of panels.",
                         ev["count"], "panel_count"))
    if not groups or missing["model"]:
        out.append(_item("module_make_model", MISSING,
                         "Exact panel make and model: not found on the quote.", ev["model"], "module_model"))
    elif unsure["model"]:
        out.append(_item("module_make_model", NEEDS_CONFIRMATION, "Please confirm the panel make and model.",
                         ev["model"], "module_model"))
    elif choices:
        out.append(_item("module_make_model", NEEDS_CONFIRMATION,
                         "The quote lists alternative panel makes or models; it does not say which one "
                         "will be installed.", ev["model"], "module_choice", {"options": "; ".join(choices)}))


def _dcr(quote, out):
    f = quote.get("dcr_declaration")
    v = value_of(f)
    if v is True:
        return
    if unresolved(f):
        out.append(_item("dcr_declaration", NEEDS_CONFIRMATION,
                         "Please confirm what the quote says about DCR (domestic content) panels.",
                         [quoted("dcr_declaration", f)], "dcr"))
        return
    if v is False:
        out.append(_item("dcr_declaration", NEEDS_CONFIRMATION,
                         "The quote appears to say the panels are not DCR. The central subsidy requires DCR "
                         "panels and cells.", [quoted("dcr_declaration", f)], "dcr"))
        return
    out.append(_item("dcr_declaration", MISSING,
                     "DCR (domestic content) declaration for the panels: not found on the quote.",
                     [quoted("dcr_declaration", f)] if f is not None else [], "dcr"))


def _inverters(quote, out):
    invs = quote.get("inverters") or []
    model_ev, rating_ev = [], []
    model_missing, rating_missing, rating_unsure, choices = not invs, not invs, False, []
    model_unsure = False
    for i, inv in enumerate(invs):
        name = f"inverters[{i}]"
        model_f = inv.get("make_model")
        rating_key = "rating_kva" if inv.get("rating_kva") is not None else "rating_kw"
        rating_f = inv.get(rating_key)
        alts = inv.get("make_model_alternatives") or []
        if model_f is not None:
            model_ev.append(quoted(f"{name}.make_model", model_f))
        if rating_f is not None:
            rating_ev.append(quoted(f"{name}.{rating_key}", rating_f))
        if unresolved(model_f):
            model_unsure = True
        elif not value_of(model_f):
            model_missing = True
        elif alts:
            choices.append(_choices(model_f, alts))
            model_ev += [quoted(f"{name}.make_model_alternatives[{j}]", a) for j, a in enumerate(alts)]
        rating = value_of(rating_f)
        if rating is None:
            rating_missing = True
        elif not isinstance(rating, (Decimal, dict)):
            rating_unsure = True
    if model_missing:
        out.append(_item("inverter_make_model", MISSING, "Inverter make and model: not found on the quote.",
                         model_ev, "inverter_model"))
    elif model_unsure:
        out.append(_item("inverter_make_model", NEEDS_CONFIRMATION, "Please confirm the inverter make and model.",
                         model_ev, "inverter_model"))
    elif choices:
        out.append(_item("inverter_make_model", NEEDS_CONFIRMATION,
                         "The quote lists alternative inverters; it does not say which one will be installed.",
                         model_ev, "inverter_choice", {"options": "; ".join(choices)}))
    if rating_missing:
        out.append(_item("inverter_rating", MISSING, "Inverter rating: not found on the quote.",
                         rating_ev, "inverter_rating"))
    elif rating_unsure:
        out.append(_item("inverter_rating", NEEDS_CONFIRMATION, "Please confirm the inverter rating.",
                         rating_ev, "inverter_rating"))


def _vendor_registration(quote, out):
    f = quote.get("vendor_registration")
    if unresolved(f):
        out.append(_item("vendor_registration", NEEDS_CONFIRMATION,
                         "Please confirm the vendor registration number on the quote.",
                         [quoted("vendor_registration", f)], "vendor_registration"))
    elif not value_of(f):
        out.append(_item("vendor_registration", MISSING,
                         "Vendor registration number: not found on the quote. This does not mean the vendor "
                         "is unregistered.", [quoted("vendor_registration", f)] if f is not None else [],
                         "vendor_registration"))


def _gst(quote, out):
    f = quote.get("gst_treatment")
    if value_of(f) not in ("included", "excluded"):
        out.append(_item("gst_basis", NEEDS_CONFIRMATION,
                         "The quote does not make clear whether the price includes GST.",
                         [quoted("gst_treatment", f)] if f is not None else [], "gst_basis"))


def _extras(quote, out):
    outside, evidence, net_meter_seen = [], [], False
    for i, e in enumerate(quote.get("extra_charges") or []):
        label = e.get("label") or f"extra_charges[{i}]"
        if NET_METER.search(str(label)):
            net_meter_seen = True
        if "included_in_total" not in e:
            continue
        inc_f = e["included_in_total"]
        if value_of(inc_f) == "yes":
            continue
        state = "outside the total" if value_of(inc_f) == "no" else "not clearly inside the total"
        where = f" ({e['total_label']})" if e.get("total_label") else ""
        outside.append(f"{label}: {_amount_text(e.get('amount'))}, {state}{where}")
        for key in ("label_field", "amount", "included_in_total"):
            if e.get(key) is not None:
                evidence.append(quoted(f"extra_charges[{i}].{key.replace('_field', '')}", e[key]))
    if outside:
        out.append(_item("extras_outside_total", NEEDS_CONFIRMATION,
                         "Some charges are listed outside the total or not clearly inside it: "
                         + "; ".join(outside) + ".", evidence, "extras_outside_total",
                         {"charges": "; ".join(outside)}))
    if not net_meter_seen:
        out.append(_item("net_meter", NEEDS_CONFIRMATION,
                         "Net-meter charges: not mentioned on the quote.", [], "net_meter"))


def check_missing_details(quote):
    """A list of findings, one per detail to ask about, or one consistent finding."""
    if options_unresolved(quote):
        return [options_finding(CHECK_ID, quote)]
    out = []
    _modules(quote, out)
    _dcr(quote, out)
    _inverters(quote, out)
    _vendor_registration(quote, out)
    _gst(quote, out)
    _extras(quote, out)
    if not out:
        return [finding(CHECK_ID, CONSISTENT,
                        "Every detail this check looks for was found on the quote (panel wattage and count, "
                        "panel and inverter make and model, inverter rating, DCR declaration, vendor "
                        "registration number, GST basis, net-meter charges). They were not verified.", [])]
    return out
