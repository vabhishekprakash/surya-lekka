"""Finding messages in plain words: no field names, no tolerance, rupee amounts as ₹1,63,350."""

import json
import re
from pathlib import Path

import pytest

from conftest import run_confirmed as run_checks  # checks after the household confirms the numbers

ROOT = Path(__file__).resolve().parent.parent
ANSWERS = {"state": "Telangana", "consumer_type": "individual_household",
           "portal_application_on_or_after_cutoff": True, "first_system": True,
           "prior_central_subsidy": False, "give_it_up": False, "extra_charges_complete": True}


def sample(sid):
    return json.loads((ROOT / "samples" / "cached" / f"{sid}.json").read_text(encoding="utf-8"))["quote"]


def finding(result, check_id):
    return next(f for f in result["findings"] if f["check_id"] == check_id)


def s2(corrections=None, answers=None, charges=True):
    quote = sample("S2")
    if not charges:
        quote["extra_charges"] = []
    return run_checks(quote, {"corrections": corrections or {}, "confirmations": {**ANSWERS, **(answers or {})}})


def test_total_that_is_more_than_its_parts_asks_about_a_missing_charge():
    r = s2({"gross_total": "1,76,350"}, charges=False)
    assert finding(r, "C3_gross_total")["message"] == (
        "Base price ₹1,50,000 + GST ₹13,350 = ₹1,63,350. The quote's total is ₹1,76,350, which is ₹13,000 more. "
        "Is a charge missing from the list?")


def test_total_that_adds_up_names_each_part():
    assert finding(s2(), "C3_gross_total")["message"] == (
        "Base price ₹1,50,000 + GST ₹13,350 + Net meter charges ₹2,500 = ₹1,65,850, the same as the quote's total.")


def test_total_that_is_less_than_its_parts():
    message = finding(s2({"gross_total": "1,60,000"}), "C3_gross_total")["message"]
    assert message.endswith("The quote's total is ₹1,60,000, which is ₹5,850 less. Please check the amounts with "
                            "the vendor.")


def test_net_cost_message():
    assert finding(s2(), "C3_net_cost")["message"] == (
        "Total ₹1,65,850 minus central subsidy ₹85,800 = ₹80,050, the same as the quote's net cost. "
        "Subsidy amounts are taken as stated and are not checked here.")


def test_capacity_messages():
    assert finding(s2(), "C1_capacity")["message"] == (
        "5 panels of 500 W make 2.5 kWp, but the quote's system size is 3 kWp.")
    same = finding(s2({"stated_capacity": "2.5 kWp"}), "C1_capacity")["message"]
    assert same == "5 panels of 500 W make 2.5 kWp, the same as the quote's system size."


def test_missing_and_doubtful_values_are_named_in_words():
    assert finding(s2({"base_price": None}), "C3_gross_total")["message"] == "Not found on the quote: base price."
    message = finding(s2({"extra_charges[E1].included_in_total": "unclear"}), "C3_gross_total")["message"]
    assert message == 'Is "Net meter charges" inside the total? Please answer on the review screen.'
    unsure = finding(s2(answers={"extra_charges_complete": "unclear"}), "C3_gross_total")["message"]
    assert unsure == ("Is every charge on your quote listed? Answer yes on the review screen once it is, "
                      "and the total will be checked.")


FIELD_NAME = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b|\[\d+\]")
AMOUNT = re.compile(r"₹(\d+)")


def all_texts(result):
    for f in result["findings"]:
        yield f["message"]
        yield from f["notes"]
        for e in f["evidence"]:
            if e.get("kind") == "computed":
                yield e["formula"]


@pytest.mark.parametrize("sid", ["S1", "S2", "S3"])
@pytest.mark.parametrize("answers", [ANSWERS, {}])
def test_no_message_shows_field_names_tolerance_or_old_rupee_text(sid, answers):
    r = run_checks(sample(sid), {"confirmations": answers})
    for text in all_texts(r):
        assert not FIELD_NAME.search(text), text
        assert "tolerance" not in text.lower() and "INR" not in text and "Rs " not in text and "Rs." not in text, text
        assert "₹ " not in text, text  # nothing can wrap between the sign and the number


def test_amounts_use_indian_grouping():
    for text in all_texts(s2({"gross_total": "1,76,350"}, charges=False)):
        for m in re.finditer(r"₹\d(?:[\d,]*\d)?", text):
            digits = m.group(0)[1:].replace(",", "")
            from checks.common import format_inr

            assert m.group(0)[1:] == format_inr(int(digits)), text
