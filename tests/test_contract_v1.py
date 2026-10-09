import json
from decimal import Decimal

from conftest import amount_field, capacity_field, field_v1

from checks.arithmetic import check_gross_total, check_net_cost
from checks.capacity import check_capacity
from checks.contract import Unparsed, normalise


def test_v1_fixture_shape(quote_v1):
    assert quote_v1["contract_version"] == "v1"
    assert quote_v1["processing_complete"] is True and quote_v1["pages_processed"] == [1, 2]
    assert set(quote_v1["flags"]) == {"model_proposed", "user_confirmed"}
    for g in quote_v1["module_groups"]:
        assert {"group_id", "option_id"} <= set(g)
    for e in quote_v1["extra_charges"]:
        assert e["included_in_total"]["value"] in ("yes", "no", "unclear") and e["total_label"]
    for name in ("base_price", "gst_amount", "discount", "gross_total", "subsidy_central", "net_cost"):
        f = quote_v1[name]
        assert set(f) == {"value", "evidence_text", "page", "batch"}
        assert set(f["value"]) == {"raw", "parsed", "parse_status"}


def test_v1_gives_same_results_as_internal_fixture(quote, quote_v1):
    view = normalise(quote_v1)
    for check in (check_capacity, check_gross_total, check_net_cost):
        assert check(view)["status"] == check(quote)["status"] == "consistent"


def test_normalised_values(quote_v1):
    view = normalise(quote_v1)
    assert view["base_price"]["value"] == Decimal("180000")
    assert view["base_price"]["raw"] == "Rs. 1,80,000" and view["base_price"]["batch"] == 1
    assert view["module_groups"][0]["wattage_w"]["value"] == Decimal("545")
    assert view["stated_capacity_kw"]["value"] == Decimal("3.27")
    assert view["inverters"][0]["rating_kw"]["value"] == Decimal("3")


def test_evidence_carries_batch_and_raw_and_is_json(quote_v1):
    result = check_gross_total(normalise(quote_v1))
    base = next(e for e in result["evidence"] if e.get("field") == "base_price")
    assert base["value"] == "180000" and base["raw"] == "Rs. 1,80,000" and base["batch"] == 1
    json.dumps(result)


def test_unit_conversion_and_ranges(quote_v1):
    quote_v1["module_groups"][0]["wattage"] = capacity_field("0.545 kWp")
    quote_v1["stated_capacity"] = capacity_field("3270 Wp")
    view = normalise(quote_v1)
    assert view["module_groups"][0]["wattage_w"]["value"] == Decimal("545.000")
    assert view["stated_capacity_kw"]["value"] == Decimal("3.270")
    assert check_capacity(view)["status"] == "consistent"
    quote_v1["module_groups"][0]["wattage"] = capacity_field("540-545 Wp")
    view = normalise(quote_v1)
    assert view["module_groups"][0]["wattage_w"]["value"] == {"min": Decimal(540), "max": Decimal(545)}
    assert check_capacity(view)["status"] == "needs_confirmation"


def test_unparsed_amount_needs_confirmation(quote_v1):
    quote_v1["gross_total"] = amount_field("Rs. 1,97,000 + GST")
    view = normalise(quote_v1)
    assert view["gross_total"]["value"] == Unparsed("Rs. 1,97,000 + GST", "ambiguous")
    assert check_gross_total(view)["status"] == "needs_confirmation"
    quote_v1["gross_total"] = amount_field("")
    assert check_gross_total(normalise(quote_v1))["status"] == "missing"


def test_user_confirmed_overrides_model_flag(quote_v1):
    quote_v1["flags"]["model_proposed"]["gst_treatment"] = field_v1("unclear", "GST as applicable", 2)
    assert check_gross_total(normalise(quote_v1))["status"] == "needs_confirmation"
    quote_v1["flags"]["user_confirmed"]["gst_treatment"] = "excluded"
    view = normalise(quote_v1)
    assert view["gst_treatment"]["provenance"] == "user_confirmed"
    assert check_gross_total(view)["status"] == "consistent"


def test_extra_outside_total_not_added(quote_v1):
    quote_v1["extra_charges"].append({
        "charge_id": "E2", "option_id": None,
        "label": field_v1("Elevated structure", "Elevated structure Rs. 12,000 extra", 2),
        "amount": amount_field("Rs. 12,000"),
        "included_in_total": field_v1("no", "Elevated structure Rs. 12,000 extra", 2),
        "total_label": "Total",
    })
    result = check_gross_total(normalise(quote_v1))
    assert result["status"] == "consistent"
    assert result["notes"][0] == 'Charges listed outside the total were not added: "Elevated structure".'


def test_extra_unclear_needs_confirmation(quote_v1):
    quote_v1["extra_charges"][0]["included_in_total"] = field_v1("unclear", "Net meter Rs. 2,500", 2)
    assert check_gross_total(normalise(quote_v1))["status"] == "needs_confirmation"
    quote_v1["extra_charges"][0]["included_in_total"] = None
    assert check_gross_total(normalise(quote_v1))["status"] == "needs_confirmation"


def test_options_filter_to_selected(quote_v1):
    g2 = json.loads(json.dumps(quote_v1["module_groups"][0]))
    g2.update(group_id="G2", option_id="B", count=field_v1(8, "Option B: 8 panels"))
    quote_v1["module_groups"][0]["option_id"] = "A"
    quote_v1["module_groups"].append(g2)
    quote_v1["options"] = [{"option_id": "A", "label": field_v1("Option A")},
                           {"option_id": "B", "label": field_v1("Option B")}]
    quote_v1["flags"]["model_proposed"]["multiple_options"] = field_v1(True, "Option A / Option B")
    assert check_capacity(normalise(quote_v1))["status"] == "needs_confirmation"
    quote_v1["flags"]["user_confirmed"]["selected_option"] = "A"
    view = normalise(quote_v1)
    assert [g["group_id"] for g in view["module_groups"]] == ["G1"]
    assert check_capacity(view)["status"] == "consistent"
    quote_v1["flags"]["user_confirmed"]["selected_option"] = "B"
    assert check_capacity(normalise(quote_v1))["status"] == "inconsistent"


def test_rejects_other_versions(quote):
    import pytest

    with pytest.raises(ValueError):
        normalise(quote)
