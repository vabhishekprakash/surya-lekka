import copy
import json
from pathlib import Path

import pytest
from conftest import wire_fact

from checks import run_checks
from checks.evidence import verify_evidence
from extract.dryrun import load_dry_run_wire
from extract.merge import merge_batches
from extract.wire_schema import normalise_batch, validate

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = json.loads((ROOT / "samples" / "expected" / "S1.json").read_text(encoding="utf-8"))
ANSWERS = SAMPLE["user_inputs"]


def record(batch, pages, data):
    cleaned, errors, _ = validate(data, pages)
    assert errors == []
    return {"batch": batch, "pages": pages, "contract": normalise_batch(cleaned, batch)}


def on_page(data, page):
    """Copy of a tool input with every page set to page."""
    out = copy.deepcopy(data)

    def walk(node):
        if isinstance(node, dict):
            if "page" in node:
                node["page"] = page
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(out)
    return out


def two_batches(change=None):
    """Batch 1 reads pages 1-2, batch 2 reads page 3 and repeats the same lines."""
    first, second = load_dry_run_wire(), on_page(load_dry_run_wire(), 3)
    if change:
        change(first, second)
    return [record(1, [1, 2], first), record(2, [3], second)]


def by_check(quote, inputs=ANSWERS):
    return {(f["check_id"], f["item"]): f for f in run_checks(quote, inputs)["findings"]}


def test_single_batch_end_to_end_through_the_checks():
    pytest.importorskip("boto3")
    from extract.dryrun import stubbed_client, tool_response
    from extract.nova_client import extract_batch
    from extract.render import PageImage

    client, stubber = stubbed_client("bedrock-runtime")
    stubber.add_response("converse", tool_response(load_dry_run_wire()))
    pages = [PageImage(n, b"\xff\xd8synthetic", 10, 10, 150, 85) for n in (1, 2)]
    quote = merge_batches([extract_batch(client, "global.amazon.nova-2-lite-v1:0", pages, 1)])
    assert quote["processing_complete"] is True and quote["pages_processed"] == [1, 2]
    page_texts = {int(k): "\n".join(v) for k, v in SAMPLE["pages"].items()}
    result = run_checks(quote, ANSWERS, page_texts=page_texts)
    actual = [{"check_id": f["check_id"], "item": f["item"], "status": f["status"]} for f in result["findings"]]
    assert actual == SAMPLE["expected"]["findings"]
    quoted = [e for f in result["findings"] for e in f["evidence"] if e["kind"] == "quoted" and e["evidence_text"]]
    assert quoted and all(e["evidence_status"] == "text_matched" for e in quoted)


def test_agreeing_batches_keep_every_candidate():
    quote = merge_batches(two_batches())
    f = quote["base_price"]
    assert f["value"]["raw"] == "Rs. 1,80,000" and f["page"] == 2 and f["batch"] == 1
    assert [(c["page"], c["batch"]) for c in f["candidates"]] == [(2, 1), (3, 2)]
    assert quote["flags"]["needs_confirmation"] == []
    assert all(f["status"] == "consistent" for f in by_check(quote).values())


def test_conflicting_totals_need_confirmation_and_no_value_wins():
    def change(first, second):
        wire_fact(second, "gross_total")["raw"] = "Rs. 2,05,000"
    quote = merge_batches(two_batches(change))
    f = quote["gross_total"]
    assert f["conflict"] is True and f["value"]["parse_status"] == "conflict" and f["value"]["raw"] is None
    assert [(c["value"]["raw"], c["page"], c["batch"]) for c in f["candidates"]] == [
        ("Rs. 1,97,000/-", 2, 1), ("Rs. 2,05,000", 3, 2)]
    (conflict,) = quote["flags"]["needs_confirmation"]
    assert conflict["field"] == "gross_total" and len(conflict["candidates"]) == 2
    checks = by_check(quote)
    assert checks[("C3_gross_total", None)]["status"] == "needs_confirmation"
    assert checks[("C3_net_cost", None)]["status"] == "needs_confirmation"


def test_repeated_panel_line_is_not_added_twice():
    quote = merge_batches(two_batches())
    (group,) = quote["module_groups"]
    assert group["count"]["value"] == 6 and group["group_id"] == "G1"
    assert by_check(quote)[("C1_capacity", None)]["status"] == "consistent"


def test_single_lines_merge_field_by_field():
    def change(first, second):
        del first["module_groups"][0]["wattage"]
        del second["module_groups"][0]["count"]
    quote = merge_batches(two_batches(change))
    (group,) = quote["module_groups"]
    assert group["count"]["batch"] == 1 and group["wattage"]["batch"] == 2
    assert by_check(quote)[("C1_capacity", None)]["status"] == "consistent"


def test_different_panel_lines_across_batches_conflict():
    def change(first, second):
        extra = copy.deepcopy(first["module_groups"][0])
        extra.update(count="2", wattage="545 Wp")
        first["module_groups"].append(extra)
    quote = merge_batches(two_batches(change))
    assert len(quote["module_groups"]) == 3
    assert all(g["count"]["conflict"] for g in quote["module_groups"])
    assert quote["flags"]["needs_confirmation"][0]["field"] == "module_groups[option=None]"
    checks = by_check(quote)
    assert checks[("C1_capacity", None)]["status"] == "needs_confirmation"
    c2 = checks[("C2_central_subsidy", None)]  # falls back to the stated DC capacity, never the panel lines
    assert [e["formula"] for e in c2["evidence"] if e.get("name") == "dc_kwp"] == ["stated DC capacity"]


def test_extra_charges_matched_by_label():
    def change(first, second):
        second["extra_charges"][0].update(label="Structure charges", amount="Rs. 5,000")
    quote = merge_batches(two_batches(change))
    assert [e["label"]["value"] for e in quote["extra_charges"]] == ["Net meter charges", "Structure charges"]
    assert [e["charge_id"] for e in quote["extra_charges"]] == ["E1", "E2"]

    def change(first, second):
        second["extra_charges"][0]["amount"] = "Rs. 3,000"
    quote = merge_batches(two_batches(change))
    (charge,) = quote["extra_charges"]
    assert charge["amount"]["conflict"] is True
    assert by_check(quote)[("C3_gross_total", None)]["status"] == "needs_confirmation"


def test_flag_and_plain_conflicts_need_confirmation():
    def change(first, second):
        second["multiple_options"]["value"] = "yes"
    checks = by_check(merge_batches(two_batches(change)))
    assert checks[("C1_capacity", None)]["status"] == "needs_confirmation"
    assert "one option or several" in checks[("C1_capacity", None)]["message"]

    def change(first, second):
        second["vendor_registration"]["value"] = "EX-VR-0009"
        second["dcr_declaration"]["value"] = "non_dcr"
        second["inverters"][0]["make_model"] = "Other Inverter OI-3"
    checks = by_check(merge_batches(two_batches(change)))
    for item in ("vendor_registration", "dcr_declaration", "inverter_make_model"):
        assert checks[("C4_missing_details", item)]["status"] == "needs_confirmation", item


def test_failed_batch_or_skipped_page_marks_processing_incomplete():
    first, _ = two_batches()
    quote = merge_batches([first], failures=[{"batch": 2, "pages": [3], "kind": "max_tokens", "code": None}])
    assert quote["processing_complete"] is False and quote["pages_skipped"] == [3]
    assert [(b["batch"], b["status"]) for b in quote["batches"]] == [(1, "ok"), (2, "failed")]
    assert "processed" in by_check(quote)[("C2_central_subsidy", None)]["message"]
    quote = merge_batches([first], skipped_pages=[9])
    assert quote["processing_complete"] is False and quote["pages_skipped"] == [9]
    assert merge_batches([first])["processing_complete"] is True


def test_no_batches_is_incomplete():
    quote = merge_batches([], failures=[{"batch": 1, "pages": [1, 2], "kind": "unexpected_text", "code": None}])
    assert quote["processing_complete"] is False and quote["module_groups"] == []
    json.dumps(run_checks(quote)["findings"])
