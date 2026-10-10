"""Highlighting boxes: page numbers and coordinates beside the evidence, and nothing else.
They never change a value or a finding."""

import copy
import json
from pathlib import Path

import pytest

from api import boxes
from checks import run_checks
from extract import textract_client as tc
from extract.merge import merge_batches
from textract_pages import Page

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
ANSWERS = {"state": "Telangana", "consumer_type": "individual_household", "portal_application_on_or_after_cutoff": True,
           "first_system": True, "prior_central_subsidy": False, "give_it_up": False, "multiple_options": False,
           "capacity_basis": "dc_kwp", "gst_treatment": "included", "extra_charges_complete": True}


def record(reply, page=1):
    wire, _ = tc.map_page(reply, page)
    return {"batch": page, "pages": [page], "model_id": "textract", "contract": tc.to_contract(wire, page),
            "response": reply}


def read(replies, with_boxes):
    records = [record(r, n) for n, r in enumerate(replies, 1)]
    quote = merge_batches(copy.deepcopy(records))
    if with_boxes:
        boxes.attach(quote, [b for r in records for b in boxes.record_boxes(r)])
    return quote


def strip(node):
    if isinstance(node, dict):
        return {k: strip(v) for k, v in node.items() if k != "boxes"}
    if isinstance(node, list):
        return [strip(v) for v in node]
    return node


def evidence_with_boxes(quote):
    return [f for f in boxes._evidence(quote) if "boxes" in f]


def test_a_line_value_gets_its_lines_box():
    page = Page()
    page.line("Grand total Rs 2,00,000", 0.3, left=0.1, width=0.5)
    quote = read([page.reply()], True)
    assert quote["gross_total"]["boxes"] == [{"page": 1, "box": [0.1, 0.3, 0.5, 0.02]}]


def test_a_table_row_gets_the_boxes_of_its_cells():
    page = Page()
    page.table([["Description", "Qty", "Wattage"], ["Solar module", "6", "550 Wp"]])
    for b in page.blocks:  # give the cells geometry, as Textract does
        if b["BlockType"] == "CELL":
            b["Geometry"] = {"BoundingBox": {"Left": 0.1 * b["ColumnIndex"], "Top": 0.1 * b["RowIndex"], "Width": 0.1,
                                             "Height": 0.05}}
    quote = read([page.reply()], True)
    (group,) = quote["module_groups"]
    assert [b["box"] for b in group["count"]["boxes"]] == [[0.1, 0.2, 0.1, 0.05], [0.2, 0.2, 0.1, 0.05],
                                                            [0.3, 0.2, 0.1, 0.05]]


def test_conflicting_evidence_gets_boxes_too():
    one, two = Page(), Page()
    one.line("Grand total Rs 2,00,000", 0.3)
    two.line("Grand total Rs 2,10,000", 0.6)
    quote = read([one.reply(), two.reply()], True)
    assert quote["gross_total"]["conflict"]
    assert [(c["page"], c["boxes"][0]["box"][1]) for c in quote["gross_total"]["candidates"]] == [(1, 0.3), (2, 0.6)]


def test_only_page_numbers_and_coordinates_are_stored():
    replies = [json.loads((FIXTURES / "textract" / f"S2-page-{n}.json").read_text(encoding="utf-8")) for n in (1, 2)]
    found = [b for n, r in enumerate(replies, 1) for b in boxes.record_boxes(record(r, n))]
    assert found
    quote = read(replies, True)
    for f in evidence_with_boxes(quote):
        for b in f["boxes"]:
            assert set(b) == {"page", "box"} and b["page"] == f["page"]
            assert len(b["box"]) == 4 and all(isinstance(x, float) and 0 <= x <= 1.5 for x in b["box"])


@pytest.mark.parametrize("sample", ["S1", "S2", "S3"])
def test_boxes_change_no_value_and_no_finding_on_the_sample_replies(sample):
    replies = [json.loads((FIXTURES / "textract" / f"{sample}-page-{n}.json").read_text(encoding="utf-8"))
               for n in (1, 2)]
    plain, boxed = read(replies, False), read(replies, True)
    assert evidence_with_boxes(boxed)
    assert strip(boxed) == plain
    for inputs in ({}, {"confirmations": ANSWERS}):
        a, b = run_checks(plain, inputs), run_checks(boxed, inputs)
        assert a["findings"] == b["findings"] and a["check_this"] == b["check_this"]


@pytest.mark.parametrize("path", sorted((FIXTURES / "redteam").glob("[0-9][0-9]_*.json")), ids=lambda p: p.stem)
def test_boxes_change_no_finding_on_the_red_team_fixtures(path):
    case = json.loads(path.read_text(encoding="utf-8"))
    plain, boxed = read([case["textract_reply"]], False), read([case["textract_reply"]], True)
    assert strip(boxed) == plain
    for inputs in case.get("requests") or [{"confirmations": case.get("confirmations") or {}}]:
        assert run_checks(plain, inputs)["findings"] == run_checks(boxed, inputs)["findings"]


def test_evidence_with_no_matching_line_gets_no_boxes():
    assert boxes.boxes_for("Nothing like this", ({"Grand total": [[0, 0, 1, 1]]}, {})) == []
