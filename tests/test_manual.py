import json
from pathlib import Path

import pytest

from api import common, manual

ROOT = Path(__file__).resolve().parent.parent
S1 = json.loads((ROOT / "samples" / "expected" / "S1.json").read_text(encoding="utf-8"))
S1_TYPED = {
    "stated_capacity": "3.3 kWp", "panel_count": "6", "panel_wattage": "550 Wp",
    "panel_make_model": "Example PV EXM-550", "inverter_make_model": "Example Inverters EXI-3K",
    "inverter_rating": "3 kW", "dcr_declaration": True, "vendor_registration": "EX-VR-0001",
    "base_price": "1,80,000", "gst_amount": "16,020", "discount": "1,520", "gross_total": "1,97,000",
    "subsidy_central": "78,000", "net_cost": "1,19,000",
    "extra_charges": [{"label": "Net meter charges", "amount": "2,500", "included_in_total": "yes"}],
}
S1_ANSWERS = {**S1["user_inputs"]["confirmations"], "capacity_basis": "dc_kwp", "gst_treatment": "excluded",
              "extra_charges_complete": True, "net_cost_subsidy_basis": "central"}


def post(body=None, raw=None):
    event = {"body": raw if raw is not None else json.dumps(body), "isBase64Encoded": False,
             "requestContext": {"http": {"sourceIp": "198.51.100.7"}}}
    r = manual.handler(event, None)
    assert r["headers"]["cache-control"] == "no-store"
    return r["statusCode"], json.loads(r["body"])


def post_confirmed(body):
    """Post, then post again confirming every operand set the first answer asked about."""
    status, first = post(body)
    tokens = [f["confirm_token"] for f in first["findings"] if f.get("confirm_token")]
    return post({**body, "confirmed_operands": tokens})


def statuses(body):
    return [{"check_id": f["check_id"], "item": f["item"], "status": f["status"]} for f in body["findings"]]


def evidence(body):
    return [e for f in body["findings"] for e in f["evidence"]]


def test_typed_s1_matches_the_sample_findings(monkeypatch):
    for name in ("TABLE_NAME", "BUCKET_NAME"):
        monkeypatch.delenv(name, raising=False)  # nothing is stored
    status, held = post({"fields": S1_TYPED, "answers": S1_ANSWERS})
    assert status == 200 and not [f for f in held["findings"] if f["status"] in ("consistent", "inconsistent")]
    status, body = post_confirmed({"fields": S1_TYPED, "answers": S1_ANSWERS})
    assert status == 200 and body["mode"] == "manual"
    assert statuses(body) == S1["expected"]["findings"]
    assert body["questions"] == [] and body["vendor_message"] is None


def test_typed_values_are_never_shown_as_quoted():
    body = post({"fields": S1_TYPED, "answers": S1_ANSWERS})[1]
    kinds = {e["kind"] for e in evidence(body)}
    assert kinds <= {"user_corrected", "user_confirmed", "computed"} and "user_corrected" in kinds
    assert all(e.get("evidence_text") is None and e.get("page") is None for e in evidence(body)
               if e["kind"] != "computed")


def test_the_s2_mismatch_shows_up_when_typed():
    typed = {**S1_TYPED, "stated_capacity": "3 kWp", "panel_count": "5", "panel_wattage": "500 W",
             "subsidy_central": "85,800"}
    body = post_confirmed({"fields": typed, "answers": S1_ANSWERS})[1]
    found = {f["check_id"]: f["status"] for f in body["findings"] if f["item"] is None}
    assert found["C1_capacity"] == "inconsistent" and found["C2_central_subsidy"] == "inconsistent"
    assert [q["id"] for q in body["questions"]][:2] == ["capacity_mismatch", "subsidy_higher"]


def test_blank_form_lists_what_is_missing():
    status, body = post({"fields": {}, "answers": {}})
    assert status == 200
    found = {(f["check_id"], f["item"]): f["status"] for f in body["findings"]}
    assert found[("C1_capacity", None)] == "missing"
    assert found[("C4_missing_details", "panel_wattage")] == "missing"
    assert found[("C4_missing_details", "dcr_declaration")] == "missing"
    assert body["vendor_message"] and "panel" in body["vendor_message"]


@pytest.mark.parametrize("body,code", [
    ({"fields": {"colour": "red"}}, "unknown_field"),
    ({"fields": {"base_price": 180000}}, "bad_fields"),
    ({"fields": {"base_price": "x" * 201}}, "bad_fields"),
    ({"fields": {"dcr_declaration": "yes"}}, "bad_fields"),
    ({"fields": {"extra_charges": [{"label": "Net meter", "amount": "2,500", "included_in_total": "maybe"}]}},
     "bad_fields"),
    ({"fields": {"extra_charges": [{"label": "x", "colour": "red"}]}}, "bad_fields"),
    ({"fields": {"extra_charges": [{"label": "x"}] * 11}}, "bad_fields"),
    ({"fields": [], "answers": {}}, "bad_inputs"),
    ({"fields": {}, "answers": {"favourite_colour": "red"}}, "unknown_field"),
    ({"fields": {}, "answers": {"state": ["Telangana"]}}, "bad_inputs"),
])
def test_bad_input_is_refused(body, code):
    status, out = post(body)
    assert status == 400 and out["error"] == code


def test_bad_json_is_refused():
    assert post(raw="not json") == (400, {"error": "bad_json", "message": "The request body is not valid JSON."})


def test_logs_hold_no_typed_values(caplog):
    with caplog.at_level("INFO"):
        assert post({"fields": S1_TYPED, "answers": S1_ANSWERS})[0] == 200
        post({"fields": {"colour": "EX-VR-0001"}})
    for value in ("EX-VR-0001", "1,80,000", "Example PV", "Telangana", "colour"):
        assert value not in caplog.text
    events = [json.loads(r.getMessage()) for r in caplog.records if r.name == "surya_lekka"]
    assert {e["event"] for e in events} == {"manual_checked", "manual_refused"}
    assert all(set(e) <= common.LOG_FIELDS | {"event"} for e in events)
