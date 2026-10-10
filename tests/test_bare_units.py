"""A bare number in a box that only ever holds watts (the review screen's panel wattage, the
typed form's "Wattage of each panel") is read as watts. Numbers read from the quote are parsed
as before: a bare number there stays ambiguous. Where a unit is still needed, the message names
the unit to add."""

import copy
import json
from pathlib import Path

import pytest

import message_corpus as mc
from api import manual
from checks import run_checks
from checks.parse import parse_capacity

ROOT = Path(__file__).resolve().parent.parent
S4 = json.loads((ROOT / "samples" / "cached" / "S4.json").read_text(encoding="utf-8"))
S4 = S4.get("quote", S4)
HOUSEHOLD = {"state": "Telangana", "consumer_type": "individual_household",
             "portal_application_on_or_after_cutoff": True, "first_system": True, "prior_central_subsidy": False,
             "give_it_up": False}
FORMS = ["500", "500W", "500 W", "500 Wp"]


def by_check(result):
    return {f["check_id"] + (f"/{f['item']}" if f.get("item") else ""): f for f in result["findings"]}


@pytest.mark.parametrize("typed", FORMS)
def test_the_review_screens_wattage_box_reads_a_bare_number_as_watts(typed):
    result = mc.confirmed_run(copy.deepcopy(S4), {"confirmations": HOUSEHOLD,
                                                 "corrections": {"module_groups[G1].wattage": typed}})
    found = by_check(result)
    assert found["C1_capacity"]["status"] == "inconsistent"
    assert found["C1_capacity"]["message_params"]["panels"] == "5 panels of 500 W make 2.5 kWp"
    assert found["C2_central_subsidy"]["message_params"]["rule"] == "₹69,000"


@pytest.mark.parametrize("typed", FORMS)
def test_the_typed_forms_wattage_box_reads_a_bare_number_as_watts(typed):
    fields = {**mc.MANUAL["good"], "panel_wattage": typed, "stated_capacity": "3 kWp", "panel_count": "6"}
    charges, corrections = manual.typed_corrections(fields)
    result = mc.confirmed_run(manual.blank_quote(charges),
                              {"corrections": corrections, "confirmations": {"multiple_options": False, **HOUSEHOLD}})
    assert by_check(result)["C1_capacity"]["message_params"]["panels"] == "6 panels of 500 W make 3 kWp"


def test_numbers_read_from_the_quote_are_parsed_as_before():
    assert parse_capacity("500")["parse_status"] == "ambiguous"
    assert parse_capacity("500 W")["parsed"] == 500 and parse_capacity("3")["parse_status"] == "ambiguous"
    quote = copy.deepcopy(S4)
    quote["module_groups"][0]["wattage"]["value"] = {"raw": "550", "parsed": None, "unit": None,
                                                     "parse_status": "ambiguous"}
    c1 = by_check(run_checks(quote, {"confirmations": HOUSEHOLD}))["C1_capacity"]
    assert c1["status"] == "needs_confirmation"  # a bare "550" on the quote is never taken as watts


def test_a_bare_system_size_or_inverter_rating_names_the_unit_to_add():
    inverter = S4["inverters"][0]["inverter_id"]
    result = run_checks(copy.deepcopy(S4), {"confirmations": HOUSEHOLD, "corrections": {
        "stated_capacity": "3", f"inverters[{inverter}].rating": "3", "module_groups[G1].wattage": "500"}})
    found = by_check(result)
    assert found["C1_capacity"]["message"] == "Please add the unit to the system size, for example 3 kWp."
    assert found["C4_missing_details/inverter_rating"]["message"] == ("Please add the unit to the inverter rating, "
                                                                      "for example 3 kW.")
    assert all("a single number is needed" not in f["message"] for f in result["findings"])


def test_a_range_still_asks_for_a_single_number():
    quote = copy.deepcopy(S4)
    quote["module_groups"][0]["wattage"]["value"] = {"raw": "540-550 W", "parsed": None, "unit": None,
                                                     "parse_status": "ambiguous"}
    assert by_check(run_checks(quote, {"confirmations": HOUSEHOLD}))["C1_capacity"]["message_key"] == "C1.single_number"
