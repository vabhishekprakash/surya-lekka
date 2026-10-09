"""Further sources for the Textract reading: label-anchored LINE patterns, bill-of-materials
and label | amount table rows, typed values inside query answers, FORMS key-value pairs,
AnalyzeExpense fields and the GSTIN's registration state. Each adversarial case below
was written before the code it guards."""

import pytest

from checks import run_checks
from extract import textract_client as tc
from extract.merge import merge_batches
from textract_pages import Page, expense

ALL = tc.ALL_SOURCES


def contract(page, number=1, sources=ALL, reply_expense=None, batch=1):
    wire, _ = tc.map_page(page.reply(), number, sources=sources, expense=reply_expense)
    return tc.to_contract(wire, batch)


def quote(*pages, sources=ALL):
    records = [{"batch": n, "pages": [n], "model_id": "textract", "contract": contract(p, n, sources, batch=n)}
               for n, p in enumerate(pages, 1)]
    return merge_batches(records)


def facts(c, name):
    return [(f["option_id"], f["field"]["value"]["raw"]) for f in c["facts"] if f["name"] == name]


def amounts(c, name):
    from decimal import Decimal

    def text(value):
        d = Decimal(value)
        return str(int(d)) if d == d.to_integral_value() else str(d)
    return sorted(text(f["field"]["value"]["parsed"]) for f in c["facts"] if f["name"] == name)


def groups(c):
    def v(f):
        return None if f is None else (f["value"]["parsed"] if isinstance(f["value"], dict) else f["value"])
    return [(v(g["count"]), v(g["wattage"]), v(g["make_model"])) for g in c["module_groups"]]


# --- adversarial cases --------------------------------------------------------------------------

def test_adv_sno_hsn_and_rate_are_never_the_quantity():
    page = Page()
    page.table([["S.No.", "Description", "HSN/SAC", "Qty", "Rate", "Amount"],
                ["1", "Solar PV module 540 Wp", "854140", "6 Nos", "25,000", "1,50,000"]])
    assert groups(contract(page)) == [(6, "540", None)]


def test_adv_gst_rate_beside_the_gst_amount():
    page = Page()
    page.line("GST @ 13.8% Rs. 20,700", 0.3)
    c = contract(page)
    assert amounts(c, "gst_amount") == ["20700"]
    page = Page()
    page.line("GST @ 13.8%", 0.3)
    assert amounts(contract(page), "gst_amount") == []  # a percentage is never a rupee amount


def test_adv_two_module_rows_of_different_wattage_stay_two_groups():
    page = Page()
    page.table([["Description", "Make", "Rating", "Qty"],
                ["Solar module", "Example PV", "540 Wp", "4"],
                ["Solar module", "Example PV", "545 Wp", "2"],
                ["Inverter", "Example Inverters", "3 kW", "1"]])
    c = contract(page)
    assert groups(c) == [(4, "540", "Example PV"), (2, "545", "Example PV")]
    assert [i["rating"]["value"]["parsed"] for i in c["inverters"]] == ["3"]


def test_adv_two_option_tables_on_one_page_keep_their_options_apart():
    page = Page()
    page.table([["System size (kW)", "Price (Rs)"], ["3 kW", "1,90,000"], ["5 kW", "2,90,000"]])
    page.table([["System size (kW)", "Net cost (Rs)"], ["3 kW", "1,12,000"], ["5 kW", "2,12,000"]])
    c = contract(page)
    assert sorted(facts(c, "gross_total")) == [("3 kW", "1,90,000"), ("5 kW", "2,90,000")]
    assert sorted(facts(c, "net_cost")) == [("3 kW", "1,12,000"), ("5 kW", "2,12,000")]
    assert groups(c) == [] and facts(c, "base_price") == []


def test_adv_a_merged_header_cell_is_rejected():
    page = Page()
    page.table([["Description", "Qty", "Rate"], ["Solar module 540 Wp", "6", "25,000"]], merged=[(1, 2, 1, 2)])
    assert groups(contract(page)) == []


def test_adv_lakh_notation():
    page = Page()
    page.line("Grand total Rs. 1.85 lakh", 0.3)
    assert amounts(contract(page), "gross_total") == ["185000"]


def test_adv_net_metering_charges_extra():
    page = Page()
    page.line("Net metering charges extra", 0.3)
    page.line("Total payable Rs 1,90,000", 0.4)
    c = contract(page)
    assert amounts(c, "gross_total") == ["190000"] and amounts(c, "net_cost") == []
    assert c["extra_charges"] == []


def test_adv_subsidy_not_included():
    page = Page()
    page.line("Subsidy not included Rs 78,000", 0.3)
    page.line("No subsidy", 0.4)
    c = contract(page)
    assert [f for f in c["facts"] if f["name"].startswith("subsidy")] == []


def test_adv_the_same_amount_as_gst_and_as_a_charge():
    page = Page()  # printed twice, so both fields hold it
    page.line("GST Rs 2,500", 0.3)
    page.line("Base price Rs 2,500", 0.4)
    c = contract(page)
    assert amounts(c, "gst_amount") == ["2500"] and amounts(c, "base_price") == ["2500"]
    page = Page()  # printed once: the query asked for the base price reads the GST line
    page.line("GST Rs. 13,350", 0.3)
    page.answer("PRICE_BEFORE_GST", "Rs. 13,350", top=0.3)
    page.answer("GST_AMOUNT", "13,350", top=0.3)
    c = contract(page)
    assert amounts(c, "gst_amount") == ["13350"] and amounts(c, "base_price") == []


def test_adv_3kw_and_3000w_are_not_a_conflict():
    one, two = Page(), Page()
    one.line("System capacity: 3 kW", 0.3)
    two.line("Plant capacity 3000 W", 0.3)
    three = Page()
    three.line("System capacity 3.0 kWp", 0.3)
    q = quote(one, two, three)
    assert "conflict" not in q["stated_capacity"] and len(q["stated_capacity"]["candidates"]) == 3


def test_adv_expense_total_that_is_really_the_subsidy_line():
    page = Page()
    page.line("Subsidy Rs 78,000", 0.3)
    reply = expense([("TOTAL", "Subsidy", "78,000", 0.3)])
    c = contract(page, reply_expense=reply)
    assert amounts(c, "gross_total") == [] and amounts(c, "subsidy_unspecified") == ["78000"]


# --- conflicts, inference and the GSTIN ---------------------------------------------------------

def test_a_query_and_a_pattern_that_disagree_keep_the_findings_waiting():
    page = Page()
    page.line("Grand total Rs 2,00,000", 0.3)
    page.line("Total amount payable Rs 1,90,000", 0.5)
    page.answer("TOTAL_PAYABLE", "Rs 1,90,000", top=0.5)
    q = quote(page)
    assert q["gross_total"]["conflict"] is True
    answers = {"state": "Telangana", "consumer_type": "individual_household", "gst_treatment": "included",
               "extra_charges_complete": True, "portal_application_on_or_after_cutoff": True,
               "first_system": True, "prior_central_subsidy": False, "give_it_up": False,
               "net_cost_subsidy_basis": "central", "multiple_options": False}
    q["base_price"] = {"value": {"raw": "1,90,000", "parsed": "190000", "parse_status": "ok"},
                       "evidence_text": "x", "page": 1, "batch": 1}
    q["net_cost"] = {"value": {"raw": "1,12,000", "parsed": "112000", "parse_status": "ok"},
                     "evidence_text": "x", "page": 1, "batch": 1}
    q["subsidy_central"] = {"value": {"raw": "78,000", "parsed": "78000", "parse_status": "ok"},
                            "evidence_text": "x", "page": 1, "batch": 1}
    result = run_checks(q, {"confirmations": answers})
    status = {f["check_id"]: f["status"] for f in result["findings"]}
    assert status["C3_gross_total"] == "needs_confirmation" and status["C3_net_cost"] == "needs_confirmation"
    resolved = run_checks(q, {"confirmations": answers, "corrections": {"gross_total": "1,90,000"}})
    assert {f["check_id"]: f["status"] for f in resolved["findings"]}["C3_gross_total"] == "consistent"


def test_amounts_are_never_worked_out_from_a_total_or_a_difference():
    page = Page()
    page.line("Base price Rs 1,50,000", 0.3)
    page.line("GST Rs 13,350", 0.4)
    c = contract(page)
    assert amounts(c, "gross_total") == [] and amounts(c, "net_cost") == []


def test_gstin_gives_only_the_suppliers_gst_registration_state():
    page = Page()
    page.line("GSTIN: 36AABCU9603R1ZO", 0.1)
    c = contract(page)
    state = c["supplier_gst_state"]
    assert state["value"] == "Telangana" and state["source"] == "gst_registration_state"
    assert state["evidence_text"] == "GSTIN: 36AABCU9603R1ZO"
    q = quote(page)
    assert q["supplier_gst_state"]["value"] == "Telangana" and q["supplier_gst_state"]["source"] == "gst_registration_state"
    assert "state" not in (q["flags"].get("user_confirmed") or {})  # never the household's state


@pytest.mark.parametrize("line", ["GSTIN: 36AABCU9603R1ZX", "GSTIN: 28AABCU9603R1ZL", "GSTIN 36AABCU9603R"])
def test_an_invalid_or_unknown_gstin_gives_no_state(line):
    page = Page()
    page.line(line, 0.1)
    assert contract(page).get("supplier_gst_state") is None


# --- each source ---------------------------------------------------------------------------------

@pytest.mark.parametrize("line,name,value", [
    ("Total system capacity: 3.3 kWp", "stated_capacity", "3.3"),
    ("Grand Total Rs. 1,65,850/-", "gross_total", "165850"),
    ("Basic price ₹1,50,000", "base_price", "150000"),
    ("Central subsidy (CFA) Rs. 85,800", "subsidy_central", "85800"),
    ("Net cost after central subsidy Rs. 80,050", "net_cost", "80050"),
    ("Total amount (incl. GST) Rs 1,90,000", "gross_total", "190000"),
])
def test_line_patterns_take_a_value_named_by_its_own_line(line, name, value):
    page = Page()
    page.line(line, 0.3)
    c = contract(page)
    assert [f["field"]["value"]["parsed"] for f in c["facts"] if f["name"] == name] == [value]
    assert all(f["field"]["evidence_text"] == line for f in c["facts"])


@pytest.mark.parametrize("line", ["Rs 1,65,850", "Total GST Rs 13,350", "Total Rs 1,90,000 or Rs 2,00,000",
                                  "System capacity 3 or 5 kW", "System capacity 3-5 kW",
                                  "Inverter capacity 3 kW", "Subsidy excluding state top-up"])
def test_line_patterns_leave_unnamed_or_ambiguous_values_alone(line):
    page = Page()
    page.line(line, 0.3)
    c = contract(page)
    assert c["facts"] == []


def test_line_patterns_for_panels_and_the_date():
    page = Page()
    page.line("No. of modules: 5", 0.2)
    page.line("Solar modules: Example PV EXM-500, 500 W", 0.3)
    page.line("Quotation No. EXS-S2 | Date 02/10/2026", 0.1)
    c = contract(page)
    assert groups(c) == [(5, "500", None)]
    assert c["quote_date"]["value"] == "02/10/2026"


@pytest.mark.parametrize("answer,line,value", [
    ("3 kW on-grid", "Inverter: Example Inverters EXI-3K, 3 kW on-grid", "3"),
])
def test_an_answer_with_extra_words_gives_its_one_typed_value(answer, line, value):
    page = Page()
    page.line(line, 0.3)
    page.answer("INVERTER_CAPACITY", answer, top=0.3)
    c = contract(page)
    assert [i["rating"]["value"]["parsed"] for i in c["inverters"]] == [value]
    assert c["inverters"][0]["rating"]["evidence_text"] == line


@pytest.mark.parametrize("alias,answer", [("INVERTER_CAPACITY", "3 or 5 kW"), ("INVERTER_CAPACITY", "Inverter 3-5 kW"),
                                          ("TOTAL_PAYABLE", "18% GST"), ("SUBSIDY_AMOUNT", "Rs 78,000 or 85,800")])
def test_an_answer_with_two_values_or_a_percentage_gives_nothing(alias, answer):
    page = Page()
    page.line(f"Something {answer}", 0.3)
    page.answer(alias, answer, top=0.3)
    c = contract(page, sources=frozenset({"queries", "answer_values"}))
    assert c["facts"] == [] and c["inverters"] == []


def test_label_amount_table_rows_tell_the_roles_apart():
    page = Page()
    page.table([["Particulars", "Amount (Rs)"], ["Basic value", "1,50,000"], ["GST @ 8.9%", "13,350"],
                ["Grand total", "1,65,850"], ["Central subsidy", "85,800"], ["Cost after subsidy", "80,050"],
                ["Rate per kWp", "50,000"]], header_rows=1)
    c = contract(page)
    assert amounts(c, "base_price") == ["150000"] and amounts(c, "gst_amount") == ["13350"]
    assert amounts(c, "gross_total") == ["165850"] and amounts(c, "subsidy_central") == ["85800"]
    assert amounts(c, "net_cost") == ["80050"] and len(c["facts"]) == 5


def test_forms_pairs_follow_the_line_rules():
    page = Page()
    page.line("Grand Total: Rs 1,65,850", 0.3)
    page.form("Grand Total:", "Rs 1,65,850", 0.3)
    page.line("GST Rate: 18%", 0.4)
    page.form("GST Rate:", "18%", 0.4)
    c = contract(page, sources=frozenset({"forms"}))
    assert amounts(c, "gross_total") == ["165850"] and amounts(c, "gst_amount") == []


def test_expense_summary_and_line_items_go_through_the_same_rules():
    page = Page()
    page.line("Sub Total Rs 1,50,000", 0.2)
    page.line("CGST + SGST Rs 13,350", 0.3)
    page.line("Grand Total Rs 1,65,850", 0.4)
    page.line("Solar PV module 540 Wp 6 Nos", 0.6)
    reply = expense([("SUBTOTAL", "Sub Total", "1,50,000", 0.2), ("TAX", "CGST + SGST", "13,350", 0.3),
                     ("TOTAL", "Grand Total", "1,65,850", 0.4), ("TAX", "GST", "18%", 0.3)],
                    [[("ITEM", "Solar PV module 540 Wp", 0.6), ("QUANTITY", "6", 0.6)]])
    c = contract(page, sources=frozenset({"expense"}), reply_expense=reply)
    assert amounts(c, "base_price") == ["150000"] and amounts(c, "gst_amount") == ["13350"]
    assert amounts(c, "gross_total") == ["165850"] and groups(c) == [(6, "540", None)]


# --- rules added after the first acceptance round --------------------------------------------------

def test_a_count_of_one_from_a_table_or_line_is_a_set_not_a_panel_count():
    page = Page()
    page.table([["Description", "Qty"], ["Solar module 540 Wp (complete set)", "1"]])
    page.line("Solar modules 540 Wp - 1 Nos", 0.6)
    assert [g[0] for g in groups(contract(page))] in ([], [None])


def test_the_gstin_state_is_kept_apart_from_the_vendors_address_state():
    page = Page()
    page.line("GSTIN: 36AABCU9603R1ZO", 0.1)
    c = contract(page)
    assert c["vendor_state"] is None
    assert c["supplier_gst_state"]["value"] == "Telangana"


def test_only_query_answers_propose_what_the_system_size_measures():
    page = Page()
    page.line("System capacity 3 kWp", 0.3)
    c = contract(page)
    assert facts(c, "stated_capacity") == [(None, "3 kWp")]
    assert c["flags"]["model_proposed"]["capacity_basis"] is None


def test_a_forms_pair_counts_only_when_key_and_value_share_a_line():
    page = Page()
    page.line("Grand Total:", 0.3)
    page.line("Rs 1,65,850", 0.32)
    page.form("Grand Total:", "Rs 1,65,850", 0.32)
    assert amounts(contract(page, sources=frozenset({"forms"})), "gross_total") == []


@pytest.mark.parametrize("line", ["Net meter charges Rs. 2,500 (included in Grand Total)",
                                  "Structure charges Rs 12,000 (part of the total)",
                                  "Installation Rs 5,000 included in the total amount",
                                  "Civil work Rs 8,000 outside the total"])
def test_a_charge_that_mentions_the_total_is_not_the_total(line):
    page = Page()
    page.line(line, 0.3)
    assert contract(page)["facts"] == []
