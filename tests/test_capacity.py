from conftest import field

from checks.capacity import DC_KWP_TOLERANCE, check_capacity
from checks.common import STATUSES


def test_matching_capacity(quote):
    result = check_capacity(quote)
    assert result["status"] == "consistent"
    assert result["rule_id"] is None and result["rule_date"] is None
    computed = [e for e in result["evidence"] if e["kind"] == "computed"]
    assert computed == [{"kind": "computed", "name": "dc_kwp", "value": "3.27", "formula": "(6 x 545 W) / 1000"}]


def test_evidence_keeps_source_text_and_page(quote):
    result = check_capacity(quote)
    stated = next(e for e in result["evidence"] if e.get("field") == "stated_capacity_kw")
    assert stated == {
        "kind": "quoted",
        "field": "stated_capacity_kw",
        "value": "3.27",
        "evidence_text": "System size 3.27 kWp",
        "page": 1,
    }


def test_mismatching_capacity(quote):
    quote["stated_capacity_kw"] = field("3.5")
    assert check_capacity(quote)["status"] == "inconsistent"


def test_tolerance_boundary(quote):
    assert str(DC_KWP_TOLERANCE) == "0.01"
    quote["stated_capacity_kw"] = field("3.28")
    assert check_capacity(quote)["status"] == "consistent"
    quote["stated_capacity_kw"] = field("3.2801")
    assert check_capacity(quote)["status"] == "inconsistent"


def test_mixed_module_groups(quote):
    quote["module_groups"] = [
        {"count": field(4), "wattage_w": field(540)},
        {"count": field(2), "wattage_w": field(555)},
    ]
    quote["stated_capacity_kw"] = field("3.27")
    result = check_capacity(quote)
    assert result["status"] == "consistent"
    computed = next(e for e in result["evidence"] if e["kind"] == "computed")
    assert computed["formula"] == "(4 x 540 W + 2 x 555 W) / 1000"


def test_missing_wattage(quote):
    quote["module_groups"][0]["wattage_w"] = field(None)
    result = check_capacity(quote)
    assert result["status"] == "missing"
    assert result["message"] == "Not found on the quote: panel wattage."


def test_null_wattage_field(quote):
    quote["module_groups"][0]["wattage_w"] = None
    assert check_capacity(quote)["status"] == "missing"


def test_no_module_groups(quote):
    quote["module_groups"] = []
    assert check_capacity(quote)["status"] == "missing"


def test_non_positive_or_fractional_count(quote):
    quote["module_groups"][0]["count"] = field(0)
    assert check_capacity(quote)["status"] == "needs_confirmation"
    quote["module_groups"][0]["count"] = field("6.5")
    assert check_capacity(quote)["status"] == "needs_confirmation"


def test_missing_stated_capacity(quote):
    quote["stated_capacity_kw"] = None
    assert check_capacity(quote)["status"] == "missing"


def test_unspecified_basis_not_compared(quote):
    quote["capacity_basis"] = field("unspecified")
    result = check_capacity(quote)
    assert result["status"] == "needs_confirmation"
    assert "3.27 kWp" in result["message"]


def test_ac_basis_not_compared(quote):
    quote["capacity_basis"] = field("ac_kw")
    quote["stated_capacity_kw"] = field("3")
    assert check_capacity(quote)["status"] == "needs_confirmation"


def test_missing_basis_not_compared(quote):
    quote["capacity_basis"] = None
    assert check_capacity(quote)["status"] == "needs_confirmation"


def test_multiple_options_need_selection(quote):
    quote["multiple_options"] = field(True, "Option A / Option B", 3)
    result = check_capacity(quote)
    assert result["status"] == "needs_confirmation"
    assert result["evidence"][0]["page"] == 3
    quote["selected_option"] = "A"
    assert check_capacity(quote)["status"] == "consistent"


def test_status_values_are_allowed(quote):
    assert check_capacity(quote)["status"] in STATUSES


def test_unknown_option_count_needs_confirmation(quote):
    quote["multiple_options"] = None
    r = check_capacity(quote)
    assert r["status"] == "needs_confirmation" and "one option or several" in r["message"]
    quote["multiple_options"] = field(None)
    assert check_capacity(quote)["status"] == "needs_confirmation"
