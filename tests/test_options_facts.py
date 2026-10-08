"""Every price, subsidy, net cost, stated capacity and panel line carries an
option_id; selecting an option selects exactly its own facts."""

import copy

from checks import run_checks
from checks.contract import normalise
from extract.dryrun import load_dry_run_wire
from extract.merge import merge_batches
from extract.wire_schema import TOOL_SCHEMA, normalise_batch, validate

PRICE_A = {"base_price": "Rs. 1,80,000", "gst_amount": "Rs. 16,020", "discount": "Rs. 1,520",
           "gross_total": "Rs. 1,97,000/-", "net_cost": "Rs. 1,19,000"}
PRICE_B = {"base_price": "Rs. 2,40,000", "gst_amount": "Rs. 21,360", "gross_total": "Rs. 2,63,860",
           "net_cost": "Rs. 1,85,860"}


def two_option_wire():
    """Option A is the S1 system; option B is 8 x 550 Wp with its own prices."""
    data = load_dry_run_wire()
    data["multiple_options"] = {"value": "yes", "evidence_text": "Option A / Option B", "page": 1}
    data["options"] = [{"option_id": "Option A", "label": "Option A", "page": 1},
                       {"option_id": "Option B", "label": "Option B", "page": 1}]
    data["prices"] = (
        [{"option_id": "Option A", "kind": k, "raw": v, "evidence_text": v, "page": 2} for k, v in PRICE_A.items()]
        + [{"option_id": "Option B", "kind": k, "raw": v, "evidence_text": v, "page": 2} for k, v in PRICE_B.items()])
    data["subsidies"] = [
        {"option_id": "Option A", "kind": "central", "raw": "Rs. 78,000", "evidence_text": "Rs. 78,000", "page": 2},
        {"option_id": "Option B", "kind": "central", "raw": "Rs. 78,000", "evidence_text": "Rs. 78,000", "page": 2}]
    data["capacities"] = [
        {"option_id": "Option A", "raw": "3.3 kWp", "evidence_text": "3.3 kWp", "page": 1},
        {"option_id": "Option B", "raw": "4.4 kWp", "evidence_text": "4.4 kWp", "page": 1}]
    group_a = data["module_groups"][0]
    group_a["option_id"] = "Option A"
    group_b = copy.deepcopy(group_a)
    group_b.update(option_id="Option B", count="8", count_evidence="8")
    data["module_groups"] = [group_a, group_b]
    for item in data["inverters"] + data["extra_charges"]:
        item["option_id"] = "All"
    return data


def quote_for(data):
    cleaned, errors, _ = validate(data, [1, 2])
    assert errors == []
    return merge_batches([{"batch": 1, "pages": [1, 2], "contract": normalise_batch(cleaned, 1)}])


def test_fact_lists_carry_option_ids_within_two_levels():
    props = TOOL_SCHEMA["properties"]
    for name in ("prices", "subsidies", "capacities", "module_groups", "inverters", "extra_charges"):
        item = props[name]["items"]
        assert "option_id" in item["required"], name
        assert all(sub["type"] != "object" for sub in item["properties"].values()), name
    assert set(props["prices"]["items"]["properties"]) == {"option_id", "kind", "raw", "evidence_text", "page"}


def test_selecting_an_option_selects_exactly_its_own_facts():
    quote = quote_for(two_option_wire())
    for option, prices, count, stated in (("Option A", PRICE_A, 6, "3.3"), ("Option B", PRICE_B, 8, "4.4")):
        q = copy.deepcopy(quote)
        q["flags"]["user_confirmed"]["selected_option"] = option
        view = normalise(q)
        for name in ("base_price", "gst_amount", "discount", "gross_total", "net_cost"):
            got = view[name]
            assert (got["raw"] if got else None) == prices.get(name), (option, name)
        assert [g["count"]["value"] for g in view["module_groups"]] == [count]
        assert str(view["stated_capacity_kw"]["value"]) == stated
    view = normalise(quote)  # nothing selected yet
    assert run_checks(quote)["findings"][2]["status"] == "needs_confirmation"
    assert view["selected_option"] is None


def test_selected_option_checks_use_only_its_facts():
    quote = quote_for(two_option_wire())
    answers = {"state": "Telangana", "consumer_type": "individual_household",
               "portal_application_on_or_after_cutoff": True, "first_system": True,
               "prior_central_subsidy": False, "give_it_up": False}
    for option in ("Option A", "Option B"):
        r = run_checks(quote, {"confirmations": {**answers, "selected_option": option}})
        by = {f["check_id"]: f["status"] for f in r["findings"] if f["item"] is None}
        assert by["C1_capacity"] == "consistent", option
        assert by["C3_gross_total"] == "consistent" and by["C3_net_cost"] == "consistent", option


def test_correction_goes_to_the_selected_options_fact():
    quote = quote_for(two_option_wire())
    r = run_checks(quote, {"confirmations": {"selected_option": "Option B"},
                           "corrections": {"gross_total": "Rs. 2,63,861"}})
    assert r["quote"]["option_fields"]["Option B"]["gross_total"]["provenance"] == "user_corrected"
    assert r["quote"]["option_fields"]["Option A"]["gross_total"]["value"]["raw"] == "Rs. 1,97,000/-"


def test_single_option_labelled_facts_still_apply():
    data = load_dry_run_wire()
    for name in ("prices", "subsidies", "capacities", "module_groups", "inverters", "extra_charges"):
        for item in data[name]:
            item["option_id"] = "Option 1"
    quote = quote_for(data)
    assert normalise(quote)["gross_total"]["raw"] == "Rs. 1,97,000/-"
