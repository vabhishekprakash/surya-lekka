import json
from decimal import Decimal

import pytest
from conftest import amount_field, capacity_field, field_v1

from checks.contract import normalise
from checks.subsidy import (
    DCR_NOTE,
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


# --- rules file ------------------------------------------------------------------------

def test_rules_file_facts_are_sourced_and_pending():
    assert RULES["verification"] == "pending"
    assert RULES["effective_from"]["value"] == "2024-02-13"
    assert "portal" in RULES["effective_from"]["applies_to"]
    source_ids = {s["source_id"] for s in RULES["sources"]}
    assert all(s["url"].startswith("https://") and s["title"] for s in RULES["sources"])
    for fact in RULES["rules"] + [RULES["effective_from"], RULES["special_category"]]:
        assert fact["verification"] == "pending"
        assert set(fact["source_ids"]) <= source_ids and fact["source_ids"]
    assert {r["rule_id"] for r in RULES["rules"]} == {"CFA-RES-GENERAL", "CFA-RES-SPECIAL", "CFA-DCR-REQUIRED"}
    assert len(pending_facts(RULES)) == 7


# --- gates, in order -------------------------------------------------------------------

def test_consistent(quote_v1):
    r = c2(quote_v1)  # 6 x 545 W = 3.27 kWp, general category, Rs 78,000 stated
    assert r["status"] == "consistent"
    assert r["rule_id"] == "CFA-RES-GENERAL" and r["rule_date"] == "2024-02-13"
    assert DCR_NOTE in r["notes"]
    assert r["question"] is None
    json.dumps(r)


def test_processing_incomplete_first(quote_v1):
    quote_v1["processing_complete"] = False
    quote_v1["flags"]["user_confirmed"]["state"] = None
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and "processed" in r["message"]


def test_options_unselected(quote_v1):
    quote_v1["flags"]["model_proposed"]["multiple_options"] = field_v1(True, "Option A / Option B")
    quote_v1["flags"]["user_confirmed"]["state"] = None
    assert "more than one option" in c2(quote_v1)["message"]


def test_state_not_confirmed(quote_v1):
    quote_v1["flags"]["user_confirmed"]["state"] = None
    quote_v1["flags"]["user_confirmed"]["consumer_type"] = "rwa"
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and "Which state is the house in?" in r["message"]


@pytest.mark.parametrize("ct", ["rwa", "group_housing", "other_non_household"])
def test_non_household_out_of_scope(quote_v1, ct):
    quote_v1["flags"]["user_confirmed"]["consumer_type"] = ct
    quote_v1["flags"]["user_confirmed"]["portal_application_on_or_after_cutoff"] = None
    assert c2(quote_v1)["status"] == "out_of_scope"


def test_model_proposed_rwa_out_of_scope(quote_v1):
    quote_v1["flags"]["user_confirmed"]["consumer_type"] = None
    quote_v1["flags"]["model_proposed"]["consumer_type"] = field_v1("rwa", "Quotation for ABC RWA", 1)
    assert c2(quote_v1)["status"] == "out_of_scope"


def test_portal_date_not_confirmed(quote_v1):
    quote_v1["flags"]["user_confirmed"]["portal_application_on_or_after_cutoff"] = None
    quote_v1["subsidy_central"] = None
    quote_v1["subsidy_combined"] = amount_field("Rs. 88,000")
    r = c2(quote_v1)
    assert r["status"] == "needs_confirmation" and "13 Feb 2024" in r["message"]


def test_portal_before_cutoff_out_of_scope(quote_v1):
    quote_v1["flags"]["user_confirmed"]["portal_application_on_or_after_cutoff"] = False
    assert c2(quote_v1)["status"] == "out_of_scope"


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
    r = c2(quote_v1)
    assert r["status"] == "missing" and "78,000" in r["message"]


def test_unparsed_central_subsidy(quote_v1):
    quote_v1["subsidy_central"] = amount_field("Rs. 78,000 approx")
    assert c2(quote_v1)["status"] == "needs_confirmation"


def test_dcr_never_inferred(quote_v1):
    quote_v1["dcr_declaration"] = None
    r = c2(quote_v1)
    assert r["status"] == "consistent" and DCR_NOTE in r["notes"]
