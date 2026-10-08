"""kVA is apparent power. It is never converted to, or compared as, kW or kWp."""

from decimal import Decimal

from conftest import capacity_field, wire_fact

from checks import run_checks
from checks.contract import normalise
from extract import scoring
from extract.dryrun import load_dry_run_wire
from extract.merge import merge_batches
from test_extract_merge import on_page, record


def statuses(quote_v1):
    return {(f["check_id"], f["item"]): f for f in run_checks(quote_v1)["findings"]}


def test_kva_system_size_is_not_compared_with_panels(quote_v1):
    quote_v1["stated_capacity"] = capacity_field("3.27 kVA")  # same number as 6 x 545 W
    view = normalise(quote_v1)
    assert not isinstance(view["stated_capacity_kw"]["value"], Decimal)
    assert view["capacity_basis"]["value"] == "unclear"
    c1 = statuses(quote_v1)[("C1_capacity", None)]
    assert c1["status"] == "needs_confirmation" and c1["question"] == "capacity_basis"
    assert "kVA" in c1["message"]


def test_kva_system_size_never_used_as_dc_for_the_subsidy(quote_v1):
    quote_v1["stated_capacity"] = capacity_field("3.27 kVA")
    quote_v1["module_groups"][0]["wattage"] = None  # C2 would fall back to a stated DC capacity
    c2 = statuses(quote_v1)[("C2_central_subsidy", None)]
    assert c2["status"] == "needs_confirmation" and c2["question"] == "dc_capacity"


def test_kva_inverter_rating_kept_in_kva(quote_v1):
    quote_v1["inverters"][0]["rating"] = capacity_field("3 kVA")
    inv = normalise(quote_v1)["inverters"][0]
    assert inv["rating_kw"] is None
    assert inv["rating_kva"]["value"] == Decimal("3")
    assert ("C4_missing_details", "inverter_rating") not in statuses(quote_v1)


def test_kw_and_kva_from_two_batches_conflict():
    first, second = load_dry_run_wire(), on_page(load_dry_run_wire(), 3)
    wire_fact(first, "stated_capacity")["raw"] = "3.3 kW"
    wire_fact(second, "stated_capacity")["raw"] = "3.3 kVA"
    quote = merge_batches([record(1, [1, 2], first), record(2, [3], second)])
    assert quote["stated_capacity"]["conflict"] is True


def test_scoring_keeps_kva_apart():
    quote = merge_batches([record(1, [1, 2], load_dry_run_wire())])  # stated 3.3 kWp
    assert scoring.score_field(quote, "stated_capacity", {"value": "3.3 kVA", "pages": []})["status"] == "wrong"
    assert scoring.score_field(quote, "stated_capacity", {"value": "3300 W", "pages": []})["status"] == "correct"
    wire = load_dry_run_wire()
    wire_fact(wire, "stated_capacity")["raw"] = "3.3 kVA"
    quote = merge_batches([record(1, [1, 2], wire)])
    assert scoring.score_field(quote, "stated_capacity", {"value": "3.3 KVA", "pages": []})["status"] == "correct"
    assert scoring.score_field(quote, "stated_capacity", {"value": "3.3 kW", "pages": []})["status"] == "wrong"
