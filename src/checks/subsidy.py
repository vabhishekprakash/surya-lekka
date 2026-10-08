"""C2: stated central subsidy against the central CFA rule for the panel capacity.

Gates run in a fixed order and the first one that applies decides the
result. DCR eligibility and subsidy opt-out are never inferred from a quote.
State subsidies are not checked.
"""

import re
from decimal import Decimal

from rules import load_cfa_rules

from .common import (
    CONSISTENT,
    INCONSISTENT,
    MISSING,
    NEEDS_CONFIRMATION,
    OUT_OF_SCOPE,
    UnusableNumber,
    computed,
    finding,
    format_inr,
    format_number,
    options_unresolved,
    quoted,
    to_decimal,
    value_of,
)

CHECK_ID = "C2_central_subsidy"
TOLERANCE_INR = Decimal("1")
HOUSEHOLD = "individual_household"
NON_HOUSEHOLD = {"rwa", "group_housing", "other_non_household"}

DCR_NOTE = ("The central subsidy also requires DCR (domestic content) panels and cells. "
            "This was not verified from the quote.")
PENDING_NOTE = "The rule values used here are pending verification against the MNRE sources."


def _norm_name(name):
    text = str(name).lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z ]", " ", text).split())


def state_category(state, rules):
    """"special" if the state or UT is on the special-category list, else "general"."""
    sc = rules["special_category"]
    names = {_norm_name(n): n for n in sc["states"] + sc["union_territories"]}
    names.update({_norm_name(a): n for a, n in sc.get("aliases", {}).items()})
    return "special" if _norm_name(state) in names else "general"


def rule_for(category, rules):
    return next(r for r in rules["rules"] if r.get("category") == category)


def cfa_amount(dc_kwp, rule):
    """Prorated CFA for a DC module capacity, capped."""
    total = Decimal(0)
    for slab in rule["slabs"]:
        lo, hi = Decimal(slab["from_kwp"]), Decimal(slab["to_kwp"])
        total += Decimal(slab["inr_per_kwp"]) * min(max(dc_kwp - lo, Decimal(0)), hi - lo)
    return min(total, Decimal(rule["cap_inr"]))


def rule_formula(rule):
    parts = []
    for slab in rule["slabs"]:
        lo, hi, rate = slab["from_kwp"], slab["to_kwp"], slab["inr_per_kwp"]
        if lo == "0":
            parts.append(f"{rate} x min(dc, {hi})")
        else:
            parts.append(f"{rate} x min(max(dc - {lo}, 0), {Decimal(hi) - Decimal(lo)})")
    return " + ".join(parts) + f", cap {rule['cap_inr']}"


def _bounds(value):
    if isinstance(value, dict):
        return value.get("min"), value.get("max")
    return value, value


def dc_capacity(quote):
    """(min_kwp, max_kwp, how, evidence) for the DC panel capacity, or None.

    Panel count x wattage when every module group has both; otherwise a stated
    capacity the quote calls DC. A wattage or capacity range stays a range.
    """
    evidence = []
    groups = quote.get("module_groups") or []
    lo_w = hi_w = Decimal(0)
    usable = bool(groups)
    terms = []
    for i, g in enumerate(groups):
        count_f, watt_f = g.get("count"), g.get("wattage_w")
        for key, f in (("count", count_f), ("wattage_w", watt_f)):
            if f is not None:
                evidence.append(quoted(f"module_groups[{i}].{key}", f))
        try:
            count = to_decimal(value_of(count_f))
        except UnusableNumber:
            count = None
        w_lo, w_hi = _bounds(value_of(watt_f))
        if not (isinstance(count, Decimal) and count > 0 and count == count.to_integral_value()
                and isinstance(w_lo, Decimal) and isinstance(w_hi, Decimal) and w_lo > 0):
            usable = False
            continue
        lo_w += count * w_lo
        hi_w += count * w_hi
        terms.append(f"{format_number(count)} x {format_number(w_lo)}"
                     + ("" if w_lo == w_hi else f"-{format_number(w_hi)}") + " W")
    if usable:
        return lo_w / 1000, hi_w / 1000, f"({' + '.join(terms)}) / 1000", evidence

    stated_f, basis_f = quote.get("stated_capacity_kw"), quote.get("capacity_basis")
    if value_of(basis_f) == "dc_kwp" and stated_f is not None:
        lo, hi = _bounds(value_of(stated_f))
        if isinstance(lo, Decimal) and isinstance(hi, Decimal) and lo > 0:
            evidence += [quoted("stated_capacity_kw", stated_f), quoted("capacity_basis", basis_f)]
            return lo, hi, "stated DC capacity", evidence
    return None


def _subsidy_evidence(quote):
    return [quoted(n, quote[n]) for n in ("subsidy_central", "subsidy_state", "subsidy_combined",
                                          "subsidy_unspecified") if quote.get(n) is not None]


def check_central_subsidy(quote, rules=None):
    rules = rules or load_cfa_rules()
    rule_date = rules["effective_from"]["value"]
    notes = [DCR_NOTE, PENDING_NOTE]

    def result(status, message, evidence=(), rule=None, question=None, params=None):
        return finding(CHECK_ID, status, message, list(evidence),
                       rule_id=rule["rule_id"] if rule else None,
                       rule_date=rule_date if rule else None,
                       notes=notes, question=question, question_params=params)

    if not quote.get("processing_complete"):
        return result(NEEDS_CONFIRMATION,
                      "Not every page of the quote was processed, so the subsidy was not checked.")
    if options_unresolved(quote):
        return result(NEEDS_CONFIRMATION,
                      "The quote lists more than one option. Select the option you are considering first.",
                      [quoted("multiple_options", quote["multiple_options"])])
    state = quote.get("state")
    if not state:
        return result(NEEDS_CONFIRMATION,
                      "Which state is the house in? The central subsidy rate depends on it.")
    ct_field = quote.get("consumer_type")
    if value_of(ct_field) in NON_HOUSEHOLD:
        return result(OUT_OF_SCOPE,
                      "This check covers individual households only, not RWAs, group housing or other "
                      "non-household connections.", [quoted("consumer_type", ct_field)])
    portal = quote.get("portal_application_on_or_after_cutoff")
    if portal is False:
        return result(OUT_OF_SCOPE,
                      "Applications made on the national portal before 13 Feb 2024 follow earlier rules, "
                      "which this check does not cover.")
    if portal is not True:
        return result(NEEDS_CONFIRMATION,
                      "Please confirm that the subsidy application is (or will be) made on the national "
                      "portal on or after 13 Feb 2024. The rule checked here applies from that date.")

    sub_evidence = _subsidy_evidence(quote)
    central_f = quote.get("subsidy_central")
    other = [n for n in ("subsidy_combined", "subsidy_unspecified") if value_of(quote.get(n)) is not None]
    if value_of(central_f) is None and other:
        return result(NEEDS_CONFIRMATION,
                      "The quote gives one subsidy figure without saying how much is central and how much "
                      "is state. Ask the vendor for the central and state amounts separately.",
                      sub_evidence, question="subsidy_breakdown")

    dc = dc_capacity(quote)
    if dc is None:
        return result(NEEDS_CONFIRMATION,
                      "The DC capacity of the panels is not known, so the central rule could not be applied.",
                      sub_evidence, question="dc_capacity")
    lo, hi, how, dc_evidence = dc
    category = state_category(state, rules)
    rule = rule_for(category, rules)
    evidence = dc_evidence + sub_evidence
    formula = rule_formula(rule)
    amount_lo, amount_hi = cfa_amount(lo, rule), cfa_amount(hi, rule)
    if lo != hi:
        evidence.append(computed("dc_kwp_range", f"{format_number(lo)}-{format_number(hi)}", how))
        evidence.append(computed("cfa_range", f"{format_inr(amount_lo)}-{format_inr(amount_hi)}", formula))
        if amount_lo != amount_hi:
            return result(NEEDS_CONFIRMATION,
                          f"The panel capacity is a range ({format_number(lo)} to {format_number(hi)} kWp), "
                          f"which gives a central subsidy between Rs {format_inr(amount_lo)} and "
                          f"Rs {format_inr(amount_hi)}. The exact panel wattage is needed.",
                          evidence, rule, question="exact_capacity")
    else:
        evidence.append(computed("dc_kwp", lo, how))
    expected = amount_lo
    evidence.append(computed("central_cfa_rule", expected, f"{formula} ({category} category, {state})"))
    dc_text = format_number(lo) if lo == hi else f"{format_number(lo)} to {format_number(hi)}"

    try:
        stated = to_decimal(value_of(central_f))
    except UnusableNumber:
        return result(NEEDS_CONFIRMATION, "Please confirm the central subsidy amount on the quote.",
                      evidence, rule)
    if stated is None:
        return result(MISSING,
                      f"The quote does not state a central subsidy amount. The central rule gives up to "
                      f"Rs {format_inr(expected)} for {dc_text} kWp of panels.", evidence, rule)

    params = {"stated": format_inr(stated), "rule": format_inr(expected), "dc_kwp": dc_text}
    if abs(stated - expected) <= TOLERANCE_INR:
        return result(CONSISTENT,
                      f"The stated central subsidy Rs {format_inr(stated)} matches the central rule for "
                      f"{dc_text} kWp of panels (Rs {format_inr(expected)}).", evidence, rule)
    if stated > expected:
        return result(INCONSISTENT,
                      f"The stated central subsidy Rs {format_inr(stated)} is higher than the central rule "
                      f"for this panel capacity ({dc_text} kWp gives Rs {format_inr(expected)}).",
                      evidence, rule, question="subsidy_higher", params=params)
    return result(NEEDS_CONFIRMATION,
                  f"The stated central subsidy Rs {format_inr(stated)} is lower than the rule maximum for "
                  f"{dc_text} kWp of panels (Rs {format_inr(expected)}); ask the vendor why.",
                  evidence, rule, question="subsidy_lower", params=params)
