import pytest

from checks.evidence import verify_evidence

PAGES = {
    1: "Example Solar Pvt Ltd\nNo. of  panels:\n6\nSystem size 3.27 kWp",
    2: "Base price Rs. 1,80,000\nModel EXM–545",
    3: None,
    4: "   ",
}


def f(text, page):
    return {"value": "x", "evidence_text": text, "page": page, "batch": 1}


@pytest.mark.parametrize("text,page", [
    ("No. of panels: 6", 1),
    ("system size 3.27 KWP", 1),
    ("Base price Rs. 1,80,000", 2),
    ("Model EXM-545", 2),
    ("Base price  Rs.​ 1,80,000", 2),
])
def test_text_matched(text, page):
    assert verify_evidence(f(text, page), PAGES) == "text_matched"


def test_mismatch_on_wrong_page_or_text():
    assert verify_evidence(f("Base price Rs. 1,80,000", 1), PAGES) == "mismatch"
    assert verify_evidence(f("Base price Rs. 1,90,000", 2), PAGES) == "mismatch"


@pytest.mark.parametrize("field", [
    f("anything", 3),          # image-only page
    f("anything", 4),          # blank text layer
    f("anything", 9),          # page not supplied
    f("anything", None),
    f(None, 1),
    f("   ", 1),
    None,
])
def test_not_machine_verified(field):
    assert verify_evidence(field, PAGES) == "not_machine_verified"


def test_list_of_pages_and_string_keys():
    assert verify_evidence(f("Base price Rs. 1,80,000", 2), [PAGES[1], PAGES[2]]) == "text_matched"
    assert verify_evidence(f("Base price Rs. 1,80,000", 2), {"2": PAGES[2]}) == "text_matched"
