"""Extra charges across batches are matched by label and parsed amount."""

from conftest import run_confirmed as run_checks  # checks after the household confirms the numbers
from extract.dryrun import load_dry_run_wire
from extract.merge import merge_batches
from test_extract_merge import ANSWERS, on_page, record


def merged(change):
    first, second = load_dry_run_wire(), on_page(load_dry_run_wire(), 3)
    change(first["extra_charges"], second["extra_charges"])
    return merge_batches([record(1, [1, 2], first), record(2, [3], second)])


def gross(quote):
    return run_checks(quote, ANSWERS)["findings"][2]


def test_same_label_and_parsed_amount_is_one_charge():
    def change(first, second):
        second[0]["amount"] = "Rs 2500"
    quote = merged(change)
    (charge,) = quote["extra_charges"]
    assert [c["batch"] for c in charge["amount"]["candidates"]] == [1, 2]
    assert quote["flags"]["needs_confirmation"] == [] and gross(quote)["status"] == "consistent"


def test_same_label_different_amount_is_a_conflict():
    def change(first, second):
        second[0]["amount"] = "Rs. 3,000"
    quote = merged(change)
    assert any(c["field"].startswith("extra_charges") for c in quote["flags"]["needs_confirmation"])
    assert gross(quote)["status"] == "needs_confirmation"


def test_conflicting_total_labels_are_flagged_not_first():
    def change(first, second):
        second[0]["total_label"] = "Total payable"
    quote = merged(change)
    (charge,) = quote["extra_charges"]
    assert charge["total_label"] is None
    assert charge["included_in_total"]["conflict"] is True
    (flag,) = quote["flags"]["needs_confirmation"]
    assert flag["field"].endswith(".total_label")
    assert [c["value"] for c in flag["candidates"]] == ["Grand Total", "Total payable"]
    assert gross(quote)["status"] == "needs_confirmation"


def test_two_charges_with_one_label_in_one_batch_are_not_merged_together():
    def change(first, second):
        extra = dict(first[0], amount="Rs. 3,000")
        first.append(extra)  # batch 1 lists the label twice with two amounts
    quote = merged(change)
    assert len(quote["extra_charges"]) == 2
    matched, other = quote["extra_charges"]
    assert matched["amount"]["value"]["raw"] == "Rs. 2,500" and len(matched["amount"]["candidates"]) == 2
    assert other["amount"]["conflict"] is True
