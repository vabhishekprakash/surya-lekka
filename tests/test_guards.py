"""Typing guards: a number the household typed that is outside the range a home system has,
or about 10, 100 or 1000 times what the other numbers imply, needs the household to confirm
it. Nothing is clipped or converted. Until it is confirmed, every check that uses it says
"Please check the number you entered"."""

import json
from pathlib import Path

import pytest

from api import manual
from checks import guards, run_checks
from conftest import run_confirmed

ROOT = Path(__file__).resolve().parent.parent
ANSWERS = {"state": "Telangana", "consumer_type": "individual_household", "portal_application_on_or_after_cutoff": True,
           "first_system": True, "prior_central_subsidy": False, "give_it_up": False, "multiple_options": False,
           "capacity_basis": "dc_kwp", "gst_treatment": "excluded", "extra_charges_complete": True,
           "net_cost_subsidy_basis": "central"}
GOOD = {"stated_capacity": "3.3 kWp", "panel_count": "6", "panel_wattage": "550 Wp", "base_price": "1,50,000",
        "gst_amount": "18,000", "gross_total": "1,68,000", "subsidy_central": "78,000", "net_cost": "90,000"}


def typed(fields, confirmed=(), operands=False):
    """Run the checks on typed fields. confirmed names typed numbers the household confirms after
    the guards ask (by the token the first run gave them); operands=True also confirms every
    operand set as shown."""
    charges, corrections = manual.typed_corrections(fields)
    quote = manual.blank_quote(charges)
    inputs = {"corrections": corrections, "confirmations": ANSWERS}
    paths = {manual.TEXT_FIELDS.get(n, n) for n in confirmed}
    if paths:
        first = run_checks(quote, inputs)
        inputs["verified"] = [e["token"] for e in first["entry_checks"] if e["path"] in paths]
    return (run_confirmed if operands else run_checks)(quote, inputs)


def statuses(result):
    return {f["check_id"]: f for f in result["findings"] if f.get("item") is None}


def test_the_ranges_are_data_with_the_asked_bounds():
    data = json.loads((ROOT / "src" / "rules" / "entry_guards.json").read_text(encoding="utf-8"))
    assert data["panel_wattage_w"] == [100, 800] and data["system_size_kw"] == [0.5, 20]
    assert data["panel_count"] == [1, 100] and data["scale_ratios"] == [10, 100, 1000]
    assert set(data["amounts"]) >= {"base_price", "gst_amount", "gross_total", "net_cost", "subsidy_central"}


def test_plausible_numbers_raise_nothing():
    result = typed(GOOD, operands=True)
    assert result["entry_checks"] == []
    assert statuses(result)["C1_capacity"]["status"] == "consistent"


@pytest.mark.parametrize("name,value,path", [
    ("panel_wattage", "5500 Wp", "module_groups[G1].wattage"),
    ("panel_wattage", "55 Wp", "module_groups[G1].wattage"),
    ("stated_capacity", "33 kWp", "stated_capacity"),
    ("stated_capacity", "0.33 kWp", "stated_capacity"),
    ("panel_count", "600", "module_groups[G1].count"),
    ("panel_count", "0", "module_groups[G1].count"),
    ("gross_total", "1,68,00,000", "gross_total"),
    ("base_price", "1,500", "base_price"),
])
def test_a_number_outside_a_home_systems_range_needs_confirming(name, value, path):
    result = typed({**GOOD, name: value})
    (entry,) = [e for e in result["entry_checks"] if e["path"] == path]
    assert entry["reason"] == "range"
    held = [f for f in result["findings"] if f["status"] == "needs_confirmation"
            and f["message"].startswith("Please check the number you entered")]
    assert held, "a check that uses it must ask for the number"
    assert not [f for f in result["findings"] if f["status"] == "inconsistent"]


def test_nothing_is_clipped_or_converted():
    result = typed({**GOOD, "panel_wattage": "5500 Wp"})
    group = result["quote"]["module_groups"][0]
    assert group["wattage"]["value"]["raw"] == "5500 Wp" and group["wattage"]["value"]["parsed"] == "5500"


@pytest.mark.parametrize("name,value,path,times", [
    ("gross_total", "16,800", "gross_total", 10),
    ("net_cost", "9,00,000", "net_cost", 10),
    ("gross_total", "16,80,000", "gross_total", 10),
])
def test_a_number_about_10_or_100_times_off_what_the_others_imply_needs_confirming(name, value, path, times):
    result = typed({**GOOD, name: value})
    entries = [e for e in result["entry_checks"] if e["path"] == path]
    assert entries and entries[0]["reason"] == "scale" and entries[0]["params"]["times"] == times


def test_scale_against_the_panels():
    result = typed({**GOOD, "stated_capacity": "0.5 kWp", "panel_count": "6", "panel_wattage": "550 Wp"})
    assert not [e for e in result["entry_checks"] if e["reason"] == "scale"]  # 6.6x is not a slip of the decimal
    result = typed({**GOOD, "stated_capacity": "1 kWp", "panel_count": "2", "panel_wattage": "500 Wp"})
    assert result["entry_checks"] == []
    result = typed({**GOOD, "stated_capacity": "1 kWp", "panel_count": "20", "panel_wattage": "500 Wp"})
    assert {e["path"] for e in result["entry_checks"]} >= {"stated_capacity", "module_groups[G1].count"}


def test_confirming_the_number_lets_the_checks_use_it_as_typed():
    result = typed({**GOOD, "panel_wattage": "750 Wp", "stated_capacity": "33 kWp"}, confirmed=["stated_capacity"],
                   operands=True)
    assert not [e for e in result["entry_checks"] if e["path"] == "stated_capacity"]
    assert statuses(result)["C1_capacity"]["status"] == "inconsistent"  # 6 x 750 W is 4.5 kW, not 33


def test_confirming_one_number_does_not_confirm_another():
    result = typed({**GOOD, "stated_capacity": "33 kWp", "gross_total": "1,68,00,000"}, confirmed=["stated_capacity"])
    paths = [e["path"] for e in result["entry_checks"]]
    assert "gross_total" in paths and "stated_capacity" not in paths


def test_values_read_from_the_quote_are_never_guarded():
    from extract.dryrun import load_dry_run_wire
    from extract.merge import merge_batches
    from extract.wire_schema import normalise_batch, validate

    wire = load_dry_run_wire()
    wire["prices"] = [dict(p, raw="1,68,00,000") if p["kind"] == "gross_total" else p for p in wire["prices"]]
    cleaned, errors, _ = validate(wire, [1, 2])
    quote = merge_batches([{"batch": 1, "pages": [1, 2], "contract": normalise_batch(cleaned, 1)}])
    assert run_checks(quote, {"confirmations": ANSWERS})["entry_checks"] == []


def test_the_manual_api_takes_confirmed_entries():
    body = {"fields": {**GOOD, "stated_capacity": "33 kWp"}, "answers": ANSWERS}
    held = manual.handler({"body": json.dumps(body)}, None)
    view = json.loads(held["body"])
    assert [e["field"] for e in view["entry_checks"]] == ["stated_capacity"]
    body["verified"] = [view["entry_checks"][0]["token"]]
    body["challenge"] = view["challenge"]  # the token is signed for this response's challenge
    view = json.loads(manual.handler({"body": json.dumps(body)}, None)["body"])
    assert "stated_capacity" not in [e["field"] for e in view["entry_checks"]]
    # with 33 kW confirmed, 6 panels of 550 W are now the numbers that look 10 times off
    assert {e["field"] for e in view["entry_checks"]} == {"panel_count", "panel_wattage"}
    body["verified"] = "stated_capacity"
    assert manual.handler({"body": json.dumps(body)}, None)["statusCode"] == 400


def test_a_confirmed_typed_number_needs_confirming_again_once_changed():
    first = typed({**GOOD, "stated_capacity": "33 kWp"})
    token = first["entry_checks"][0]["token"]
    for again in ("33.0 kWp", "34 kWp", "33 kW"):  # a reformat, a new number, another unit
        charges, corrections = manual.typed_corrections({**GOOD, "stated_capacity": again})
        result = run_checks(manual.blank_quote(charges), {"corrections": corrections, "confirmations": ANSWERS,
                                                          "verified": [token]})
        assert "stated_capacity" in [e["path"] for e in result["entry_checks"]], again


def test_guards_name_their_reason_in_plain_words():
    result = typed({**GOOD, "panel_wattage": "5500 Wp"})
    (entry,) = result["entry_checks"]
    assert entry["message"] == "A home system's panels are usually 100 to 800 W each. Please check the number you entered."
    assert guards.message(entry) == entry["message"]
