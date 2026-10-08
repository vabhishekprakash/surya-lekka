"""Turn C2 and C4 findings into a short list of questions for the vendor.

Fixed templates only. Placeholders are filled with numbers and labels taken
from the findings; nothing else is generated.
"""

INTRO = "Hello, and thanks for the quotation. Before we decide, could you help with a few questions?"
OUTRO = "Thank you."

# Order here is the order questions appear in the message.
TEMPLATES = {
    "subsidy_breakdown": "Could you show the central subsidy and any state subsidy as separate amounts?",
    "subsidy_higher": ("The quote shows a central subsidy of Rs {stated}. For {dc_kwp} kWp of panels, the "
                       "central rule we checked gives Rs {rule}. How did you work out the subsidy amount?"),
    "subsidy_lower": ("The quote shows a central subsidy of Rs {stated}. For {dc_kwp} kWp of panels, the "
                      "central rule we checked allows up to Rs {rule}. Could you explain the difference?"),
    "dc_capacity": "What is the total DC capacity of the solar panels, in kWp?",
    "exact_capacity": ("The quote gives a range for the panels. What is the exact panel wattage, and the "
                       "total capacity in kWp?"),
    "panel_wattage": "What is the wattage of each solar panel?",
    "panel_count": "How many solar panels are included?",
    "module_model": "Which make and model of solar panel will you install?",
    "module_choice": "The quote lists more than one panel option ({options}). Which make and model will you install?",
    "dcr": ("Could you confirm in writing that the panels are DCR (domestic content) panels with domestic "
            "cells? The central subsidy requires this."),
    "inverter_model": "Which make and model of inverter will you install?",
    "inverter_rating": "What is the inverter's rating in kW?",
    "inverter_choice": "The quote lists more than one inverter option ({options}). Which one will you install?",
    "vendor_registration": "Could you share your vendor registration number? We couldn't find it on the quote.",
    "gst_basis": "Does the price include GST? If not, what GST rate and amount will be added?",
    "extras_outside_total": ("These charges seem to be outside the total: {charges}. What is the full amount "
                             "we would pay, including them?"),
    "net_meter": "Are net-meter charges included? If not, how much are they?",
}
SOURCE_CHECKS = ("C2_central_subsidy", "C4_missing_details")


def vendor_questions(findings):
    """[{"id", "text"}] in template order, one per question id."""
    params = {}
    for f in findings:
        qid = f.get("question")
        if f.get("check_id") in SOURCE_CHECKS and qid in TEMPLATES and qid not in params:
            params[qid] = f.get("question_params") or {}
    return [{"id": qid, "text": TEMPLATES[qid].format(**params[qid])} for qid in TEMPLATES if qid in params]


def vendor_message(findings):
    """The questions as one message a household can send, or None if there are none."""
    questions = vendor_questions(findings)
    if not questions:
        return None
    lines = [INTRO, ""] + [f"{i}. {q['text']}" for i, q in enumerate(questions, 1)] + ["", OUTRO]
    return "\n".join(lines)
