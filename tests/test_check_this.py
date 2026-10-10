""""Check this": a value that is unresolved, conflicting, read with low confidence, or made by
a source rule that produced a wrong value on the development quotes needs the household to
confirm or correct that one value. Until then every finding that depends on it stays "needs
confirmation". The rule works on properties of the value, never on a list of known errors."""

import copy
import json
from pathlib import Path

import pytest

from checks import run_checks
from checks import check_this as ct
from extract import textract_client as tc
from extract.merge import merge_batches
from textract_pages import Page

ROOT = Path(__file__).resolve().parent.parent


def contract(page):
    wire, _ = tc.map_page(page.reply(), 1, sources=tc.SOURCES)
    return tc.to_contract(wire, 1)


def quote(page):
    return merge_batches([{"batch": 1, "pages": [1], "model_id": "textract", "contract": contract(page)}])


def fact(c, name):
    (f,) = [f["field"] for f in c["facts"] if f["name"] == name]
    return f


# --- every value says which source rule made it, and how sure the reading was ------------------

def test_a_line_value_carries_its_rule_and_line_confidence():
    page = Page()
    line = page.line("Grand total Rs 2,00,000", 0.3)
    next(b for b in page.blocks if b["Id"] == line)["Confidence"] = 71.0
    f = fact(contract(page), "gross_total")
    assert f["rules"] == ["lines.gross_total"] and f["confidence"] == 71.0


def test_a_query_value_carries_the_lower_of_answer_and_line_confidence():
    page = Page()
    page.line("Amount payable after subsidy Rs 1,22,000", 0.3)
    page.answer("NET_COST", "Rs 1,22,000", confidence=64.0, top=0.3)
    f = fact(contract(page), "net_cost")
    assert "queries.net_cost" in f["rules"] and f["confidence"] == 64.0


def test_table_values_carry_their_table_rule():
    page = Page()
    page.table([["Description", "Qty", "Wattage"], ["Solar module", "6", "550 Wp"]], confidence={(2, 2): 77.0})
    (g,) = contract(page)["module_groups"]
    assert g["count"]["rules"] == ["bom_table.count"] and g["count"]["confidence"] == 77.0
    assert g["wattage"]["rules"] == ["bom_table.wattage"]


def test_option_table_values_carry_their_rule():
    page = Page()
    page.table([["Option", "Capacity", "Total price (Rs)"], ["A", "3 kW", "1,80,000"], ["B", "5 kW", "2,90,000"]])
    c = contract(page)
    totals = [f["field"] for f in c["facts"] if f["name"] == "gross_total"]
    assert totals and all(f["rules"] == ["options_table.gross_total"] for f in totals)
    assert c["flags"]["model_proposed"]["multiple_options"]["rules"] == ["options_table.multiple_options"]


def test_flags_from_lines_carry_their_rule():
    page = Page()
    page.line("Prices are inclusive of GST", 0.3)
    f = contract(page)["flags"]["model_proposed"]["gst_treatment"]
    assert f["rules"] == ["lines.gst_treatment"] and f["confidence"] == 99.0


def test_merge_keeps_rules_and_confidence_on_candidates():
    page = Page()
    page.line("Grand total Rs 2,00,000", 0.3)
    f = quote(page)["gross_total"]
    assert f["candidates"][0]["rules"] == ["lines.gross_total"] and f["candidates"][0]["confidence"] == 99.0


# --- which values need checking, by property ---------------------------------------------------

def field(**extra):
    base = {"value": {"raw": "Rs 2,00,000", "parsed": "200000", "parse_status": "ok"}, "evidence_text": "Total",
            "page": 1, "batch": 1, "rules": ["lines.gross_total"], "confidence": 99.0}
    base.update(extra)
    base["candidates"] = [{k: base[k] for k in ("value", "evidence_text", "page", "batch", "rules", "confidence")
                           if k in base}]
    return base


CONFIG = {"low_confidence_below": 80, "rules_with_dev_errors": ["queries.net_cost"]}


@pytest.mark.parametrize("f,reasons", [
    (field(), []),
    (field(confidence=60.0), ["low_confidence"]),
    (field(rules=["queries.net_cost"]), ["source_rule"]),
    (field(rules=["unmatched"]), ["source_rule"]),
    (field(value={"raw": "Rs 2,00,0O0", "parsed": None, "parse_status": "unparseable"}), ["unresolved"]),
    (dict(field(), conflict=True), ["conflict"]),
    (field(confidence=60.0, rules=["queries.net_cost"]), ["low_confidence", "source_rule"]),
])
def test_reasons_come_from_the_values_own_properties(f, reasons):
    assert ct.reasons(f, CONFIG) == reasons


def test_a_value_the_household_confirmed_or_corrected_needs_nothing():
    for provenance in ("user_verified", "user_corrected"):
        assert ct.reasons(field(confidence=10.0, rules=["queries.net_cost"], provenance=provenance), CONFIG) == []


def test_a_value_with_no_rule_from_a_saved_or_typed_reading_needs_nothing():
    f = field()
    for key in ("rules", "confidence"):
        f.pop(key)
        f["candidates"][0].pop(key)
    assert ct.reasons(f, CONFIG) == []


def test_the_config_lists_rules_and_a_confidence_band_only():
    config = json.loads((ROOT / "src" / "rules" / "check_this.json").read_text(encoding="utf-8"))
    assert set(config) >= {"low_confidence_below", "rules_with_dev_errors", "source", "computed_from"}
    assert all("." in r for r in config["rules_with_dev_errors"])
    assert 50 < config["low_confidence_below"] <= 100


# --- a value that needs checking blocks every finding that uses it -------------------------------

def sample_quote():
    from extract.dryrun import load_dry_run_wire
    from extract.wire_schema import normalise_batch, validate

    cleaned, errors, _ = validate(load_dry_run_wire(), [1, 2])
    assert errors == []
    return merge_batches([{"batch": 1, "pages": [1, 2], "contract": normalise_batch(cleaned, 1)}])


ANSWERS = {"state": "Telangana", "consumer_type": "individual_household", "portal_application_on_or_after_cutoff": True,
           "first_system": True, "prior_central_subsidy": False, "give_it_up": False, "multiple_options": False,
           "capacity_basis": "dc_kwp", "gst_treatment": "included", "extra_charges_complete": True,
           "net_cost_subsidy_basis": "central"}


def by_id(result):
    return {(f["check_id"], f.get("item")): f for f in result["findings"]}


def flagged(q, path):
    """Mark the value at path as made by a rule that produced a wrong value on dev."""
    q = copy.deepcopy(q)
    container, key = (q, path) if "[" not in path else (q["module_groups"][0], path.split(".")[1])
    container[key]["rules"] = ["queries.net_cost"]
    for c in container[key].get("candidates") or []:
        c["rules"] = ["queries.net_cost"]
    return q


def test_a_value_to_check_holds_every_finding_that_uses_it_at_needs_confirmation(monkeypatch):
    monkeypatch.setattr(ct, "CONFIG", CONFIG)
    q = sample_quote()
    before = by_id(run_checks(q, {"confirmations": ANSWERS}))
    assert before[("C3_gross_total", None)]["status"] != "needs_confirmation"
    after = run_checks(flagged(q, "gross_total"), {"confirmations": ANSWERS})
    findings = by_id(after)
    assert findings[("C3_gross_total", None)]["status"] == "needs_confirmation"
    assert findings[("C3_net_cost", None)]["status"] == "needs_confirmation"
    assert findings[("C1_capacity", None)] == before[("C1_capacity", None)]  # doesn't use the total
    assert [c["path"] for c in after["check_this"]] == ["gross_total"]
    assert after["check_this"][0]["reasons"] == ["source_rule"]


def test_confirming_that_one_value_releases_its_findings(monkeypatch):
    monkeypatch.setattr(ct, "CONFIG", CONFIG)
    q = flagged(sample_quote(), "gross_total")
    result = run_checks(q, {"confirmations": ANSWERS, "verified": ["gross_total"]})
    assert by_id(result)[("C3_gross_total", None)]["status"] != "needs_confirmation"
    assert result["check_this"] == []


def test_confirming_another_value_releases_nothing(monkeypatch):
    monkeypatch.setattr(ct, "CONFIG", CONFIG)
    q = flagged(sample_quote(), "gross_total")
    result = run_checks(q, {"confirmations": ANSWERS, "verified": ["base_price"]})
    assert by_id(result)[("C3_gross_total", None)]["status"] == "needs_confirmation"


def test_a_panel_value_to_check_holds_the_capacity_check(monkeypatch):
    monkeypatch.setattr(ct, "CONFIG", CONFIG)
    q = sample_quote()
    gid = q["module_groups"][0]["group_id"]
    result = run_checks(flagged(q, f"module_groups[{gid}].count"), {"confirmations": ANSWERS})
    assert by_id(result)[("C1_capacity", None)]["status"] == "needs_confirmation"
    assert [c["path"] for c in result["check_this"]] == [f"module_groups[{gid}].count"]
    result = run_checks(flagged(q, f"module_groups[{gid}].count"),
                        {"confirmations": ANSWERS, "verified": [f"module_groups[{gid}].count"]})
    assert by_id(result)[("C1_capacity", None)]["status"] != "needs_confirmation"


def test_verified_paths_must_be_real_values():
    with pytest.raises(KeyError):
        run_checks(sample_quote(), {"confirmations": ANSWERS, "verified": ["no_such_field"]})


def test_there_is_no_way_to_verify_everything_at_once():
    for bulk in (["*"], ["all"], True):
        with pytest.raises((KeyError, TypeError, ValueError)):
            run_checks(sample_quote(), {"confirmations": ANSWERS, "verified": bulk})
