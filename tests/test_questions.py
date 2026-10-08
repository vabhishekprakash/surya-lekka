import re

from conftest import amount_field, field_v1

from checks import run_checks
from checks.questions import INTRO, OUTRO, TEMPLATES, vendor_message, vendor_questions

ACCUSATORY = re.compile(r"\b(fraud|cheat|lie|lying|illegal|unregistered|wrong|scam|mislead|overcharg)", re.I)


def test_templates_are_polite_and_plain():
    for text in [INTRO, OUTRO, *TEMPLATES.values()]:
        assert not ACCUSATORY.search(text)
        assert "—" not in text and "–" not in text


def test_no_questions_for_clean_quote(quote_v1):
    assert run_checks(quote_v1)["vendor_message"] is None


def test_questions_from_c2_and_c4(quote_v1):
    quote_v1["subsidy_central"] = amount_field("Rs. 85,800")
    quote_v1["vendor_registration"] = None
    quote_v1["dcr_declaration"] = None
    r = run_checks(quote_v1)
    assert [q["id"] for q in r["questions"]] == ["subsidy_higher", "dcr", "vendor_registration"]
    assert "Rs 85,800" in r["questions"][0]["text"] and "Rs 78,000" in r["questions"][0]["text"]
    msg = r["vendor_message"]
    assert msg.startswith(INTRO) and msg.endswith(OUTRO)
    assert "1. The quote shows a central subsidy" in msg and "3. Could you share" in msg


def test_questions_deduplicated_and_ordered():
    findings = [
        {"check_id": "C4_missing_details", "question": "net_meter", "question_params": {}},
        {"check_id": "C4_missing_details", "question": "exact_capacity", "question_params": {}},
        {"check_id": "C2_central_subsidy", "question": "exact_capacity", "question_params": {}},
        {"check_id": "C1_capacity", "question": "panel_count", "question_params": {}},
    ]
    assert [q["id"] for q in vendor_questions(findings)] == ["exact_capacity", "net_meter"]


def test_user_gates_are_not_vendor_questions(quote_v1):
    quote_v1["flags"]["user_confirmed"]["state"] = None
    assert vendor_message(run_checks(quote_v1)["findings"]) is None


def test_choice_question_lists_options(quote_v1):
    g = quote_v1["module_groups"][0]
    g["make_model_alternatives"] = [field_v1("Sample Energy SE-545", "or Sample Energy SE-545")]
    (q,) = run_checks(quote_v1)["questions"]
    assert q["id"] == "module_choice"
    assert "(Example PV EXM-545 or Sample Energy SE-545)" in q["text"]
