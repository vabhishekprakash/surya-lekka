"""C2: stated central subsidy against the central CFA rule for the panel capacity.

Gates run in a fixed order and the first one that applies decides the
result. Consumer type, state, portal date, prior subsidy and Give It Up come
from the user's answers; DCR eligibility and subsidy opt-out are never
inferred from a quote. State subsidies are not checked.
"""

import re
from datetime import date
from decimal import Decimal

from rules import load_cfa_rules, pending_facts

from .common import (
    CONSISTENT,
    INCONSISTENT,
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
HOUSEHOLD = "individual_household"
NON_HOUSEHOLD = {"rwa", "group_housing", "other_non_household"}
USER_CONFIRMED = "user_confirmed"

# Gates in the order they run: (gate, what it needs to pass, result otherwise).
GATES = (
    ("processing", "every page processed", "needs_confirmation"),
    ("options", "one option, or the user selected one", "needs_confirmation"),
    ("consumer_type", "user confirmed an individual household",
     "out_of_scope if confirmed RWA or group housing, else needs_confirmation"),
    ("state", "a recognised state or UT", "needs_confirmation"),
    ("portal_date", "application on the National Portal on or after the effective date",
     "out_of_scope if confirmed before, else needs_confirmation"),
    ("prior_subsidy", "user confirmed first system and no prior central subsidy",
     "out_of_scope if confirmed otherwise, else needs_confirmation"),
    ("give_it_up", "no Give It Up opt-out",
     "out_of_scope if the user confirmed it, needs_confirmation if only the quote mentions it"),
    ("subsidy_stated", "a separate central subsidy amount", "needs_confirmation"),
    ("dc_capacity", "a known DC module capacity", "needs_confirmation"),
    ("dc_range", "a range only if both ends give the same capped amount", "needs_confirmation"),
)

CONSISTENT_MESSAGE = ("Matches the central subsidy rate for this panel capacity. Eligibility (DCR panels, "
                      "registration, inspection) is not verified by this check.")
DCR_NOTE = ("The central subsidy also requires DCR (domestic content) panels and cells. "
            "This was not verified from the quote.")
PENDING_NOTE = "Some rule values used here are pending verification against the MNRE sources."
NO_SUBSIDY_MESSAGE = ("This quote doesn't mention the central subsidy. If you plan to apply, ask the vendor "
                      "what they expect it to be.")


def _norm_name(name):
    text = str(name).lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z ]", " ", text).split())


def _date_text(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b %Y}"


def rules_note(rules):
    """Says whether the rule values were verified, and when."""
    if pending_facts(rules) or not rules.get("verified_on"):
        return PENDING_NOTE
    return f"The rule values were checked against the MNRE guidelines on {_date_text(rules['verified_on'])}."


def state_category(state, rules):
    """"special" or "general" for a recognised state or UT, else None. Never defaults to general."""
    if not state:
        return None
    key = _norm_name(state)
    aliases = {_norm_name(a): _norm_name(n) for a, n in rules.get("state_aliases", {}).items()}
    key = aliases.get(key, key)
    for category in ("special", "general"):
        listed = rules[f"{category}_category"]
        if key in {_norm_name(n) for n in listed["states"] + listed["union_territories"]}:
            return category
    return None


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
    """(min_kwp, max_kwp, how, evidence, from_modules) for the DC panel capacity, or None.

    Panel count x wattage when every module group has both (from_modules is
    True); otherwise a stated capacity the quote calls DC. A wattage or
    capacity range stays a range.
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
        return lo_w / 1000, hi_w / 1000, f"({' + '.join(terms)}) / 1000", evidence, True

    stated_f, basis_f = quote.get("stated_capacity_kw"), quote.get("capacity_basis")
    if value_of(basis_f) == "dc_kwp" and stated_f is not None:
        lo, hi = _bounds(value_of(stated_f))
        if isinstance(lo, Decimal) and isinstance(hi, Decimal) and lo > 0:
            evidence += [quoted("stated_capacity_kw", stated_f), quoted("capacity_basis", basis_f)]
            return lo, hi, "stated DC capacity", evidence, False
    return None


def _subsidy_evidence(quote):
    return [quoted(n, quote[n]) for n in ("subsidy_central", "subsidy_state", "subsidy_combined",
                                          "subsidy_unspecified") if quote.get(n) is not None]


def _confirmed(field):
    return field is not None and field.get("provenance") == USER_CONFIRMED


def check_central_subsidy(quote, rules=None):
    rules = rules or load_cfa_rules()
    rule_date = rules["effective_from"]["value"]
    cutoff = _date_text(rule_date)
    tolerance = Decimal(rules["comparison_tolerance"]["inr"])
    notes = [DCR_NOTE, rules_note(rules)]

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

    ct_field = quote.get("consumer_type")
    ct = value_of(ct_field)
    if _confirmed(ct_field) and ct in NON_HOUSEHOLD:
        return result(OUT_OF_SCOPE,
                      "Subsidies for RWAs, group housing and other non-household connections are outside "
                      "what this checker covers.", [quoted("consumer_type", ct_field)])
    if not (_confirmed(ct_field) and ct == HOUSEHOLD):
        return result(NEEDS_CONFIRMATION,
                      "Is this system for an individual household? This check covers individual households "
                      "only, so please confirm that first.",
                      [quoted("consumer_type", ct_field)] if ct is not None else [])

    state = quote.get("state")
    if not state:
        return result(NEEDS_CONFIRMATION,
                      "Which state is the house in? The central subsidy rate depends on it.")
    category = state_category(state, rules)
    if category is None:
        return result(NEEDS_CONFIRMATION,
                      "That state name wasn't recognised. Please pick the state or union territory the house "
                      "is in. The central subsidy rate depends on it.")

    portal = quote.get("portal_application_on_or_after_cutoff")
    if portal is False:
        return result(OUT_OF_SCOPE,
                      f"Applications received on the National Portal before {cutoff} follow earlier rules, "
                      "which are outside what this checker covers.")
    if portal is not True:
        return result(NEEDS_CONFIRMATION,
                      f"Please confirm that your subsidy application was, or will be, made on the National "
                      f"Portal on or after {cutoff}. The rule checked here goes by the application date, "
                      "not the quote date.")

    first, prior = quote.get("first_system"), quote.get("prior_central_subsidy")
    if first is False or prior is True:
        return result(OUT_OF_SCOPE,
                      "Adding to an existing system, or a house that has had central subsidy before, is "
                      "outside what this checker covers.")
    if first is not True or prior is not False:
        return result(NEEDS_CONFIRMATION,
                      "Please confirm that this is the first rooftop solar system at this house and that the "
                      "house hasn't received central subsidy for rooftop solar before.")

    giu_field = quote.get("give_it_up")
    if _confirmed(giu_field):
        if value_of(giu_field) is True:
            return result(OUT_OF_SCOPE,
                          "You've chosen to give up the central subsidy (Give It Up), so there is no subsidy "
                          "amount to check.")
    elif value_of(giu_field) is not None and value_of(giu_field) is not False:
        return result(NEEDS_CONFIRMATION,
                      "The quote mentions Give It Up. Are you giving up the central subsidy? This check "
                      "doesn't decide that from the quote.", [quoted("give_it_up", giu_field)])

    sub_evidence = _subsidy_evidence(quote)
    central_f = quote.get("subsidy_central")
    other = [n for n in ("subsidy_combined", "subsidy_unspecified") if value_of(quote.get(n)) is not None]
    if value_of(central_f) is None and other:
        return result(NEEDS_CONFIRMATION,
                      "The quote gives one subsidy figure without saying how much is central and how much "
                      "is state. Ask the vendor for the central and state amounts separately.",
                      sub_evidence, question="subsidy_breakdown")
    if value_of(central_f) is None:
        return result(NEEDS_CONFIRMATION, NO_SUBSIDY_MESSAGE, sub_evidence, question="subsidy_not_stated")

    dc = dc_capacity(quote)
    if dc is None:
        return result(NEEDS_CONFIRMATION,
                      "The DC capacity of the panels is not known, so the central rule could not be applied.",
                      sub_evidence, question="dc_capacity")
    lo, hi, how, dc_evidence, from_modules = dc
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
    params = {"stated": format_inr(stated), "rule": format_inr(expected), "dc_kwp": dc_text}
    if abs(stated - expected) <= tolerance:
        return result(CONSISTENT, CONSISTENT_MESSAGE, evidence, rule)
    if stated > expected:
        higher = (f"The stated central subsidy Rs {format_inr(stated)} is higher than the central rule "
                  f"for this panel capacity ({dc_text} kWp gives Rs {format_inr(expected)}).")
        gaps = []
        if not from_modules:
            gaps.append("the panel capacity comes from the stated DC capacity, not from a panel count and "
                        "wattage for every panel group")
        elif lo != hi:
            gaps.append("the panel wattage is a range")
        single_option = (value_of(quote.get("multiple_options")) is False
                         or quote.get("selected_option") is not None)
        if not single_option:
            gaps.append("it isn't confirmed that the quote has a single option")
        if not quote.get("processing_complete"):
            gaps.append("not every page was processed")
        if gaps:
            return result(NEEDS_CONFIRMATION,
                          f"{higher} This can't be confirmed yet because {' and '.join(gaps)}. Please check "
                          "the panel details.", evidence, rule, question="subsidy_higher", params=params)
        return result(INCONSISTENT, higher, evidence, rule, question="subsidy_higher", params=params)
    return result(NEEDS_CONFIRMATION,
                  f"The stated central subsidy Rs {format_inr(stated)} is lower than the rule maximum for "
                  f"{dc_text} kWp of panels (Rs {format_inr(expected)}); ask the vendor why.",
                  evidence, rule, question="subsidy_lower", params=params)
