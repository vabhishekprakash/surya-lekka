import copy
import json

import pytest
from conftest import field_v1

from checks import run_checks


def statuses(result):
    return [(f["check_id"], f["item"], f["status"]) for f in result["findings"]]


def test_check_order_and_json(quote_v1):
    r = run_checks(quote_v1)
    assert [f["check_id"] for f in r["findings"]] == [
        "C1_capacity", "C2_central_subsidy", "C3_gross_total", "C3_net_cost", "C4_missing_details"]
    assert all(f["status"] == "consistent" for f in r["findings"])
    assert r["corrected_fields"] == [] and r["questions"] == [] and r["vendor_message"] is None
    json.dumps(r)


def test_correction_recomputes_and_keeps_original(quote_v1):
    before = copy.deepcopy(quote_v1)
    quote_v1["stated_capacity"]["value"] = {"raw": "3.5 kWp", "parsed": "3.5", "unit": "kWp", "parse_status": "ok"}
    assert run_checks(quote_v1)["findings"][0]["status"] == "inconsistent"

    r = run_checks(quote_v1, {"corrections": {"stated_capacity": "3.27 kWp"}})
    assert r["findings"][0]["status"] == "consistent"
    field = r["quote"]["stated_capacity"]
    assert field["provenance"] == "user_corrected"
    assert field["original"]["value"]["raw"] == "3.5 kWp"
    assert field["page"] == 1 and field["evidence_text"] == "System size 3.27 kWp"
    assert r["original_quote"]["stated_capacity"]["value"]["raw"] == "3.5 kWp"
    assert quote_v1["stated_capacity"]["value"]["raw"] == "3.5 kWp"  # input untouched
    assert r["corrected_fields"] == [{
        "path": "stated_capacity",
        "original_value": {"raw": "3.5 kWp", "parsed": "3.5", "unit": "kWp", "parse_status": "ok"},
        "corrected_value": {"raw": "3.27 kWp", "parsed": "3.27", "unit": "kWp", "parse_status": "ok"},
    }]
    assert before["stated_capacity"] != quote_v1["stated_capacity"]


def test_item_corrections_by_id(quote_v1):
    r = run_checks(quote_v1, {"corrections": {
        "module_groups[G1].count": 5, "module_groups[G1].wattage": "500 W", "subsidy_central": "85,800"}})
    by = {f["check_id"]: f for f in r["findings"]}
    assert by["C1_capacity"]["status"] == "inconsistent"
    assert by["C2_central_subsidy"]["status"] == "inconsistent"
    assert [c["path"] for c in r["corrected_fields"]] == [
        "module_groups[G1].count", "module_groups[G1].wattage", "subsidy_central"]
    entry = [e for e in by["C1_capacity"]["evidence"] if e.get("field") == "module_groups[0].count"][0]
    assert entry["kind"] == "user_corrected" and entry["original_value"] == 6


def test_user_values_never_shown_as_quoted(quote_v1):
    r = run_checks(quote_v1, {"corrections": {"base_price": "1,80,000"}})
    for f in r["findings"]:
        for e in f["evidence"]:
            if e.get("provenance"):
                assert e["kind"] == e["provenance"] != "quoted"


def test_confirmations_answer_gates(quote_v1):
    quote_v1["flags"]["user_confirmed"] = {}
    r = run_checks(quote_v1)
    assert "individual household" in r["findings"][1]["message"]
    answers = {"consumer_type": "individual_household", "state": "Telangana",
               "portal_application_on_or_after_cutoff": True, "first_system": True,
               "prior_central_subsidy": False}
    r = run_checks(quote_v1, {"confirmations": answers})
    assert r["findings"][1]["status"] == "consistent"
    r = run_checks(quote_v1, {"confirmations": {**answers, "give_it_up": True}})
    assert r["findings"][1]["status"] == "out_of_scope"


def test_deterministic_and_idempotent(quote_v1):
    inputs = {"corrections": {"base_price": "1,80,000", "discount": "Rs. 1,520"},
              "confirmations": {"gst_treatment": "excluded"}}
    a = run_checks(quote_v1, inputs)
    b = run_checks(quote_v1, inputs)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    again = run_checks(a["quote"], inputs)
    assert again["findings"] == a["findings"]
    assert again["corrected_fields"] == a["corrected_fields"]
    assert again["quote"] == a["quote"]


def test_correcting_a_missing_field(quote_v1):
    quote_v1["vendor_registration"] = None
    r = run_checks(quote_v1, {"corrections": {"vendor_registration": "EX-VR-0001"}})
    assert r["quote"]["vendor_registration"]["original"] is None
    assert r["findings"][-1]["status"] == "consistent"


def test_unknown_path_or_confirmation_rejected(quote_v1):
    with pytest.raises(KeyError):
        run_checks(quote_v1, {"corrections": {"module_groups[G9].count": 3}})
    with pytest.raises(KeyError):
        run_checks(quote_v1, {"corrections": {"evidence_text": "x"}})
    with pytest.raises(KeyError):
        run_checks(quote_v1, {"confirmations": {"dcr_declaration": True}})


def test_evidence_status_annotation(quote_v1):
    pages = {1: "No. of panels: 6\n545 Wp mono PERC\nSystem size 3.27 kWp", 2: None}
    r = run_checks(quote_v1, {"corrections": {"base_price": "180000"}}, page_texts=pages)
    c1 = r["findings"][0]["evidence"]
    assert {e["field"]: e["evidence_status"] for e in c1 if e["kind"] == "quoted"}["stated_capacity_kw"] \
        == "text_matched"
    gross = {e["field"]: (e["kind"], e["evidence_status"])
             for e in r["findings"][2]["evidence"] if e["kind"] != "computed"}
    assert gross["base_price"] == ("user_corrected", "user_corrected")
    assert gross["gross_total"] == ("quoted", "not_machine_verified")
    quote_v1["module_groups"][0]["count"] = field_v1(6, "No. of panels: 7", 1)
    c1 = run_checks(quote_v1, page_texts=pages)["findings"][0]["evidence"]
    assert c1[0]["evidence_status"] == "mismatch"


# --- charges the household adds on the review screen ------------------------------------------

def s2_without_charges():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    quote = json.loads((root / "samples" / "cached" / "S2.json").read_text(encoding="utf-8"))["quote"]
    quote["extra_charges"] = []
    return quote


S2_ANSWERS = {"state": "Telangana", "consumer_type": "individual_household",
              "portal_application_on_or_after_cutoff": True, "first_system": True,
              "prior_central_subsidy": False, "give_it_up": False}


def gross(result):
    return next(f for f in result["findings"] if f["check_id"] == "C3_gross_total")


def test_an_added_charge_counts_like_a_typed_one_and_is_marked_as_entered():
    added = {"extra_charges[U1].label": "Net meter charges", "extra_charges[U1].amount": "2,500",
             "extra_charges[U1].included_in_total": "yes"}
    r = run_checks(s2_without_charges(), {"corrections": added,
                                          "confirmations": {**S2_ANSWERS, "extra_charges_complete": True}})
    assert gross(r)["status"] == "consistent"
    (charge,) = r["quote"]["extra_charges"]
    assert charge["charge_id"] == "U1" and charge["added_by_household"] is True
    assert charge["amount"]["provenance"] == "user_corrected" and charge["amount"]["page"] is None
    assert any(e.get("kind") == "user_corrected" for e in gross(r)["evidence"])


def test_without_the_charge_or_with_doubt_the_total_is_not_settled():
    confirmations = {**S2_ANSWERS, "extra_charges_complete": True}
    assert gross(run_checks(s2_without_charges(), {"confirmations": confirmations}))["status"] == "inconsistent"
    unsure = {"extra_charges[U1].label": "Net meter charges", "extra_charges[U1].amount": "2,500",
              "extra_charges[U1].included_in_total": "unclear"}
    r = run_checks(s2_without_charges(), {"corrections": unsure, "confirmations": confirmations})
    assert gross(r)["status"] == "needs_confirmation"


@pytest.mark.parametrize("answer", ["unclear", False])
def test_anything_but_yes_to_every_charge_listed_keeps_the_total_unsettled(answer):
    added = {"extra_charges[U1].label": "Net meter charges", "extra_charges[U1].amount": "2,500",
             "extra_charges[U1].included_in_total": "yes"}
    r = run_checks(s2_without_charges(), {"corrections": added,
                                          "confirmations": {**S2_ANSWERS, "extra_charges_complete": answer}})
    assert gross(r)["status"] == "needs_confirmation"


@pytest.mark.parametrize("path", ["extra_charges[U0].label", "extra_charges[U11].label", "extra_charges[X1].label",
                                  "extra_charges[U1].page", "module_groups[U1].count"])
def test_only_a_few_household_charges_with_known_fields_can_be_added(path):
    with pytest.raises(KeyError):
        run_checks(s2_without_charges(), {"corrections": {path: "x"}})


def test_s2_with_the_missed_charge_added_gives_the_expected_findings():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    expected = json.loads((root / "samples" / "expected" / "S2.json").read_text(encoding="utf-8"))["expected"]
    added = {"extra_charges[U1].label": "Net meter charges", "extra_charges[U1].amount": "2,500",
             "extra_charges[U1].included_in_total": "yes"}
    r = run_checks(s2_without_charges(), {"corrections": added,
                                          "confirmations": {**S2_ANSWERS, "extra_charges_complete": True}})
    assert statuses(r) == [(f["check_id"], f["item"], f["status"]) for f in expected["findings"]]
