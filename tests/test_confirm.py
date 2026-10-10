"""Confirmation before any finding: no check says "matches" or "doesn't match" until the
household has confirmed the exact operand set it uses (field, option and value). Any change to
them clears the confirmation; a correction is never a confirmation by itself."""

import copy

import pytest

from api import manual
from checks import run_checks
from extract import textract_client as tc
from extract.merge import merge_batches
from textract_pages import Page

DEFINITIVE = ("consistent", "inconsistent")
ANSWERS = {"state": "Telangana", "consumer_type": "individual_household", "portal_application_on_or_after_cutoff": True,
           "first_system": True, "prior_central_subsidy": False, "give_it_up": False, "multiple_options": False,
           "capacity_basis": "dc_kwp", "gst_treatment": "excluded", "extra_charges_complete": True,
           "net_cost_subsidy_basis": "central"}
TYPED = {"stated_capacity": "3.3 kWp", "panel_count": "6", "panel_wattage": "550 Wp", "base_price": "1,50,000",
         "gst_amount": "18,000", "gross_total": "1,70,000", "subsidy_central": "78,000", "net_cost": "92,000"}


def typed_quote(fields=TYPED):
    charges, corrections = manual.typed_corrections(fields)
    return manual.blank_quote(charges), corrections


def run(quote, corrections=None, confirmations=ANSWERS, **tokens):
    return run_checks(copy.deepcopy(quote), {"corrections": corrections or {}, "confirmations": confirmations,
                                             **tokens})


def by_id(result):
    return {(f["check_id"], f.get("item")): f for f in result["findings"]}


def tokens(result):
    return [f["confirm_token"] for f in result["findings"] if f.get("confirm_token")]


def test_no_check_is_definitive_before_its_numbers_are_confirmed():
    quote, corrections = typed_quote()
    held = run(quote, corrections)
    assert not [f for f in held["findings"] if f["status"] in DEFINITIVE]
    gross = by_id(held)[("C3_gross_total", None)]
    assert gross["status"] == "needs_confirmation" and gross["message"] == "Are these the numbers on your quote?"
    assert gross["message_key"] == "confirm.operands" and gross["operands_confirmed"] is False
    assert {o["field"] for o in gross["operands"]} == {"gross_total", "base_price", "gst_amount"}
    assert all(o["source"] == "you typed this" for o in gross["operands"])


def test_a_held_finding_asks_the_vendor_nothing_yet():
    quote, corrections = typed_quote()
    held = run(quote, corrections)
    assert held["questions"] == [] or "total_mismatch" not in [q["id"] for q in held["questions"]]
    released = run(quote, corrections, confirmed_operands=tokens(held))
    assert "total_mismatch" in [q["id"] for q in released["questions"]]


def test_confirming_the_shown_operands_gives_the_result():
    quote, corrections = typed_quote()
    held = run(quote, corrections)
    result = by_id(run(quote, corrections, confirmed_operands=tokens(held)))
    assert result[("C3_gross_total", None)]["status"] == "inconsistent"  # 1,50,000 + 18,000 is not 1,70,000
    assert result[("C1_capacity", None)]["status"] == "consistent"
    assert result[("C3_gross_total", None)]["operands_confirmed"] is True


def test_confirming_one_check_releases_only_that_check():
    quote, corrections = typed_quote()
    held = by_id(run(quote, corrections))
    one = [held[("C1_capacity", None)]["confirm_token"]]
    result = by_id(run(quote, corrections, confirmed_operands=one))
    assert result[("C1_capacity", None)]["status"] == "consistent"
    assert result[("C3_gross_total", None)]["status"] == "needs_confirmation"


@pytest.mark.parametrize("change", [{"base_price": "1,52,000"},  # another number
                                    {"base_price": "150000"},  # a reformat that parses to the same number
                                    {"base_price": "Rs. 1,50,000/-"}])
def test_a_correction_after_confirming_clears_the_confirmation(change):
    quote, corrections = typed_quote()
    confirmed = tokens(run(quote, corrections))
    result = by_id(run(quote, {**corrections, **change}, confirmed_operands=confirmed))
    assert result[("C3_gross_total", None)]["status"] == "needs_confirmation"
    assert result[("C1_capacity", None)]["status"] == "consistent"  # it doesn't use the base price


def test_a_correction_of_a_read_value_is_never_a_confirmation_by_itself():
    page = Page()
    page.line("Base price Rs 1,00,000", 0.1)
    page.line("GST Rs 18,000", 0.2)
    page.line("Grand total Rs 1,18,000", 0.3)
    wire, _ = tc.map_page(page.reply(), 1)
    quote = merge_batches([{"batch": 1, "pages": [1], "model_id": "textract", "contract": tc.to_contract(wire, 1)}])
    read = run(quote)
    gross = by_id(read)[("C3_gross_total", None)]
    assert gross["status"] == "needs_confirmation"
    assert {o["source"]["text"] for o in gross["operands"]} == {"Base price Rs 1,00,000", "GST Rs 18,000",
                                                                "Grand total Rs 1,18,000"}
    corrected = run(quote, {"gst_amount": "1,00,000"})  # case 33: in range, wrong field
    assert by_id(corrected)[("C3_gross_total", None)]["status"] == "needs_confirmation"
    corrected = run(quote, {"gst_amount": "18000.00"}, confirmed_operands=tokens(read))  # case 31: reformat
    assert by_id(corrected)[("C3_gross_total", None)]["status"] == "needs_confirmation"


def option_quote():
    page = Page()
    page.line("Base price Rs 1,00,000", 0.1)
    page.table([["Option", "Capacity", "Total price (Rs)"], ["A", "3 kW", "1,00,000"], ["B", "5 kW", "1,10,000"]])
    wire, _ = tc.map_page(page.reply(), 1)
    return merge_batches([{"batch": 1, "pages": [1], "model_id": "textract", "contract": tc.to_contract(wire, 1)}])


def option_answers(option):
    return {**ANSWERS, "gst_treatment": "included", "multiple_options": True, "selected_option": option}


def bound(quote, revision, **inputs):
    """run_checks as the API runs it: tokens signed for one job and its review revision."""
    return run_checks(copy.deepcopy(quote), {"corrections": {}, **inputs},
                      binding={"key": b"test key", "job": "job-1", "revision": revision})


def test_every_option_switch_clears_the_confirmation_even_back_to_the_first():
    quote = option_quote()
    A, B = sorted(quote["option_fields"])  # the option ids this table gives
    a_tokens = tokens(bound(quote, 1, confirmations=option_answers(A)))
    assert by_id(bound(quote, 1, confirmations=option_answers(A), confirmed_operands=a_tokens))[
        ("C3_gross_total", None)]["status"] == "consistent"
    # The API moves the revision on with every change of answers, the option included.
    b = by_id(bound(quote, 2, confirmations=option_answers(B), confirmed_operands=a_tokens))
    assert b[("C3_gross_total", None)]["status"] == "needs_confirmation"
    back = by_id(bound(quote, 3, confirmations=option_answers(A), confirmed_operands=a_tokens))
    assert back[("C3_gross_total", None)]["status"] == "needs_confirmation"  # A to B to A asks again


def test_tokens_bind_to_the_job_and_the_key():
    quote, corrections = typed_quote()
    signed = run_checks(quote, {"corrections": corrections, "confirmations": ANSWERS},
                        binding={"key": b"k", "job": "job-1", "revision": 0})
    for binding in ({"key": b"k", "job": "job-2", "revision": 0}, {"key": b"other", "job": "job-1", "revision": 0},
                    {"key": b"k", "job": "job-1", "revision": 1}, None):
        result = run_checks(quote, {"corrections": corrections, "confirmations": ANSWERS,
                                    "confirmed_operands": tokens(signed)}, binding=binding)
        assert not [f for f in result["findings"] if f["status"] in DEFINITIVE], binding


def test_a_value_tick_does_not_follow_an_option_switch():
    quote = option_quote()
    for f in quote["option_fields"].values():  # both totals read with low confidence
        f["gross_total"]["confidence"] = 60.0
        for c in f["gross_total"]["candidates"]:
            c["confidence"] = 60.0
    A, B = sorted(quote["option_fields"])
    a = run(quote, confirmations=option_answers(A))
    tick = [c["token"] for c in a["check_this"] if c["path"] == "gross_total"]
    assert tick
    b = run(quote, confirmations=option_answers(B), verified=tick)
    assert "gross_total" in [c["path"] for c in b["check_this"]]
    b = run(quote, confirmations=option_answers(B), verified=tick, confirmed_operands=tokens(b))
    assert by_id(b)[("C3_gross_total", None)]["status"] == "needs_confirmation"


def test_tokens_must_be_a_list_of_strings():
    quote, corrections = typed_quote()
    for bad in ("x", [1], {"a": 1}):
        with pytest.raises(TypeError):
            run(quote, corrections, confirmed_operands=bad)
