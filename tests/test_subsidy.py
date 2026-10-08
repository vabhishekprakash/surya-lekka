import copy
import json
from decimal import Decimal

import pytest
from conftest import amount_field, capacity_field, field_v1

from checks.contract import normalise
from checks.subsidy import (
    DCR_NOTE,
    GATES,
    PENDING_NOTE,
    cfa_amount,
    check_central_subsidy,
    rule_for,
    state_category,
)
from rules import load_cfa_rules, pending_facts

RULES = load_cfa_rules()
GENERAL = rule_for("general", RULES)
SPECIAL = rule_for("special", RULES)


def c2(quote_v1):
    return check_central_subsidy(normalise(quote_v1))


def set_modules(q, count, watt):
    q["module_groups"][0]["count"] = field_v1(count, f"No. of panels: {count}")
    q["module_groups"][0]["wattage"] = capacity_field(watt)


# --- rule arithmetic ----------------------------------------------------------------

@pytest.mark.parametrize("dc,general,special", [
    ("1", "30000", "33000"),
    ("2", "60000", "66000"),
    ("2.5", "69000", "75900"),
    ("2.825", "74850", "82335"),
    ("3", "78000", "85800"),
    ("3.3", "78000", "85800"),
    ("10", "78000", "85800"),
    ("0", "0", "0"),
])
def test_cfa_amounts(dc, general, special):
    assert cfa_amount(Decimal(dc), GENERAL) == Decimal(general)
    assert cfa_amount(Decimal(dc), SPECIAL) == Decimal(special)


def test_state_categories():
    assert state_category("Telangana", RULES) == "general"
    assert state_category("assam", RULES) == "special"
    assert state_category("J&K", RULES) == "special"
    assert state_category("Andaman & Nicobar Islands", RULES) == "special"
    assert state_category("Orissa", RULES) == "general"
    assert state_category("NCT of Delhi", RULES) == "general"


@pytest.mark.parametrize("name", ["Telangna", "India", "Hyderabad", "", None])
def test_unrecognised_state_has_no_category(name):
    assert state_category(name, RULES) is None


def test_every_state_and_ut_listed_once():
    special, general = RULES["special_category"], RULES["general_category"]
    assert special["states"] + special["union_territories"] == [
        "Arunachal Pradesh", "Assam", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Sikkim", "Tripura",
        "Himachal Pradesh", "Uttarakhand", "Jammu and Kashmir", "Ladakh", "Andaman and Nicobar Islands",
        "Lakshadweep"]
    assert len(special["states"]) + len(general["states"]) == 28
    assert len(special["union_territories"]) + len(general["union_territories"]) == 8
    names = special["states"] + special["union_territories"] + general["states"] + general["union_territories"]
    assert len(set(names)) == 36
    for n in special["states"] + special["union_territories"]:
        assert state_category(n, RULES) == "special"
    for n in general["states"] + general["union_territories"]:
        assert state_category(n, RULES) == "general"


# --- rules file ------------------------------------------------------------------------

def test_rules_file_facts_are_sourced_and_verified():
    assert RULES["verification"] == "verified" and RULES["verified_on"] == "2026-10-08"
    sources = {s["source_id"]: s for s in RULES["sources"]}
    assert set(sources) == {"MNRE-CFA-OG-2024", "MNRE-CFA-AMEND-2025-07-07", "PIB-2042617"}
    assert sources["MNRE-CFA-OG-2024"]["date"] == "2024-06-07"
    assert sources["MNRE-CFA-AMEND-2025-07-07"]["date"] == "2025-07-07"
    assert "not modelled" in sources["MNRE-CFA-AMEND-2025-07-07"]["note"]
    assert sources["PIB-2042617"]["url"] == "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2042617"
    assert sources["PIB-2042617"]["role"] == "corroborating"
    assert all(s["url"].startswith("https://") and s["title"] for s in sources.values())
    eff = RULES["effective_from"]
    assert eff["value"] == "2024-02-13" and eff["applies_to"] == "application received on the National Portal"
    assert eff["sections"] == ["2(c)"]
    facts = RULES["rules"] + [eff, RULES["special_category"], RULES["general_category"]]
    for fact in list(sources.values()) + facts:
        assert fact["verification"] == "verified" and fact["verified_on"] == "2026-10-08"
    for fact in facts:
        assert set(fact["source_ids"]) <= set(sources) and fact["source_ids"] and fact["sections"]
    assert {r["rule_id"]: r["sections"] for r in RULES["rules"]} == {
        "CFA-RES-GENERAL": ["5(h)", "5(k)"], "CFA-RES-SPECIAL": ["5(h)", "5(k)"], "CFA-DCR-REQUIRED": ["5(m)"]}
    assert RULES["special_category"]["sections"] == ["5(g)"]
    assert pending_facts(RULES) == []


def test_tolerance_is_product_policy():
    tol = RULES["comparison_tolerance"]
    assert tol["inr"] == "1" and tol["basis"] == "product_policy"
    assert "not an official allowance" in tol["note"]


def test_rules_note_follows_verification(quote_v1):
    assert c2(quote_v1)["notes"] == [
        DCR_NOTE, "The rule values were checked against the MNRE guidelines on 8 Oct 2026."]
    rules = copy.deepcopy(RULES)
    rules["special_category"]["verification"] = "pending"
    assert pending_facts(rules) == ["CFA-SPECIAL-CATEGORY-LIST"]
    assert PENDING_NOTE in check_central_subsidy(normalise(quote_v1), rules)["notes"]


def test_tolerance_read_from_rules(quote_v1):
    quote_v1["subsidy_central"] = amount_field("Rs. 77,999")
    rules = copy.deepcopy(RULES)
    rules["comparison_tolerance"]["inr"] = "0"
    assert check_central_subsidy(normalise(quote_v1), rules)["status"] == "needs_confirmation"


# --- gates, in order -------------------------------------------------------------------

def test_gate_order():
    assert [g[0] for g in GATES] == [
        "processing", "options", "consumer_type", "state", "portal_date", "prior_subsidy", "give_it_up",
        "subsidy_stated", "dc_capacity", "dc_range"]


def test_consistent(quote_v1):
    r = c2(quote_v1)  # 6 x 545 W = 3.27 kWp, general category, Rs 78,000 stated
    assert r["status"] == "consistent"
    assert r["message"] == ("Matches the central subsidy rate for this panel capacity. Eligibility (DCR panels, "
                            "registration, inspection) is not verified by this check.")
    assert r["rule_id"] == "CFA-RES-GENERAL" and r["rule_date"] == "2024-02-13"
    assert DCR_NOTE in r["notes"]
    assert r["question"] is None
    json.dumps(r)


def test_processing_incomplete_first(quote_v1):
    quote_v1["processing_complete"] = False
    quote_v1["flags"]["user_confirmed"]["consumer_type"] = None
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and "processed" in r["message"]


def test_options_unselected(quote_v1):
    quote_v1["flags"]["model_proposed"]["multiple_options"] = field_v1(True, "Option A / Option B")
    quote_v1["flags"]["user_confirmed"]["consumer_type"] = None
    assert "more than one option" in c2(quote_v1)["message"]


@pytest.mark.parametrize("ct", ["rwa", "group_housing", "other_non_household"])
def test_confirmed_non_household_out_of_scope(quote_v1, ct):
    quote_v1["flags"]["user_confirmed"]["consumer_type"] = ct
    quote_v1["flags"]["user_confirmed"]["state"] = None  # consumer type is checked before state
    r = c2(quote_v1)
    assert r["status"] == "out_of_scope"
    assert "outside what this checker covers" in r["message"]
    assert "ineligible" not in r["message"].lower() and "not eligible" not in r["message"].lower()


@pytest.mark.parametrize("confirmed", [None, "commercial"])
def test_unknown_consumer_type(quote_v1, confirmed):
    quote_v1["flags"]["user_confirmed"]["consumer_type"] = confirmed
    quote_v1["flags"]["user_confirmed"]["state"] = None
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and "individual household" in r["message"]


def test_model_proposed_consumer_type_is_not_enough(quote_v1):
    quote_v1["flags"]["user_confirmed"]["consumer_type"] = None
    for value in ("rwa", "individual_household"):
        quote_v1["flags"]["model_proposed"]["consumer_type"] = field_v1(value, "Quotation for", 1)
        r = c2(quote_v1)
        assert r["status"] == "needs_confirmation"
        assert r["evidence"][0]["kind"] == "quoted" and r["evidence"][0]["value"] == value


def test_state_not_confirmed(quote_v1):
    quote_v1["flags"]["user_confirmed"]["state"] = None
    quote_v1["flags"]["user_confirmed"]["portal_application_on_or_after_cutoff"] = False
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and "Which state is the house in?" in r["message"]


@pytest.mark.parametrize("state", ["Telangna", "Hyderabad", "India"])
def test_unrecognised_state_never_defaults_to_general(quote_v1, state):
    quote_v1["flags"]["user_confirmed"]["state"] = state
    quote_v1["flags"]["user_confirmed"]["portal_application_on_or_after_cutoff"] = False
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and "wasn't recognised" in r["message"]
    assert r["rule_id"] is None


def test_portal_date_not_confirmed(quote_v1):
    quote_v1["flags"]["user_confirmed"]["portal_application_on_or_after_cutoff"] = None
    quote_v1["flags"]["user_confirmed"]["first_system"] = None
    quote_v1["subsidy_central"] = None
    quote_v1["subsidy_combined"] = amount_field("Rs. 88,000")
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and "13 Feb 2024" in r["message"]
    assert "National Portal" in r["message"] and "not the quote date" in r["message"]


def test_portal_before_cutoff_out_of_scope(quote_v1):
    quote_v1["flags"]["user_confirmed"]["portal_application_on_or_after_cutoff"] = False
    quote_v1["flags"]["user_confirmed"]["first_system"] = None
    r = c2(quote_v1)
    assert r["status"] == "out_of_scope" and "outside what this checker covers" in r["message"]


@pytest.mark.parametrize("first,prior", [(None, False), (True, None), (None, None)])
def test_prior_subsidy_not_confirmed(quote_v1, first, prior):
    quote_v1["flags"]["user_confirmed"]["first_system"] = first
    quote_v1["flags"]["user_confirmed"]["prior_central_subsidy"] = prior
    quote_v1["flags"]["user_confirmed"]["give_it_up"] = True  # prior subsidy is checked first
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and "first rooftop solar system" in r["message"]


@pytest.mark.parametrize("first,prior", [(False, False), (True, True), (False, None), (None, True)])
def test_existing_system_or_prior_subsidy_out_of_scope(quote_v1, first, prior):
    quote_v1["flags"]["user_confirmed"]["first_system"] = first
    quote_v1["flags"]["user_confirmed"]["prior_central_subsidy"] = prior
    r = c2(quote_v1)
    assert r["status"] == "out_of_scope" and "outside what this checker covers" in r["message"]


def test_give_it_up_confirmed_out_of_scope(quote_v1):
    quote_v1["flags"]["user_confirmed"]["give_it_up"] = True
    quote_v1["subsidy_central"] = None
    r = c2(quote_v1)
    assert r["status"] == "out_of_scope" and "Give It Up" in r["message"]


def test_give_it_up_text_alone_needs_confirmation(quote_v1):
    quote_v1["flags"]["user_confirmed"]["give_it_up"] = None
    quote_v1["flags"]["model_proposed"]["give_it_up"] = field_v1(True, "Give It Up option available", 2)
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and "Give It Up" in r["message"]
    assert r["evidence"][0]["kind"] == "quoted"
    quote_v1["flags"]["user_confirmed"]["give_it_up"] = False
    assert c2(quote_v1)["status"] == "consistent"


def test_give_it_up_not_mentioned_continues(quote_v1):
    quote_v1["flags"]["user_confirmed"]["give_it_up"] = None
    assert c2(quote_v1)["status"] == "consistent"


@pytest.mark.parametrize("name", ["subsidy_combined", "subsidy_unspecified"])
def test_combined_or_unspecified_subsidy(quote_v1, name):
    quote_v1["subsidy_central"] = None
    quote_v1[name] = amount_field("Rs. 78,000")
    quote_v1["module_groups"] = []
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and r["question"] == "subsidy_breakdown"


def test_central_alongside_combined_is_compared(quote_v1):
    quote_v1["subsidy_combined"] = amount_field("Rs. 88,000")
    assert c2(quote_v1)["status"] == "consistent"


def test_dc_unknown(quote_v1):
    quote_v1["module_groups"][0]["wattage"] = None
    quote_v1["flags"]["model_proposed"]["capacity_basis"] = field_v1("unspecified", "3.27 kW")
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and r["question"] == "dc_capacity"


def test_dc_falls_back_to_stated_dc(quote_v1):
    quote_v1["module_groups"][0]["wattage"] = None
    r = c2(quote_v1)
    assert r["status"] == "consistent"
    assert any(e.get("field") == "stated_capacity_kw" for e in r["evidence"])


def test_ac_rating_never_used_as_dc(quote_v1):
    quote_v1["module_groups"] = []
    quote_v1["flags"]["model_proposed"]["capacity_basis"] = field_v1("ac_kw", "3 kW inverter")
    assert c2(quote_v1)["question"] == "dc_capacity"


def test_range_with_different_amounts(quote_v1):
    set_modules(quote_v1, 4, "540-560 Wp")  # 2.16 to 2.24 kWp
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and r["question"] == "exact_capacity"
    assert "62,880" in r["message"] and "64,320" in r["message"]


def test_range_with_same_amount_continues(quote_v1):
    set_modules(quote_v1, 6, "595-600Wp")  # 3.57 to 3.6 kWp, both capped
    r = c2(quote_v1)
    assert r["status"] == "consistent"
    assert any(e.get("name") == "dc_kwp_range" for e in r["evidence"])


def test_fraction_prorated(quote_v1):
    set_modules(quote_v1, 5, "565 Wp")  # 2.825 kWp
    quote_v1["subsidy_central"] = amount_field("Rs. 74,850")
    assert c2(quote_v1)["status"] == "consistent"


def test_within_one_rupee(quote_v1):
    quote_v1["subsidy_central"] = amount_field("Rs. 77,999")
    assert c2(quote_v1)["status"] == "consistent"
    quote_v1["subsidy_central"] = amount_field("Rs. 77,998.99")
    assert c2(quote_v1)["status"] == "needs_confirmation"


def test_higher_is_inconsistent(quote_v1):
    set_modules(quote_v1, 5, "500 W")
    quote_v1["subsidy_central"] = amount_field("Rs. 85,800")
    r = c2(quote_v1)
    assert r["status"] == "inconsistent"
    assert "higher than the central rule for this panel capacity" in r["message"]
    assert r["question"] == "subsidy_higher"
    assert r["question_params"] == {"stated": "85,800", "rule": "69,000", "dc_kwp": "2.5"}


def test_lower_needs_confirmation(quote_v1):
    quote_v1["subsidy_central"] = amount_field("Rs. 60,000")
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation"
    assert "lower than the rule maximum" in r["message"] and "ask the vendor why" in r["message"]


def test_special_category(quote_v1):
    quote_v1["flags"]["user_confirmed"]["state"] = "Sikkim"
    quote_v1["subsidy_central"] = amount_field("Rs. 85,800")
    r = c2(quote_v1)
    assert r["status"] == "consistent" and r["rule_id"] == "CFA-RES-SPECIAL"


def test_no_central_subsidy_stated(quote_v1):
    quote_v1["subsidy_central"] = None
    quote_v1["module_groups"] = []  # no subsidy is checked before capacity
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation"
    assert r["message"] == ("This quote doesn't mention the central subsidy. If you plan to apply, "
                            "ask the vendor what they expect it to be.")
    assert r["question"] == "subsidy_not_stated"
    quote_v1["subsidy_state"] = amount_field("Rs. 10,000")
    assert c2(quote_v1)["question"] == "subsidy_not_stated"


def test_unparsed_central_subsidy(quote_v1):
    quote_v1["subsidy_central"] = amount_field("Rs. 78,000 approx")
    assert c2(quote_v1)["status"] == "needs_confirmation"


def test_dcr_never_inferred(quote_v1):
    quote_v1["dcr_declaration"] = None
    r = c2(quote_v1)
    assert r["status"] == "consistent" and DCR_NOTE in r["notes"]


# --- higher than the rule needs complete capacity evidence -------------------------

def test_higher_with_stated_dc_only_needs_confirmation(quote_v1):
    quote_v1["module_groups"][0]["wattage"] = None  # falls back to the stated 3.27 kWp DC
    quote_v1["subsidy_central"] = amount_field("Rs. 85,800")
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and "stated DC capacity" in r["message"]
    assert r["question"] == "subsidy_higher"


def test_higher_with_one_group_incomplete_needs_confirmation(quote_v1):
    second = copy.deepcopy(quote_v1["module_groups"][0])
    second["group_id"], second["wattage"] = "G2", None
    quote_v1["module_groups"].append(second)
    quote_v1["subsidy_central"] = amount_field("Rs. 85,800")
    assert c2(quote_v1)["status"] == "needs_confirmation"


def test_higher_with_wattage_range_needs_confirmation(quote_v1):
    set_modules(quote_v1, 6, "595-600Wp")  # both ends capped at Rs 78,000
    quote_v1["subsidy_central"] = amount_field("Rs. 85,800")
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and "range" in r["message"]


def test_higher_with_option_count_unknown_needs_confirmation(quote_v1):
    quote_v1["flags"]["model_proposed"]["multiple_options"] = None
    quote_v1["subsidy_central"] = amount_field("Rs. 85,800")
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and "single option" in r["message"]


def test_higher_with_selected_option_is_inconsistent(quote_v1):
    quote_v1["flags"]["model_proposed"]["multiple_options"] = field_v1(True, "Option A / Option B")
    quote_v1["flags"]["user_confirmed"]["selected_option"] = "A"
    quote_v1["subsidy_central"] = amount_field("Rs. 85,800")
    assert c2(quote_v1)["status"] == "inconsistent"
