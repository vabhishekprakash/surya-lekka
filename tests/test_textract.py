import json
import re
from pathlib import Path

import pytest

from extract import textract_client as tc
from extract.merge import merge_batches
from extract.render import PageImage
from extract.textract_queries import MAX_QUERIES, QUERIES, TARGETS, queries_config
from extract.wire_schema import validate
from textract_pages import Page

JPEG = b"\xff\xd8\xff\xe0" + bytes(100)


def contract(page, number=1, batch=1, **kwargs):
    wire, _ = tc.map_page(page.reply(), number, **kwargs)
    cleaned, errors, _ = validate(wire, [number])
    assert errors == []
    return tc.to_contract(wire, batch)


def record(page, number, batch):
    return {"batch": batch, "pages": [number], "model_id": "textract", "contract": contract(page, number, batch)}


def facts(c, name=None):
    return [(f["option_id"], f["name"], f["field"]["value"].get("raw"), f["field"]["evidence_text"], f["field"]["page"])
            for f in c["facts"] if name in (None, f["name"])]


# --- queries -------------------------------------------------------------------------------

def test_queries_fit_textract_limits():
    assert len(QUERIES) <= MAX_QUERIES == 15
    aliases = [a for a, _, _ in QUERIES]
    assert len(set(aliases)) == len(aliases) and set(aliases) == set(TARGETS)
    for alias, text, _ in QUERIES:
        assert re.fullmatch(r"[A-Z_]{1,100}", alias)
        assert text.isascii() and len(text) <= 200 and text.startswith(("What is", "What are"))
    assert queries_config()["Queries"][0] == {"Text": QUERIES[0][1], "Alias": QUERIES[0][0]}


def test_request_sends_page_bytes_with_tables_and_queries():
    request = tc.build_request(PageImage(3, JPEG, 0, 0, 0, 0))
    assert request["Document"] == {"Bytes": JPEG}  # bytes, never an S3 reference
    assert request["FeatureTypes"] == ["TABLES", "QUERIES"]
    assert request["QueriesConfig"] == queries_config()


def test_client_uses_adaptive_retries_with_four_attempts():
    client = tc.make_client("ap-south-1")
    assert client.meta.config.retries == {"mode": "adaptive", "total_max_attempts": 4}
    assert client.meta.service_model.service_name == "textract" and client.meta.region_name == "ap-south-1"


# --- answers to facts ------------------------------------------------------------------------

def test_answer_keeps_raw_text_full_overlapping_lines_and_the_page_number():
    page = Page()
    page.line("Proposal for rooftop solar", 0.05)
    page.line("Total system capacity: 3 kWp on-grid", 0.20)
    page.line("Panel make: Example Solar", 0.24)
    page.answer("SYSTEM_CAPACITY", "3 kWp", top=0.20, left=0.3, width=0.1)
    c = contract(page, number=4, batch=2)
    assert facts(c) == [(None, "stated_capacity", "3 kWp", "Total system capacity: 3 kWp on-grid", 4)]
    assert c["facts"][0]["field"]["batch"] == 2
    assert c["flags"]["model_proposed"]["capacity_basis"]["value"] == "dc_kwp"


def test_evidence_joins_every_line_the_answer_box_overlaps():
    page = Page()
    page.line("Grand total", 0.40)
    page.line("Rs 1,90,000/-", 0.42)
    page.line("Terms and conditions", 0.60)
    page.answer("TOTAL_PAYABLE", "Rs 1,90,000/-", top=0.405, width=0.3)
    assert facts(contract(page), "gross_total") == [(None, "gross_total", "Rs 1,90,000/-", "Grand total\nRs 1,90,000/-", 1)]


def test_answer_without_a_box_uses_the_lines_that_contain_it():
    page = Page()
    page.line("Quotation date: 12/09/2026", 0.1)
    page.answer("QUOTE_DATE", "12/09/2026")
    assert contract(page)["quote_date"]["evidence_text"] == "Quotation date: 12/09/2026"


def test_an_answer_found_on_no_line_of_the_page_is_dropped():
    page = Page()
    page.line("Price breakup", 0.1)
    page.answer("DCR_STATUS", "non-dcr", top=0.8)  # its box overlaps no line
    page.answer("VENDOR_NAME", "Example Solar")  # no box, and no line contains it
    wire, report = tc.map_page(page.reply(), 2)
    assert "dcr_declaration" not in wire and "vendor_name" not in wire
    assert [(r["alias"], r["kept"], r["why"]) for r in report] == [("DCR_STATUS", False, "not_on_page"),
                                                                   ("VENDOR_NAME", False, "not_on_page")]


@pytest.mark.parametrize("alias,text", [
    ("SYSTEM_CAPACITY", "three"), ("SYSTEM_CAPACITY", "3"), ("SYSTEM_CAPACITY", "3 kVA"),
    ("TOTAL_PAYABLE", "as discussed"), ("SUBSIDY_AMOUNT", "applicable"), ("PANEL_WATTAGE", "540"),
    ("PANEL_COUNT", "six"), ("INVERTER_CAPACITY", "on-grid"),
])
def test_answers_that_do_not_parse_as_their_type_are_dropped(alias, text):
    page = Page()
    page.line(f"Something {text}", 0.3)
    page.answer(alias, text, top=0.3)
    wire, report = tc.map_page(page.reply(), 1)
    assert report == [{"alias": alias, "confidence": 95.0, "kept": False, "why": "unparsed"}]
    assert wire["capacities"] == wire["prices"] == wire["subsidies"] == wire["module_groups"] == wire["inverters"] == []


def test_answers_below_the_threshold_are_dropped():
    page = Page()
    page.line("Total payable Rs 2,00,000", 0.3)
    page.answer("TOTAL_PAYABLE", "Rs 2,00,000", confidence=tc.CONFIDENCE_THRESHOLD - 0.1, top=0.3)
    page.answer("GST_AMOUNT", "Rs 10,000", confidence=tc.CONFIDENCE_THRESHOLD, top=0.3)
    page.unanswered("NET_COST")
    wire, report = tc.map_page(page.reply(), 1)
    assert [(p["kind"], p["raw"]) for p in wire["prices"]] == [("gst_amount", "Rs 10,000")]
    assert [(r["alias"], r["kept"], r["why"]) for r in report] == [
        ("TOTAL_PAYABLE", False, "low_confidence"), ("GST_AMOUNT", True, None)]
    assert tc.map_page(page.reply(), 1, threshold=0)[0]["prices"][0]["kind"] == "gross_total"


def test_counts_wattage_makes_and_inverters_form_one_group_per_page():
    page = Page()
    page.line("Solar panels: Example Solar EX-540, 540 Wp x 6 Nos", 0.3)
    page.line("Inverter: Example Inverter 3 kW", 0.4)
    page.answer("PANEL_COUNT", "6 Nos", top=0.3)
    page.answer("PANEL_WATTAGE", "540 Wp", top=0.3)
    page.answer("PANEL_MAKE_MODEL", "Example Solar EX-540", top=0.3)
    page.answer("INVERTER_MAKE_MODEL", "Example Inverter", top=0.4)
    page.answer("INVERTER_CAPACITY", "3 kW", top=0.4)
    c = contract(page, number=2)
    (group,) = c["module_groups"]
    assert group["option_id"] is None and group["count"]["value"] == 6
    assert group["wattage"]["value"]["parsed"] == "540" and group["make_model"]["value"] == "Example Solar EX-540"
    assert group["count"]["evidence_text"] == "Solar panels: Example Solar EX-540, 540 Wp x 6 Nos"
    (inverter,) = c["inverters"]
    assert inverter["make_model"]["value"] == "Example Inverter" and inverter["rating"]["value"]["unit"] == "kW"


def test_inverter_rating_in_kva_is_kept():
    page = Page()
    page.line("Inverter 5 kVA", 0.3)
    page.answer("INVERTER_CAPACITY", "5 kVA", top=0.3)
    assert contract(page)["inverters"][0]["rating"]["value"]["unit"] == "kVA"


def test_every_answer_is_kept_and_pages_are_never_resolved():
    one, two = Page(), Page()
    one.line("Total: Rs 1,90,000", 0.3)
    one.answer("TOTAL_PAYABLE", "Rs 1,90,000", top=0.3)
    two.line("Total amount payable Rs 1,95,000", 0.3)
    two.answer("TOTAL_PAYABLE", "Rs 1,95,000", top=0.3)
    quote = merge_batches([record(one, 1, 1), record(two, 2, 2)])
    total = quote["gross_total"]
    assert total["conflict"] is True
    assert [(c["value"]["raw"], c["page"]) for c in total["candidates"]] == [("Rs 1,90,000", 1), ("Rs 1,95,000", 2)]
    assert any(n["field"] == "gross_total" for n in quote["flags"]["needs_confirmation"])


def test_two_answers_on_one_page_are_both_kept():
    page = Page()
    page.line("Subsidy Rs 78,000", 0.3)
    page.line("Subsidy Rs 60,000", 0.5)
    page.answers("SUBSIDY_AMOUNT", ("Rs 78,000", 96.0, 0.3), ("Rs 60,000", 92.0, 0.5))
    quote = merge_batches([record(page, 1, 1)])
    assert quote["subsidy_unspecified"]["conflict"] is True


def test_the_same_value_on_two_pages_is_one_value_with_both_pages():
    one, two = Page(), Page()
    for p in (one, two):
        p.line("Total payable Rs 1,90,000", 0.3)
        p.answer("TOTAL_PAYABLE", "Rs 1,90,000", top=0.3)
    total = merge_batches([record(one, 1, 1), record(two, 3, 2)])["gross_total"]
    assert "conflict" not in total and [c["page"] for c in total["candidates"]] == [1, 3]


@pytest.mark.parametrize("line,kind", [
    ("PM Surya Ghar subsidy Rs 78,000", "subsidy_central"),
    ("MNRE CFA: Rs 78,000", "subsidy_central"),
    ("Central subsidy Rs 78,000", "subsidy_central"),
    ("TGREDCO state subsidy Rs 78,000", "subsidy_state"),
    ("NREDCAP subsidy Rs 78,000", "subsidy_state"),
    ("Subsidy (MNRE + TGREDCO) Rs 78,000", "subsidy_combined"),
    ("Subsidy Rs 78,000", "subsidy_unspecified"),
])
def test_subsidy_kind_comes_from_the_answer_line(line, kind):
    page = Page()
    page.line(line, 0.3)
    page.answer("SUBSIDY_AMOUNT", "Rs 78,000", top=0.3)
    assert [f[1] for f in facts(contract(page))] == [kind]


@pytest.mark.parametrize("line,text,basis", [
    ("System size 3 kWp", "3 kWp", "dc_kwp"), ("System size 3 kW DC", "3 kW", "dc_kwp"),
    ("System size 3 kW AC", "3 kW", "ac_kw"), ("System size 3 kW", "3 kW", "unspecified"),
    ("3 kW (AC) / 3.3 kWp (DC)", "3 kW", "unspecified"),
])
def test_capacity_basis_only_from_positive_evidence(line, text, basis):
    page = Page()
    page.line(line, 0.3)
    page.answer("SYSTEM_CAPACITY", text, top=0.3)
    flag = contract(page)["flags"]["model_proposed"]["capacity_basis"]
    assert flag["value"] == basis and flag["evidence_text"] == line


@pytest.mark.parametrize("lines,treatment", [
    (["Price inclusive of GST"], "included"), (["Total including GST Rs 2,00,000"], "included"),
    (["Rs 1,90,000 plus GST"], "excluded"), (["GST extra as applicable"], "excluded"),
    (["Price excluding GST"], "excluded"), (["GST @ 18% (extra) Rs 27,000"], "excluded"),
    (["GST @ 8.9% extra"], "excluded"), (["Price inclusive of GST", "Installation plus GST"], "unclear"),
    (["Price Rs 1,90,000"], None),
])
def test_gst_treatment_from_wording_on_the_page(lines, treatment):
    page = Page()
    for n, line in enumerate(lines):
        page.line(line, 0.3 + n / 10)
    flag = contract(page)["flags"]["model_proposed"]["gst_treatment"]
    assert (flag and flag["value"]) == treatment
    if flag:
        assert flag["evidence_text"] in lines and flag["page"] == 1


def test_give_it_up_and_flags_textract_never_sets():
    page = Page()
    page.line("Customers may choose the Give It Up option", 0.3)
    proposed = contract(page)["flags"]["model_proposed"]
    assert proposed["give_it_up"]["value"] is True
    assert proposed["give_it_up"]["evidence_text"] == "Customers may choose the Give It Up option"
    plain = contract(Page())["flags"]["model_proposed"]
    assert all(plain[name] is None for name in plain)
    for name in ("multiple_options", "extra_charges_complete", "net_cost_subsidy_basis"):
        assert proposed[name] is None


@pytest.mark.parametrize("answer,value", [("DCR", True), ("DCR panels", True), ("Domestic content", True),
                                          ("Non-DCR", False), ("non DCR modules", False), ("Yes", None)])
def test_dcr_declaration_from_the_dcr_answer(answer, value):
    page = Page()
    page.line(f"Panels: {answer}", 0.3)
    page.answer("DCR_STATUS", answer, top=0.3)
    f = contract(page)["dcr_declaration"]
    assert (f and f["value"]) == value


def test_gstin_is_never_taken_as_the_registration_number():
    page = Page()
    page.line("GSTIN: 36ABCDE1234F1Z5", 0.3)
    page.answer("VENDOR_REGISTRATION", "36ABCDE1234F1Z5", top=0.3)
    c = contract(page)
    assert c["vendor_registration"] is None
    assert c["vendor_gstin"] == {"value": "36ABCDE1234F1Z5", "evidence_text": "GSTIN: 36ABCDE1234F1Z5",
                                 "page": 1, "batch": 1}
    other = Page()
    other.line("MNRE registration no. PMSG/TS/2024/0012", 0.3)
    other.answer("VENDOR_REGISTRATION", "PMSG/TS/2024/0012", top=0.3)
    c = contract(other)
    assert c["vendor_registration"]["value"] == "PMSG/TS/2024/0012" and c["vendor_gstin"] is None


def test_gstin_reaches_the_merged_quote_as_evidence_only():
    page = Page()
    page.line("GSTIN 36ABCDE1234F1Z5", 0.3)
    page.answer("VENDOR_REGISTRATION", "36ABCDE1234F1Z5", top=0.3)
    quote = merge_batches([record(page, 1, 1)])
    assert quote["vendor_registration"] is None and quote["vendor_gstin"]["value"] == "36ABCDE1234F1Z5"


# --- tables ----------------------------------------------------------------------------------

def test_option_table_gives_one_option_per_row():
    page = Page()
    page.table([["System size (kW)", "Price (Rs)"], ["3 kW", "1,90,000"], ["5 kW", "2,90,000"]])
    c = contract(page, number=2)
    assert c["flags"]["model_proposed"]["multiple_options"]["value"] is True
    assert [o["option_id"] for o in c["options"]] == ["3 kW", "5 kW"]
    assert sorted(facts(c)) == [
        ("3 kW", "gross_total", "1,90,000", "3 kW | 1,90,000", 2), ("3 kW", "stated_capacity", "3 kW", "3 kW | 1,90,000", 2),
        ("5 kW", "gross_total", "2,90,000", "5 kW | 2,90,000", 2), ("5 kW", "stated_capacity", "5 kW", "5 kW | 2,90,000", 2)]
    quote = merge_batches([record(page, 2, 1)])
    assert set(quote["option_fields"]) == {"3 kW", "5 kW"} and quote["gross_total"] is None


def test_a_bom_with_an_inverter_row_and_a_panel_row_is_not_an_option_table():
    page = Page()
    page.table([["Item", "Capacity", "Amount"], ["Inverter", "3 kW", "40,000"], ["Solar panels", "3.3 kWp", "1,20,000"]])
    c = contract(page)
    assert c["flags"]["model_proposed"]["multiple_options"] is None and c["options"] == [] and c["facts"] == []


@pytest.mark.parametrize("rows", [
    [["System size (kW)", "Price (Rs)"], ["3 kW", "1,90,000"]],  # one row
    [["System size (kW)", "Price (Rs)"], ["3 kW", "1,90,000"], ["3 kW", "1,95,000"]],  # same capacity twice
    [["Description", "Price (Rs)"], ["3 kW", "1,90,000"], ["5 kW", "2,90,000"]],  # header doesn't name capacity
    [["System size (kW)", "Remarks"], ["3 kW", "on-grid"], ["5 kW", "on-grid"]],  # no amount
    [["System size (kW)", "Qty"], ["3 kW", "6"], ["5 kW", "10"]],  # a number that isn't a price
])
def test_tables_that_are_not_option_tables(rows):
    page = Page()
    page.table(rows)
    c = contract(page)
    assert c["flags"]["model_proposed"]["multiple_options"] is None and c["options"] == []


def test_capacity_without_a_unit_takes_the_header_unit():
    page = Page()
    page.table([["Capacity (kWp)", "Total cost"], ["3", "1,90,000"], ["5", "2,90,000"]])
    c = contract(page)
    assert [o["option_id"] for o in c["options"]] == ["3", "5"]
    caps = sorted((f["option_id"], f["field"]["value"]["parsed"]) for f in c["facts"] if f["name"] == "stated_capacity")
    assert caps == [("3", "3"), ("5", "5")]


def test_merged_cells_spread_their_text_over_the_cells_they_cover():
    page = Page()
    page.table([["System size (kW)", "Price (Rs)"], ["3 kW", "1,90,000"], ["5 kW", ""]], merged=[(2, 2, 2, 1)])
    c = contract(page)
    assert sorted(f[2] for f in facts(c, "gross_total")) == ["1,90,000", "1,90,000"]


def test_cell_text_comes_from_its_words():
    page = Page()
    page.table([["System size (kW)", "Price incl. GST (Rs)"], ["3   kW", "1,90,000 /-"], ["5 kW", "2,90,000 /-"]])
    assert [o["option_id"] for o in contract(page)["options"]] == ["3 kW", "5 kW"]


# --- calls -----------------------------------------------------------------------------------

class FakeTextract:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.requests = reply, error, []

    def analyze_document(self, **request):
        from botocore.exceptions import ClientError

        self.requests.append(request)
        if self.error:
            raise ClientError({"Error": {"Code": self.error, "Message": "synthetic"}}, "AnalyzeDocument")
        return self.reply


def test_read_page_returns_a_record_like_a_nova_batch():
    page = Page()
    page.line("Total payable Rs 1,90,000", 0.3)
    page.answer("TOTAL_PAYABLE", "Rs 1,90,000", top=0.3)
    client = FakeTextract(page.reply())
    rec = tc.read_page(client, PageImage(5, JPEG, 0, 0, 0, 0), 3)
    assert client.requests[0]["Document"]["Bytes"] == JPEG
    assert rec["batch"] == 3 and rec["pages"] == [5] and rec["model_id"] == "textract"
    assert rec["usage"] == {"pages": 1}
    assert rec["contract"]["facts"][0]["field"]["page"] == 5
    assert rec["confidence"] == [{"alias": "TOTAL_PAYABLE", "confidence": 95.0, "kept": True, "why": None}]


@pytest.mark.parametrize("code", ["BadDocumentException", "UnsupportedDocumentException",
                                  "DocumentTooLargeException", "InvalidParameterException"])
def test_a_bad_page_fails_that_page_only(code):
    with pytest.raises(tc.ExtractionFailure) as e:
        tc.read_page(FakeTextract(error=code), PageImage(2, JPEG, 0, 0, 0, 0), 1)
    assert not e.value.stops_run and e.value.code == code
    assert e.value.record["pages"] == [2]


@pytest.mark.parametrize("code,stop", [
    ("ThrottlingException", "ThrottlingException"), ("ProvisionedThroughputExceededException", "ThrottlingException"),
    ("LimitExceededException", "ThrottlingException"), ("AccessDeniedException", "AccessDeniedException"),
    ("ExpiredTokenException", "ExpiredTokenException"),
])
def test_throttling_and_access_errors_stop_the_job(code, stop):
    with pytest.raises(tc.ExtractionFailure) as e:
        tc.read_page(FakeTextract(error=code), PageImage(1, JPEG, 0, 0, 0, 0), 1)
    assert e.value.stops_run and e.value.code == stop


def test_page_over_the_textract_size_limit_is_not_sent():
    client = FakeTextract({"Blocks": []})
    big = PageImage(1, b"\xff\xd8\xff" + bytes(tc.MAX_IMAGE_BYTES), 0, 0, 0, 0)
    assert tc.request_size([big]) > tc.MAX_IMAGE_BYTES
    with pytest.raises(tc.ExtractionFailure) as e:
        tc.read_page(client, big, 1)
    assert e.value.kind == "size_limit" and client.requests == []


def test_cost_estimate():
    assert tc.estimated_cost(7) == pytest.approx(0.14)
    assert tc.PRICE_PER_PAGE_USD == pytest.approx(0.020)


# --- live replies for the synthetic samples (Textract run once on S1, S2 and S3) -----------

FIXTURES = Path(__file__).parent / "fixtures" / "textract"


def sample(sid, n):
    return json.loads((FIXTURES / f"{sid}-page-{n}.json").read_text(encoding="utf-8"))


def all_fields(c):
    yield from (f["field"] for f in c["facts"])
    for name in ("vendor_name", "quote_date", "vendor_registration", "dcr_declaration", "vendor_gstin"):
        if c.get(name):
            yield c[name]
    for item in c["module_groups"] + c["inverters"]:
        yield from (v for k, v in item.items() if isinstance(v, dict) and "evidence_text" in v)
    yield from (f for f in c["flags"]["model_proposed"].values() if f)


@pytest.mark.parametrize("sid", ["S1", "S2", "S3"])
@pytest.mark.parametrize("n", [1, 2])
def test_sample_replies_map_only_to_text_on_their_own_page(sid, n):
    reply = sample(sid, n)
    lines = {" ".join(b["Text"].split()) for b in reply["Blocks"] if b["BlockType"] == "LINE"}
    c = tc.to_contract(tc.map_page(reply, n + 10)[0], n)
    fields = list(all_fields(c))
    for f in fields:
        assert f["page"] == n + 10 and f["batch"] == n
        assert all(line in lines for line in f["evidence_text"].split("\n"))


def test_sample_reports_carry_no_answer_text():
    for sid in ("S1", "S2", "S3"):
        for n in (1, 2):
            for entry in tc.map_page(sample(sid, n), n)[1]:
                assert set(entry) == {"alias", "confidence", "kept", "why"}


def test_what_textract_gets_from_sample_s2():
    """S2 states 5 panels of 500 W, 3 kW and a subsidy of Rs 85,800. At the chosen
    threshold the mapping keeps the panel count (and the net cost) only: the wattage
    answer is low and in kWp, the capacity answer has no unit, and the subsidy answer
    carries its label, so it doesn't parse as an amount. Recorded, not tuned to."""
    quote = merge_batches([{"batch": n, "pages": [n], "model_id": "textract",
                            "contract": tc.to_contract(tc.map_page(sample("S2", n), n)[0], n)} for n in (1, 2)])
    assert [g["count"]["value"] for g in quote["module_groups"]] == [5]
    assert quote["module_groups"][0]["wattage"] is None and quote["stated_capacity"] is None
    assert all(quote[k] is None for k in ("subsidy_central", "subsidy_state", "subsidy_combined", "subsidy_unspecified"))
    assert quote["net_cost"]["value"]["parsed"] == "80050"
