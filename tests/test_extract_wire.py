import copy
import json
import re
from pathlib import Path

import pytest
from conftest import batch_fact, wire_fact

from extract.dryrun import load_dry_run_wire
from extract.merge import merge_batches
from extract.wire_schema import AMOUNT_FIELDS, TOOL_SCHEMA, count_value, normalise_batch, validate

ROOT = Path(__file__).resolve().parent.parent
S1 = json.loads((ROOT / "samples" / "expected" / "S1.json").read_text(encoding="utf-8"))["quote"]
CUSTOMER = re.compile(r"customer|name|phone|mobile|email|address|consumer", re.I)


def keys_and_depth(schema, depth=0):
    """Property names, and how many objects deep the schema nests."""
    keys, deepest = [], depth
    if schema.get("type") == "object":
        depth += 1
        deepest = depth
    for key, sub in (schema.get("properties") or {}).items():
        keys.append(key)
        k, d = keys_and_depth(sub, depth)
        keys += k
        deepest = max(deepest, d)
    if "items" in schema:
        k, d = keys_and_depth(schema["items"], depth)
        keys += k
        deepest = max(deepest, d)
    return keys, deepest


def wire():
    return load_dry_run_wire()


def contract(data, pages=(1, 2), batch=1):
    cleaned, errors, _ = validate(data, list(pages))
    assert errors == []
    return normalise_batch(cleaned, batch)


def without_candidates(field):
    return None if field is None else {k: v for k, v in field.items() if k != "candidates"}


def test_root_has_only_type_properties_required():
    assert set(TOOL_SCHEMA) == {"type", "properties", "required"}
    assert TOOL_SCHEMA["type"] == "object"
    assert set(TOOL_SCHEMA["required"]) <= set(TOOL_SCHEMA["properties"])


def test_schema_is_shallow_and_has_no_customer_fields():
    keys, depth = keys_and_depth(TOOL_SCHEMA)
    assert depth == 2  # the root, then a field or list item
    assert not [k for k in keys if CUSTOMER.search(k)]


def test_amounts_are_verbatim_strings():
    for name in ("prices", "subsidies", "capacities"):
        assert TOOL_SCHEMA["properties"][name]["items"]["properties"]["raw"]["type"] == "string"


def test_dry_run_wire_is_valid():
    cleaned, errors, ignored = validate(wire(), [1, 2])
    assert errors == [] and ignored == []


def test_normaliser_gives_the_sample_contract():
    c = contract(wire())
    quote = merge_batches([{"batch": 1, "pages": [1, 2], "contract": c}])
    assert quote["option_fields"] == {}
    for name in ("stated_capacity", "dcr_declaration", "vendor_registration", *AMOUNT_FIELDS):
        expected = S1[name]
        assert without_candidates(quote[name]) == (None if expected is None else {**expected, "batch": 1}), name
    for list_name, keys in (("module_groups", ("count", "wattage", "make_model")),
                            ("inverters", ("make_model", "rating")),
                            ("extra_charges", ("label", "amount", "included_in_total"))):
        assert len(c[list_name]) == len(S1[list_name])
        for got, expected in zip(c[list_name], S1[list_name]):
            for key in keys:
                assert got[key] == {**expected[key], "batch": 1}, (list_name, key)
    assert "consumer_type" not in c["flags"]["model_proposed"]  # the user answers it
    for name, got in c["flags"]["model_proposed"].items():
        expected = S1["flags"]["model_proposed"][name]
        assert got == (None if expected is None else {**expected, "batch": 1}), name
    assert c["extra_charges"][0]["total_label"] == "Grand Total"


def test_ranges_and_alternatives_preserved():
    data = wire()
    data["module_groups"][0].update({"wattage": "540-550 Wp", "make_model_alternatives": ["Sample Energy SE-545", " "]})
    data["inverters"][0]["make_model_alternatives"] = ["Other Inverter OI-3"]
    c = contract(data)
    g = c["module_groups"][0]
    assert g["wattage"]["value"]["parsed"] == {"min": "540", "max": "550"}
    assert [a["value"] for a in g["make_model_alternatives"]] == ["Sample Energy SE-545"]
    assert g["make_model_alternatives"][0]["page"] == 1
    assert [a["value"] for a in c["inverters"][0]["make_model_alternatives"]] == ["Other Inverter OI-3"]


@pytest.mark.parametrize("raw,value", [("6", 6), ("6 Nos", 6), ("6 nos.", 6), ("12 panels", 12), ("six", "six"),
                                       ("6 + 4", "6 + 4"), ("6-8", "6-8")])
def test_count_values(raw, value):
    assert count_value(raw) == value


def test_amounts_kept_verbatim():
    data = wire()
    wire_fact(data, "base_price")["raw"] = "Rs. 1,80,000/-"
    wire_fact(data, "discount")["raw"] = "approx 1,520"
    c = contract(data)
    assert batch_fact(c, "base_price")["value"] == {"raw": "Rs. 1,80,000/-", "parsed": "180000", "parse_status": "ok"}
    assert batch_fact(c, "discount")["value"]["raw"] == "approx 1,520"
    assert batch_fact(c, "discount")["value"]["parsed"] is None


@pytest.mark.parametrize("absent", [None, "", "  "])
def test_not_found_fields_become_null(absent):
    data = wire()
    wire_fact(data, "subsidy_central")["raw"] = absent
    data["vendor_registration"] = {"value": "", "evidence_text": "", "page": 2}
    data["give_it_up"] = {"value": "not_mentioned"}
    data["multiple_options"] = {"value": "not_stated"}
    c = contract(data)
    assert batch_fact(c, "subsidy_central") is None and c["vendor_registration"] is None
    assert c["flags"]["model_proposed"]["give_it_up"] is None
    assert c["flags"]["model_proposed"]["multiple_options"] is None


def test_validation_errors_name_paths_not_values():
    secret = "Rs. 9,99,999 secret"
    cases = [
        (lambda d: d["prices"][0].update(raw=999999), "input.prices[0].raw: expected a string"),
        (lambda d: d["prices"][0].update(kind=secret), "input.prices[0].kind: not one of"),
        (lambda d: d["gst_treatment"].update(value=secret), "input.gst_treatment.value: not one of"),
        (lambda d: d["prices"][4].update(page=7), "input.prices[4].page: page 7 is not in this batch"),
        (lambda d: d.pop("module_groups"), "input.module_groups: required"),
        (lambda d: d["module_groups"][0].pop("option_id"), "input.module_groups[0].option_id: required"),
        (lambda d: d["module_groups"][0].pop("page"), "input.module_groups[0].page: required"),
        (lambda d: d["inverters"][0].update(page="1"), "input.inverters[0].page: expected an integer"),
        (lambda d: d.update(extra_charges={"label": secret}), "input.extra_charges: expected an array"),
        (lambda d: d.update(capacities=secret), "input.capacities: expected an array"),
        (lambda d: d.update(vendor_registration=secret), "input.vendor_registration: expected an object"),
    ]
    for change, message in cases:
        data = wire()
        change(data)
        _, errors, _ = validate(data, [1, 2])
        assert any(e.startswith(message) for e in errors), (message, errors)
        assert not any(secret in e or "999999" in e for e in errors)
    assert validate([], [1])[1] == ["input: expected an object"]


def test_unknown_keys_are_dropped():
    data = wire()
    data["customer_name"] = "Synthetic Person"
    cleaned, errors, ignored = validate(data, [1, 2])
    assert errors == [] and ignored == ["customer_name"]
    assert "customer_name" not in cleaned and "customer_name" not in normalise_batch(cleaned, 1)


def test_option_ids_normalised():
    data = copy.deepcopy(wire())
    data["options"] = [{"option_id": " Option  A ", "label": "Option A: 3.3 kWp", "page": 1}]
    data["module_groups"][0]["option_id"] = "Option A"
    data["extra_charges"][0]["option_id"] = ""
    data["inverters"][0]["option_id"] = "ALL"
    c = contract(data)
    assert c["options"][0]["option_id"] == "Option A" == c["module_groups"][0]["option_id"]
    assert c["extra_charges"][0]["option_id"] is None and c["inverters"][0]["option_id"] is None
