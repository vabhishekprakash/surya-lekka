"""Safety harness: run household scenarios on a quote's raw reading and on its labelled values,
and compare the findings check by check.

Household facts (state, application date, first system, Give It Up, consumer type) are scenario
inputs, never taken from the quote or the vendor's state. Answers the review screen asks about
the quote itself (options, GST included, charges listed, capacity basis, which subsidy the net
cost takes off) come from the labels where they have them, otherwise unanswered.

Labelled-value runs confirm every operand. Raw-output runs come in two kinds: "untouched"
(nothing ticked or confirmed) and "accept_all" (every value to check ticked and every operand
set confirmed as shown, with no correction). No data lives here: labels are passed in.
"""

import copy
import re
from decimal import Decimal

from checks import run_checks
from checks.recheck import _parsed_value
from extract import scoring
from extract.textract_sources import amounts_in

DEFINITIVE = ("consistent", "inconsistent")
CHECKS = ("C1_capacity", "C2_central_subsidy", "C3_gross_total", "C3_net_cost")
HOUSEHOLD = {"state": "Telangana", "consumer_type": "individual_household", "portal_application_on_or_after_cutoff": True,
             "first_system": True, "prior_central_subsidy": False, "give_it_up": False}
SCENARIOS = {
    "S-A": HOUSEHOLD,
    "S-B": {**HOUSEHOLD, "state": "Assam"},
    "S-C": {**HOUSEHOLD, "portal_application_on_or_after_cutoff": False},
    "S-D": {},
    "S-E": {**HOUSEHOLD, "consumer_type": "rwa"},
}
SUBSIDY_BASIS = {"central": "central", "state": "state", "combined": "combined"}


def _val(truth, key):
    return (truth.get(key) or {}).get("value")


def quote_answers(truth):
    """Review-screen answers about the quote itself, from the labels where they have them."""
    out = {}
    for flag, kind in (("multiple_options", "bool"), ("gst_treatment", "enum"), ("extra_charges_complete", "bool"),
                       ("capacity_basis", "enum"), ("net_cost_subsidy_basis", "enum")):
        if _val(truth, flag) is None:
            continue
        key = scoring.truth_key(kind, _val(truth, flag), flag)
        if key[0] in ("bool", "enum"):
            out[flag] = key[1]
    if "net_cost_subsidy_basis" not in out and _val(truth, "subsidy_type"):
        kind = scoring.truth_key("enum", _val(truth, "subsidy_type"), "subsidy_type")[1]
        if kind in SUBSIDY_BASIS:
            out["net_cost_subsidy_basis"] = SUBSIDY_BASIS[kind]
    return out


def answers(scenario, truth):
    return {**SCENARIOS[scenario], **quote_answers(truth)}


# --- the labelled quote -----------------------------------------------------------------------

# Labels carry notes ("Rs.78,000 for 3 kW", "3 KW (DC)", "6 No.s"). These read the one value a
# label states and nothing else: a label with two different values gives none.
_CAPACITY = re.compile(r"(\d+(?:\.\d+)?)\s*(kwp|kva|kw|wp|watts?|w)\b", re.I)
_COUNT = re.compile(r"^\s*(\d{1,3})\s*(?:nos?\.?s?|numbers?|pcs\.?|panels?|modules?)?\s*$", re.I)


def label_amount(text):
    """The one rupee amount a label states, as text, or None."""
    if not text:
        return None
    found = {value.normalize() for part in str(text).split(";") for _, value in amounts_in(part)}
    return format(found.pop(), "f") if len(found) == 1 else None


def label_capacity(text):
    """The first number with a unit in a label's first mention, as "3.3 kWp", or None."""
    if not text:
        return None
    first = [p.strip() for p in str(text).replace("\n", ";").split(";") if p.strip()][0]
    m = _CAPACITY.search(first)
    return f"{m.group(1)} {m.group(2)}" if m else None


def label_count(text):
    c = scoring.count_value(text)
    if isinstance(c, int):
        return c
    m = _COUNT.match(str(text))
    return int(m.group(1)) if m else None


def _wattage(text):
    return text if re.search(r"\d\s*\w*\s*-\s*\d", text) else label_capacity(text) or text


def _field(kind, text, name):
    return {"value": _parsed_value(kind, text), "evidence_text": f"label:{name}", "page": None, "batch": 1}


def _first_capacity(text):
    return label_capacity(text) or [p.strip() for p in str(text).replace("\n", ";").split(";") if p.strip()][0]


def labelled_quote(truth):
    """A contract v1 quote holding the labelled values as if read from the quote."""
    q = {"contract_version": "v1", "processing_complete": True, "pages_processed": [], "options": [],
         "option_fields": {}, "module_groups": [], "inverters": [], "extra_charges": [],
         "stated_capacity": None, "dcr_declaration": None, "vendor_registration": None,
         "flags": {"model_proposed": {}, "user_confirmed": {}}}
    amounts = ("base_price", "gst_amount", "discount", "gross_total", "net_cost", "subsidy_central", "subsidy_state",
               "subsidy_combined", "subsidy_unspecified")
    for name in amounts:
        text = label_amount(_val(truth, name)) or _val(truth, name)
        q[name] = _field("amount", text, name) if text else None
    if _val(truth, "subsidy") and not any(q[n] for n in amounts[5:]):
        kind = scoring.truth_key("enum", _val(truth, "subsidy_type"), "subsidy_type")[1] \
            if _val(truth, "subsidy_type") else None
        target = {"central": "subsidy_central", "state": "subsidy_state", "combined": "subsidy_combined"}.get(
            kind, "subsidy_unspecified")
        q[target] = _field("amount", label_amount(_val(truth, "subsidy")) or _val(truth, "subsidy"), "subsidy")
    if _val(truth, "stated_capacity"):
        q["stated_capacity"] = _field("capacity", _first_capacity(_val(truth, "stated_capacity")), "stated_capacity")

    def parts(key):
        return scoring._split(_val(truth, key)) if _val(truth, key) else []
    counts, watts, makes = parts("panel_count"), parts("panel_wattage"), parts("module_make_model")
    for i in range(max(len(counts), len(watts), len(makes))):
        count = label_count(counts[i]) if i < len(counts) else None
        q["module_groups"].append({
            "group_id": f"G{i + 1}", "option_id": None, "make_model_alternatives": [],
            "count": {"value": count, "evidence_text": "label:panel_count", "page": None, "batch": 1}
            if isinstance(count, int) else None,
            "wattage": _field("capacity", _wattage(watts[i]), "panel_wattage") if i < len(watts) else None,
            "make_model": {"value": makes[i], "evidence_text": "label:module_make_model", "page": None, "batch": 1}
            if i < len(makes) else None})
    ratings, inv_makes = parts("inverter_rating"), parts("inverter_make_model")
    for i in range(max(len(ratings), len(inv_makes))):
        q["inverters"].append({
            "inverter_id": f"I{i + 1}", "option_id": None, "make_model_alternatives": [],
            "rating": _field("capacity", ratings[i], "inverter_rating") if i < len(ratings) else None,
            "make_model": {"value": inv_makes[i], "evidence_text": "label:inverter_make_model", "page": None,
                           "batch": 1} if i < len(inv_makes) else None})
    for i, text in enumerate(parts("extra_charges_outside_total"), 1):
        amount = scoring._amount_key_of_text(text)
        q["extra_charges"].append({
            "charge_id": f"L{i}", "option_id": None, "total_label": None,
            "label": {"value": f"charge {i}", "evidence_text": "label:extra_charges_outside_total", "page": None,
                      "batch": 1},
            "amount": _field("amount", str(amount[1]) if amount else text, "extra_charges_outside_total"),
            "included_in_total": {"value": "no", "evidence_text": "label", "page": None, "batch": 1}})
    if _val(truth, "dcr_declaration") is not None:
        d = scoring.truth_key("bool", _val(truth, "dcr_declaration"))
        if d[0] == "bool":
            q["dcr_declaration"] = {"value": d[1], "evidence_text": "label:dcr_declaration", "page": None, "batch": 1}
    if _val(truth, "vendor_registration"):
        q["vendor_registration"] = {"value": _val(truth, "vendor_registration"), "evidence_text": "label",
                                    "page": None, "batch": 1}
    return q


# --- runs ----------------------------------------------------------------------------------------

def _run(quote, inputs):
    return run_checks(copy.deepcopy(quote), copy.deepcopy(inputs))


def untouched(quote, confirmations):
    return _run(quote, {"confirmations": confirmations})


def accept_all(quote, confirmations, rounds=4):
    """Every value to check ticked and every operand set confirmed exactly as shown, repeated
    until nothing new appears. Conflicts can't be ticked; nothing is corrected."""
    inputs = {"confirmations": confirmations, "verified": [], "confirmed_operands": []}
    result = _run(quote, inputs)
    for _ in range(rounds):
        ticks = sorted({c["token"] for c in result["check_this"] if "conflict" not in c["reasons"]}
                       | {e["token"] for e in result["entry_checks"]} | set(inputs["verified"]))
        confirms = sorted({f["confirm_token"] for f in result["findings"] if f.get("confirm_token")}
                          | set(inputs["confirmed_operands"]))
        if ticks == inputs["verified"] and confirms == inputs["confirmed_operands"]:
            break
        inputs = {**inputs, "verified": ticks, "confirmed_operands": confirms}
        result = _run(quote, inputs)
    return result


def labelled(truth, confirmations):
    """The labelled values with every operand confirmed."""
    return accept_all(labelled_quote(truth), confirmations)


# --- comparing -----------------------------------------------------------------------------------

def by_check(result):
    return {(f["check_id"], f.get("item")): f for f in result["findings"]}


def operand_values(f):
    return sorted((o["field"], str(o["value"])) for o in f.get("operands") or [])


def _num(text):
    try:
        return Decimal(str(text))
    except Exception:
        return None


def truth_number(truth, name, index):
    """The labelled number for an operand field, in the checks' unit (rupees, kW or W), or None."""
    if name in ("base_price", "gst_amount", "discount", "gross_total", "net_cost", "subsidy_central", "subsidy_state",
                "subsidy_combined", "subsidy_unspecified"):
        text = _val(truth, name) or (_val(truth, "subsidy") if name.startswith("subsidy") else None)
        amount = label_amount(text)
        return Decimal(amount) if amount else None
    to_kw = {"kW": 1, "kWp": 1, "W": Decimal("0.001"), "Wp": Decimal("0.001")}
    to_w = {"W": 1, "Wp": 1, "kW": 1000, "kWp": 1000}
    if name == "stated_capacity_kw" and _val(truth, "stated_capacity"):
        r = _parsed_value("capacity", _first_capacity(_val(truth, "stated_capacity")))
        n = _num(r.get("parsed")) if r.get("parse_status") == "ok" else None
        return None if n is None or r.get("unit") not in to_kw else n * to_kw[r["unit"]]
    labels = {"count": "panel_count", "wattage_w": "panel_wattage", "rating_kw": "inverter_rating"}
    if name in labels and _val(truth, labels[name]):
        items = scoring._split(_val(truth, labels[name]))
        if index is None or index >= len(items):
            return None
        if name == "count":
            c = label_count(items[index])
            return Decimal(c) if isinstance(c, int) else None
        r = _parsed_value("capacity", _wattage(items[index]))
        n = _num(r.get("parsed")) if r.get("parse_status") == "ok" else None
        scale = to_w if name == "wattage_w" else to_kw
        return None if n is None or r.get("unit") not in scale else n * scale[r["unit"]]
    return None


def wrong_operands(f, truth):
    """Operand fields of a finding whose number differs from the labelled number."""
    out = []
    for o in f.get("operands") or []:
        m = re.match(r"(?:module_groups|inverters)\[(\d+)\]\.(\w+)$", o["field"])
        name, index = (m.group(2), int(m.group(1))) if m else (o["field"], None)
        got = _num(o["value"]) if not isinstance(o["value"], dict) else None
        want = truth_number(truth, name, index)
        if got is not None and want is not None and got.normalize() != want.normalize():
            out.append(o["field"])
    return out
