import json

from conftest import amount_field, capacity_field, field_v1

from checks.contract import normalise
from checks.missing import check_missing_details


def c4(quote_v1):
    return {f["item"]: f for f in check_missing_details(normalise(quote_v1))}


def test_complete_quote(quote_v1):
    (only,) = check_missing_details(normalise(quote_v1))
    assert only["status"] == "consistent" and only["item"] is None
    assert "not verified" in only["message"]


def test_missing_wattage_and_count(quote_v1):
    quote_v1["module_groups"][0]["wattage"] = None
    quote_v1["module_groups"][0]["count"] = field_v1(None, None)
    r = c4(quote_v1)
    assert r["panel_wattage"]["status"] == "missing" and r["panel_wattage"]["question"] == "panel_wattage"
    assert r["panel_count"]["status"] == "missing"


def test_wattage_range_asks_which(quote_v1):
    quote_v1["module_groups"][0]["wattage"] = capacity_field("540-550 Wp")
    r = c4(quote_v1)
    assert r["panel_wattage"]["status"] == "needs_confirmation"
    assert r["panel_wattage"]["question"] == "exact_capacity"


def test_no_module_groups(quote_v1):
    quote_v1["module_groups"] = []
    r = c4(quote_v1)
    assert {"panel_wattage", "panel_count", "module_make_model"} <= set(r)


def test_module_alternatives_ask_which(quote_v1):
    g = quote_v1["module_groups"][0]
    g["make_model"] = field_v1("Example PV", "Example PV / Sample Energy or equivalent")
    g["make_model_alternatives"] = [field_v1("Sample Energy", "Example PV / Sample Energy or equivalent"),
                                    field_v1("equivalent", "Example PV / Sample Energy or equivalent")]
    r = c4(quote_v1)["module_make_model"]
    assert r["status"] == "needs_confirmation" and r["question"] == "module_choice"
    assert r["question_params"]["options"] == "Example PV, Sample Energy or equivalent"


def test_module_model_missing(quote_v1):
    quote_v1["module_groups"][0]["make_model"] = None
    assert c4(quote_v1)["module_make_model"]["status"] == "missing"


def test_dcr_never_inferred(quote_v1):
    quote_v1["dcr_declaration"] = None
    r = c4(quote_v1)["dcr_declaration"]
    assert r["status"] == "missing" and "not found on the quote" in r["message"]
    quote_v1["dcr_declaration"] = field_v1(False, "Non-DCR modules")
    assert c4(quote_v1)["dcr_declaration"]["status"] == "needs_confirmation"


def test_inverter_details(quote_v1):
    inv = quote_v1["inverters"][0]
    inv["make_model_alternatives"] = [field_v1("Sample Power SP-3", "Example Inverters / Sample Power")]
    inv["rating"] = None
    r = c4(quote_v1)
    assert r["inverter_make_model"]["question"] == "inverter_choice"
    assert r["inverter_rating"]["status"] == "missing"
    quote_v1["inverters"] = []
    r = c4(quote_v1)
    assert r["inverter_make_model"]["status"] == "missing" and r["inverter_rating"]["status"] == "missing"


def test_vendor_registration_absent_is_not_unregistered(quote_v1):
    quote_v1["vendor_registration"] = None
    r = c4(quote_v1)["vendor_registration"]
    assert r["status"] == "missing"
    assert "not found on the quote" in r["message"]
    assert "does not mean the vendor is unregistered" in r["message"]


def test_gst_basis_unclear(quote_v1):
    quote_v1["flags"]["model_proposed"]["gst_treatment"] = field_v1("unclear", "GST as applicable", 2)
    assert c4(quote_v1)["gst_basis"]["status"] == "needs_confirmation"
    quote_v1["flags"]["model_proposed"]["gst_treatment"] = None
    assert c4(quote_v1)["gst_basis"]["status"] == "needs_confirmation"


def test_extras_outside_total_and_net_meter(quote_v1):
    quote_v1["extra_charges"] = [
        {"charge_id": "E1", "option_id": None,
         "label": field_v1("Elevated structure", "Elevated structure Rs. 12,000 extra", 2),
         "amount": amount_field("Rs. 12,000"),
         "included_in_total": field_v1("no", "Elevated structure Rs. 12,000 extra", 2),
         "total_label": "Total"},
        {"charge_id": "E2", "option_id": None,
         "label": field_v1("Civil work", "Civil work at actuals", 2),
         "amount": amount_field("at actuals"),
         "included_in_total": field_v1("unclear", "Civil work at actuals", 2),
         "total_label": "Total"},
    ]
    r = c4(quote_v1)
    extras = r["extras_outside_total"]
    assert extras["status"] == "needs_confirmation"
    assert "Elevated structure: Rs 12,000, outside the total (Total)" in extras["message"]
    assert "Civil work: at actuals, not clearly inside the total" in extras["message"]
    assert r["net_meter"]["status"] == "needs_confirmation"
    json.dumps(list(r.values()))


def test_net_meter_inside_total_needs_nothing(quote_v1):
    assert "net_meter" not in c4(quote_v1)


def test_options_unselected(quote_v1):
    quote_v1["flags"]["model_proposed"]["multiple_options"] = field_v1(True, "Option A / Option B")
    (only,) = check_missing_details(normalise(quote_v1))
    assert only["status"] == "needs_confirmation"
