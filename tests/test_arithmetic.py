import json
from pathlib import Path

from conftest import field

from checks import run_checks
from checks.arithmetic import check_gross_total, check_net_cost


def test_gross_total_consistent(quote):
    result = check_gross_total(quote)
    assert result["status"] == "consistent"
    assert result["rule_id"] is None and result["rule_date"] is None
    computed = [e for e in result["evidence"] if e["kind"] == "computed"]
    assert computed[0]["value"] == "197000"
    assert all(e["kind"] == "quoted" for e in result["evidence"][:-1])


def test_already_parsed_indian_amount(quote):
    quote["gross_total"] = field("197000.00", "Total Rs. 1,97,000/-", 2)
    result = check_gross_total(quote)
    assert result["status"] == "consistent"
    gross = next(e for e in result["evidence"] if e.get("field") == "gross_total")
    assert gross["evidence_text"] == "Total Rs. 1,97,000/-" and gross["page"] == 2


def test_unparsed_amount_is_not_parsed(quote):
    quote["gross_total"] = field("1,97,000")
    assert check_gross_total(quote)["status"] == "needs_confirmation"


def test_tolerance_boundary(quote):
    quote["gross_total"] = field("197001")
    assert check_gross_total(quote)["status"] == "consistent"
    quote["gross_total"] = field("196999")
    assert check_gross_total(quote)["status"] == "consistent"
    quote["gross_total"] = field("197001.01")
    assert check_gross_total(quote)["status"] == "inconsistent"


def test_explicit_zero_is_valid(quote):
    quote["discount"] = field(0)
    quote["gross_total"] = field(198520)
    assert check_gross_total(quote)["status"] == "consistent"


def test_missing_discount_is_not_zero(quote):
    quote["discount"] = None
    quote["gross_total"] = field(198520)
    result = check_gross_total(quote)
    assert result["status"] == "missing"
    assert "discount" in result["message"]


def test_empty_extras_without_guarantee(quote):
    quote["extra_charges"] = []
    quote["extra_charges_complete"] = None
    quote["gross_total"] = field(194500)
    assert check_gross_total(quote)["status"] == "needs_confirmation"


def test_empty_extras_with_guarantee(quote):
    quote["extra_charges"] = []
    quote["gross_total"] = field(194500)
    assert check_gross_total(quote)["status"] == "consistent"


def test_extra_charge_without_amount(quote):
    quote["extra_charges"].append({"label": "Structure", "amount": field(None)})
    assert check_gross_total(quote)["status"] == "missing"


def test_gst_included_not_added_again(quote):
    quote["gst_treatment"] = field("included", "Price inclusive of GST", 2)
    quote["base_price"] = field(196020)
    result = check_gross_total(quote)
    assert result["status"] == "consistent"
    assert "gst_amount" not in result["evidence"][-1]["formula"]


def test_gst_excluded_added(quote):
    quote["gst_amount"] = field(None)
    assert check_gross_total(quote)["status"] == "missing"


def test_gst_treatment_unknown(quote):
    quote["gst_treatment"] = None
    assert check_gross_total(quote)["status"] == "needs_confirmation"


def test_gross_mismatch(quote):
    quote["gross_total"] = field(200000)
    assert check_gross_total(quote)["status"] == "inconsistent"


def test_net_cost_consistent(quote):
    result = check_net_cost(quote)
    assert result["status"] == "consistent"
    assert "not checked" in result["message"]


def test_net_cost_mismatch(quote):
    quote["net_cost"] = field(120000)
    assert check_net_cost(quote)["status"] == "inconsistent"


def test_ambiguous_subsidy_scope(quote):
    quote["net_cost_subsidy_basis"] = None
    assert check_net_cost(quote)["status"] == "needs_confirmation"


def test_central_not_assumed_whole_subsidy(quote):
    quote["net_cost_subsidy_basis"] = field("central_and_state")
    result = check_net_cost(quote)
    assert result["status"] == "missing"
    assert "subsidy_state" in result["message"]


def test_central_and_state_summed(quote):
    quote["net_cost_subsidy_basis"] = field("central_and_state")
    quote["subsidy_state"] = field(10000)
    quote["net_cost"] = field(109000)
    assert check_net_cost(quote)["status"] == "consistent"


def test_combined_not_added_to_components(quote):
    quote["net_cost_subsidy_basis"] = field("combined")
    quote["subsidy_state"] = field(10000)
    quote["subsidy_combined"] = field(88000)
    quote["net_cost"] = field(109000)
    result = check_net_cost(quote)
    assert result["status"] == "consistent"
    assert result["evidence"][-1]["formula"] == "gross_total - subsidy_combined"


def test_no_subsidy_deducted(quote):
    quote["net_cost_subsidy_basis"] = field("none")
    quote["net_cost"] = field(197000)
    assert check_net_cost(quote)["status"] == "consistent"


def test_multiple_options_block_arithmetic(quote):
    quote["multiple_options"] = field(True)
    assert check_gross_total(quote)["status"] == "needs_confirmation"
    assert check_net_cost(quote)["status"] == "needs_confirmation"


def test_run_checks(quote):
    assert [f["check_id"] for f in run_checks(quote)] == ["C1_capacity", "C3_gross_total", "C3_net_cost"]


def test_cfa_rules_placeholder_pending():
    path = Path(__file__).parent.parent / "src" / "rules" / "cfa_rules.json"
    rules = json.loads(path.read_text())
    assert rules["verification_status"] == "pending"
    assert rules["source_url"] is None and rules["effective_date"] is None
