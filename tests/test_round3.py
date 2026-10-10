"""General rules behind the red-team's round-3 cases (24 to 28): scope wording in every
source, relational price wording, supplier GSTINs from section context only, no loose count
copied into several rows, and rows whose option can't be read kept out."""

import pytest

from extract import textract_client as tc
from extract import textract_sources as src
from extract.merge import merge_batches
from textract_pages import Page


def contract(page):
    wire, _ = tc.map_page(page.reply(), 1, sources=tc.SOURCES)
    return tc.to_contract(wire, 1)


def certain(f):
    return f is not None and not f.get("conflict") and f.get("value") is not None


def price_facts(c):
    return {f["name"] for f in c["facts"]}


# --- 24: one scope rule for every source --------------------------------------------------------

SCOPE = ["not supplied", "not provided", "by customer", "customer scope", "in customer's scope", "excluded",
         "optional", "not in scope", "not included"]


@pytest.mark.parametrize("words", SCOPE)
def test_scope_wording_is_out_of_scope(words):
    assert src.out_of_scope(f"Solar panels {words}: 6 Nos 550 Wp")


@pytest.mark.parametrize("words", SCOPE)
def test_a_line_out_of_scope_gives_no_component(words):
    page = Page()
    page.line(f"Solar panels {words}: 6 Nos 550 Wp", 0.3)
    page.line(f"Inverter {words}: 3 kW", 0.4)
    c = contract(page)
    assert not any(certain(g.get("count")) or certain(g.get("wattage")) for g in c["module_groups"])
    assert not any(certain(i.get("rating")) for i in c["inverters"])


@pytest.mark.parametrize("words", SCOPE)
def test_a_table_row_out_of_scope_gives_no_component(words):
    page = Page()
    page.table([["Description", "Qty", "Wattage", "Remarks"],
                ["Solar module", "6", "550 Wp", words]])
    assert not any(certain(g.get("count")) for g in contract(page)["module_groups"])


@pytest.mark.parametrize("words", SCOPE)
def test_a_query_answer_on_a_line_out_of_scope_is_dropped(words):
    page = Page()
    page.line(f"Panels {words}: 6 Nos", 0.3)
    page.answer("PANEL_COUNT", "6 Nos", top=0.3)
    assert not any(certain(g.get("count")) for g in contract(page)["module_groups"])


@pytest.mark.parametrize("words", SCOPE)
def test_an_option_row_out_of_scope_gives_no_price(words):
    page = Page()
    page.table([["Option", "Capacity", "Total price (Rs)", "Remarks"],
                ["A", "3 kW", "1,80,000", words],
                ["B", "5 kW", "2,90,000", ""]])
    c = contract(page)
    assert not [p for p in c["option_facts"] if p.get("option_id") == "A"] if "option_facts" in c else True
    assert not any(f["option_id"] == "A" and f["name"] == "gross_total" for f in c["facts"])


# --- 25: relational wording before role keywords -------------------------------------------------

@pytest.mark.parametrize("label", ["Net cost before subsidy", "Net payable before subsidy", "Net amount excluding subsidy",
                                   "Net price without subsidy"])
def test_net_wording_with_before_subsidy_has_no_role(label):
    assert src.role(label) is None
    page = Page()
    page.line(f"{label} Rs 2,00,000", 0.3)
    page.answer("NET_COST", "Rs 2,00,000", top=0.3)
    page.answer("TOTAL_PAYABLE", "Rs 2,00,000", top=0.3)
    assert not {"net_cost", "gross_total"} & price_facts(contract(page))


@pytest.mark.parametrize("label,role", [("Total before subsidy", "gross_total"), ("Net cost after subsidy", "net_cost"),
                                        ("Net payable", "net_cost"), ("Amount payable after subsidy", "net_cost")])
def test_plain_relational_wording_keeps_its_role(label, role):
    assert src.role(label) == role


# --- 26: supplier GSTINs from section context ---------------------------------------------------

GSTIN = "GSTIN: 36AABCU9603R1ZO"


def state(*lines):
    page = Page()
    for n, (text, top, left) in enumerate(lines):
        page.line(text, top, left=left, width=0.4)
    return (contract(page)["supplier_gst_state"] or {}).get("value")


@pytest.mark.parametrize("heading", ["Buyer", "Customer", "Bill to", "Ship to", "Customer details", "To,"])
def test_a_gstin_under_a_buyer_heading_is_the_buyers(heading):
    assert state((heading, 0.05, 0.05), (GSTIN, 0.1, 0.05)) is None


@pytest.mark.parametrize("heading", ["Supplier", "From", "Our details", "Vendor details", "Seller"])
def test_a_gstin_under_a_supplier_heading_gives_the_state(heading):
    assert state((heading, 0.5, 0.05), (GSTIN, 0.55, 0.05)) == "Telangana"


def test_a_gstin_labelled_as_ours_gives_the_state_anywhere():
    assert state(("Our GSTIN: 36AABCU9603R1ZO", 0.8, 0.05)) == "Telangana"


def test_the_letterhead_is_the_block_before_the_first_heading():
    assert state(("Example Solar Pvt Ltd", 0.02, 0.05), (GSTIN, 0.06, 0.05), ("To,", 0.2, 0.05),
                 ("Customer name", 0.24, 0.05)) == "Telangana"


def test_a_gstin_on_a_page_with_no_headings_is_unclear():
    assert state((GSTIN, 0.1, 0.05)) is None
    assert state(("Quotation", 0.02, 0.05), (GSTIN, 0.5, 0.05)) is None


def test_a_buyer_gstin_after_a_supplier_block_is_still_the_buyers():
    assert state(("Supplier", 0.05, 0.05), ("Example Solar Pvt Ltd", 0.08, 0.05), ("Bill to", 0.2, 0.05),
                 (GSTIN, 0.24, 0.05)) is None


def test_side_by_side_supplier_and_buyer_blocks_are_unclear():
    assert state(("Supplier", 0.05, 0.05), ("Buyer", 0.05, 0.55), (GSTIN, 0.1, 0.05),
                 ("GSTIN: 29AABCU9603R1ZJ", 0.1, 0.55)) is None
    assert state(("Buyer", 0.05, 0.05), ("Supplier", 0.05, 0.55), (GSTIN, 0.1, 0.55)) is None


# --- 27: no broadcast of a loose count -----------------------------------------------------------

def test_a_loose_count_is_never_copied_into_several_rows():
    page = Page()
    page.line("Solar panels: 6 Nos", 0.1)
    page.table([["Description", "Qty", "Wattage"], ["Solar module", "", "550 Wp"], ["Solar module", "", "450 Wp"]])
    gs = contract(page)["module_groups"]
    assert len(gs) == 2 and not any(certain(g.get("count")) for g in gs)


def test_a_loose_count_that_disagrees_with_row_counts_is_a_conflict_not_a_value():
    page = Page()
    page.line("Solar panels: 6 Nos", 0.1)
    page.table([["Description", "Qty", "Wattage"], ["Solar module", "3", "550 Wp"], ["Solar module", "2", "450 Wp"]])
    gs = contract(page)["module_groups"]
    assert all(g["count"].get("conflict") for g in gs)


# --- 28: rows without a readable option ----------------------------------------------------------

@pytest.mark.parametrize("cell,confidence", [("B", 20.0), ("", 90.0)])
def test_a_row_whose_option_cant_be_read_is_kept_out_and_the_options_need_confirmation(cell, confidence):
    page = Page()
    page.table([["Option", "Description", "Qty", "Wattage"], ["A", "Solar module", "6", "550 Wp"],
                [cell, "Solar module", "6", "500 Wp"]], confidence={(3, 1): confidence})
    c = contract(page)
    assert [(g["option_id"], g["wattage"]["value"]["parsed"]) for g in c["module_groups"]] == [("A", "550")]
    q = merge_batches([{"batch": 1, "pages": [1], "model_id": "textract", "contract": c}])
    assert q["flags"]["model_proposed"]["multiple_options"]["conflict"]
    assert q["flags"]["needs_confirmation"]


def test_rows_with_readable_options_bind_to_them():
    page = Page()
    page.table([["Option", "Description", "Qty", "Wattage"], ["A", "Solar module", "6", "550 Wp"],
                ["B", "Solar module", "6", "500 Wp"]])
    c = contract(page)
    assert sorted(g["option_id"] for g in c["module_groups"]) == ["A", "B"]
    assert c["flags"]["model_proposed"]["multiple_options"]["value"] is True
