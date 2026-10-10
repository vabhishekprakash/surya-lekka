"""Stable keys and parameters for every finding message, vendor question and vendor-message
line, for translation and for tests. The English text is unchanged: each check still writes
its own message, and identify() names the template it matches and the values filled in.
render(key, params) gives back exactly the same text.
"""

import re

from .questions import INTRO, OUTRO, TEMPLATES as QUESTION_TEMPLATES

RUPEES = r"₹[0-9,]+(?:\.[0-9]+)?"
NUMBER = r"[0-9]+(?:\.[0-9]+)?"
# Values a placeholder may hold, so that no two templates read one message.
PATTERNS = {"stated": RUPEES, "difference": RUPEES, "rule": RUPEES, "amount_low": RUPEES, "amount_high": RUPEES,
            "direction": "more|less", "what": "total|net cost", "kwp": NUMBER, "stated_kw": NUMBER,
            "stated_kwp": NUMBER, "low": NUMBER, "high": NUMBER,
            "hint": r"Is a charge missing from the list\?|Please check the amounts with the vendor\."}
NET_NOTE = " Subsidy amounts are taken as stated and are not checked here."

# key: (check ids it belongs to, or None for any; template)
TEMPLATES = {
    "options.several": (None, "The quote lists more than one option. Select the option you are considering first."),
    "options.unclear": (None, "It isn't clear whether the quote has one option or several. Please confirm this "
                              "before the figures are checked."),
    "entry.check_number": (None, "Please check the number you entered for {fields}."),
    "confirm.operands": (None, "Are these the numbers on your quote?"),
    "fields.not_found": (("C1_capacity", "C3_gross_total", "C3_net_cost"), "Not found on the quote: {fields}."),
    "amounts.unusable": (("C3_gross_total", "C3_net_cost"), "Please check {fields}: it can't be used as an amount."),

    "C1.no_panels": (("C1_capacity",), "The number of panels and their wattage weren't found on the quote."),
    "C1.single_number": (("C1_capacity",), "Please check {fields}: a single number is needed."),
    "C1.kva": (("C1_capacity",), "The quote gives the system size as {stated_raw}. kVA measures the inverter's "
                                 "output, not the panels, so it wasn't compared with the {kwp} kWp the panels make."),
    "C1.check_size": (("C1_capacity",), "Please check the system size on the quote."),
    "C1.size_missing": (("C1_capacity",), "The system size wasn't found on the quote."),
    "C1.basis_unclear": (("C1_capacity",), "{panels}. The quote doesn't say whether its {stated_kw} kW system size "
                                           "is the panels' (DC) capacity, so the two weren't compared."),
    "C1.matches": (("C1_capacity",), "{panels}, the same as the quote's system size."),
    "C1.matches_within": (("C1_capacity",), "{panels}, within 0.01 kWp of the quote's {stated_kwp} kWp."),
    "C1.differs": (("C1_capacity",), "{panels}, but the quote's system size is {stated_kwp} kWp."),

    "C3.gst_basis": (("C3_gross_total",), "The quote does not say whether the base price includes GST."),
    "C3.charge_inside": (("C3_gross_total",), "Is {charges} inside the total? Please answer on the review screen."),
    "C3.charges_complete": (("C3_gross_total",), "Is every charge on your quote listed? Answer yes on the review "
                                                 "screen once it is, and the total will be checked."),
    "C3.net_basis": (("C3_net_cost",), "The quote doesn't say which subsidy the net cost takes off."),
    "C3.matches": (("C3_gross_total", "C3_net_cost"), "{sums}, the same as the quote's {what}."),
    "C3.matches_within": (("C3_gross_total", "C3_net_cost"), "{sums}, within ₹1 of the {stated} on the quote's {what}."),
    "C3.differs": (("C3_gross_total", "C3_net_cost"),
                   "{sums}. The quote's {what} is {stated}, which is {difference} {direction}. {hint}"),
    "C3.net_matches": (("C3_net_cost",), "{sums}, the same as the quote's net cost." + NET_NOTE),
    "C3.net_matches_within": (("C3_net_cost",), "{sums}, within ₹1 of the {stated} on the quote's net cost." + NET_NOTE),
    "C3.net_differs": (("C3_net_cost",), "{sums}. The quote's net cost is {stated}, which is {difference} "
                                         "{direction}. Please check the amounts with the vendor." + NET_NOTE),

    "C2.pages_incomplete": (("C2_central_subsidy",), "Not every page of the quote was processed, so the subsidy was "
                                                     "not checked."),
    "C2.non_household": (("C2_central_subsidy",), "Subsidies for RWAs, group housing and other non-household "
                                                  "connections are outside what this checker covers."),
    "C2.household": (("C2_central_subsidy",), "Is this system for an individual household? This check covers "
                                              "individual households only, so please confirm that first."),
    "C2.state": (("C2_central_subsidy",), "Which state is the house in? The central subsidy rate depends on it."),
    "C2.state_unknown": (("C2_central_subsidy",), "That state name wasn't recognised. Please pick the state or union "
                                                  "territory the house is in. The central subsidy rate depends on it."),
    "C2.before_cutoff": (("C2_central_subsidy",), "Applications received on the National Portal before {cutoff} "
                                                  "follow earlier rules, which are outside what this checker covers."),
    "C2.portal_date": (("C2_central_subsidy",), "Please confirm that your subsidy application was, or will be, made "
                                                "on the National Portal on or after {cutoff}. The rule checked here "
                                                "goes by the application date, not the quote date."),
    "C2.not_first": (("C2_central_subsidy",), "Adding to an existing system, or a house that has had central subsidy "
                                              "before, is outside what this checker covers."),
    "C2.first": (("C2_central_subsidy",), "Please confirm that this is the first rooftop solar system at this house "
                                          "and that the house hasn't received central subsidy for rooftop solar "
                                          "before."),
    "C2.give_it_up": (("C2_central_subsidy",), "You've chosen to give up the central subsidy (Give It Up), so there "
                                               "is no subsidy amount to check."),
    "C2.give_it_up_mentioned": (("C2_central_subsidy",), "The quote mentions Give It Up. Are you giving up the "
                                                         "central subsidy? This check doesn't decide that from the "
                                                         "quote."),
    "C2.one_figure": (("C2_central_subsidy",), "The quote gives one subsidy figure without saying how much is central "
                                               "and how much is state. Ask the vendor for the central and state "
                                               "amounts separately."),
    "C2.not_mentioned": (("C2_central_subsidy",), "This quote doesn't mention the central subsidy. If you plan to "
                                                  "apply, ask the vendor what they expect it to be."),
    "C2.dc_unknown": (("C2_central_subsidy",), "The DC capacity of the panels is not known, so the central rule could "
                                               "not be applied."),
    "C2.capacity_range": (("C2_central_subsidy",), "The panel capacity is a range ({low} to {high} kWp), which gives "
                                                   "a central subsidy between {amount_low} and {amount_high}. The "
                                                   "exact panel wattage is needed."),
    "C2.confirm_amount": (("C2_central_subsidy",), "Please confirm the central subsidy amount on the quote."),
    "C2.matches": (("C2_central_subsidy",), "Matches the central subsidy rate for this panel capacity. Eligibility "
                                            "(DCR panels, registration, inspection) is not verified by this check."),
    "C2.higher": (("C2_central_subsidy",), "The stated central subsidy {stated} is higher than the central rule for "
                                           "this panel capacity ({dc_kwp} kWp gives {rule})."),
    "C2.higher_unconfirmed": (("C2_central_subsidy",), "The stated central subsidy {stated} is higher than the central "
                                                       "rule for this panel capacity ({dc_kwp} kWp gives {rule}). This "
                                                       "can't be confirmed yet because {gaps}. Please check the panel "
                                                       "details."),
    "C2.lower": (("C2_central_subsidy",), "The stated central subsidy {stated} is lower than the rule maximum for "
                                          "{dc_kwp} kWp of panels ({rule}); ask the vendor why."),

    "C4.panel_wattage.missing": (("C4_missing_details",), "Panel wattage: not found on the quote."),
    "C4.panel_wattage.range": (("C4_missing_details",), "Panel wattage is given as a range, so the exact panel "
                                                        "capacity is not fixed."),
    "C4.panel_wattage.confirm": (("C4_missing_details",), "Please confirm the panel wattage."),
    "C4.panel_count.missing": (("C4_missing_details",), "Number of panels: not found on the quote."),
    "C4.panel_count.confirm": (("C4_missing_details",), "Please confirm the number of panels."),
    "C4.module_model.missing": (("C4_missing_details",), "Exact panel make and model: not found on the quote."),
    "C4.module_model.confirm": (("C4_missing_details",), "Please confirm the panel make and model."),
    "C4.module_model.choice": (("C4_missing_details",), "The quote lists alternative panel makes or models; it does "
                                                        "not say which one will be installed."),
    "C4.dcr.confirm": (("C4_missing_details",), "Please confirm what the quote says about DCR (domestic content) "
                                                "panels."),
    "C4.dcr.not_dcr": (("C4_missing_details",), "The quote appears to say the panels are not DCR. The central subsidy "
                                                "requires DCR panels and cells."),
    "C4.dcr.missing": (("C4_missing_details",), "DCR (domestic content) declaration for the panels: not found on the "
                                                "quote."),
    "C4.inverter_model.missing": (("C4_missing_details",), "Inverter make and model: not found on the quote."),
    "C4.inverter_model.confirm": (("C4_missing_details",), "Please confirm the inverter make and model."),
    "C4.inverter_model.choice": (("C4_missing_details",), "The quote lists alternative inverters; it does not say "
                                                          "which one will be installed."),
    "C4.inverter_rating.missing": (("C4_missing_details",), "Inverter rating: not found on the quote."),
    "C4.inverter_rating.confirm": (("C4_missing_details",), "Please confirm the inverter rating."),
    "C4.vendor_registration.confirm": (("C4_missing_details",), "Please confirm the vendor registration number on "
                                                                "the quote."),
    "C4.vendor_registration.missing": (("C4_missing_details",), "Vendor registration number: not found on the "
                                                                "quote. This does not mean the vendor is "
                                                                "unregistered."),
    "C4.gst_basis": (("C4_missing_details",), "The quote does not make clear whether the price includes GST."),
    "C4.extras_outside_total": (("C4_missing_details",), "Some charges are listed outside the total or not clearly "
                                                         "inside it: {charges}."),
    "C4.net_meter": (("C4_missing_details",), "Net-meter charges: not mentioned on the quote."),
    "C4.all_found": (("C4_missing_details",), "Every detail this check looks for was found on the quote (panel "
                                              "wattage and count, panel and inverter make and model, inverter "
                                              "rating, DCR declaration, vendor registration number, GST basis, "
                                              "net-meter charges). They were not verified."),
}
VENDOR_LINES = {"vendor.intro": INTRO, "vendor.blank": "", "vendor.outro": OUTRO,
                "vendor.question": "{number}. {text}"}


def _compile(template):
    parts, pos = [], 0
    for m in re.finditer(r"\{(\w+)\}", template):
        parts.append(re.escape(template[pos:m.start()]))
        parts.append(f"(?P<{m.group(1)}>{PATTERNS.get(m.group(1), '.+?')})")
        pos = m.end()
    parts.append(re.escape(template[pos:]))
    return re.compile("".join(parts), re.S)


_COMPILED = {key: (checks, _compile(template)) for key, (checks, template) in TEMPLATES.items()}


def identify(check_id, message):
    """(key, params) of the one template this check's message matches. Raises LookupError
    when none or several match."""
    found = [(key, m.groupdict()) for key, (checks, rx) in _COMPILED.items()
             if (checks is None or check_id in checks) and (m := rx.fullmatch(message))]
    if len(found) != 1:
        raise LookupError(f"{check_id}: {len(found)} message templates match")
    return found[0]


def render(key, params):
    template = TEMPLATES[key][1] if key in TEMPLATES else VENDOR_LINES[key]
    return template.format(**params)


def keyed_findings(findings):
    """Add message_key and message_params to each finding, in place."""
    for f in findings:
        f["message_key"], f["message_params"] = identify(f["check_id"], f["message"])


def keyed_questions(questions, findings):
    """Each vendor question with its key ("question.<id>") and the parameters it was filled with."""
    params = {}
    for f in findings:
        if f.get("question") in QUESTION_TEMPLATES and f["question"] not in params:
            params[f["question"]] = dict(f.get("question_params") or {})
    from .message_parts import message_parts
    return [{**q, "key": f"question.{q['id']}", "params": params.get(q["id"], {}),
             "parts": message_parts(params.get(q["id"], {}), f"question.{q['id']}")} for q in questions]


def vendor_message_lines(questions):
    """The vendor message line by line, each with its key and parameters."""
    if not questions:
        return []
    lines = [{"key": "vendor.intro", "params": {}}, {"key": "vendor.blank", "params": {}}]
    lines += [{"key": "vendor.question", "params": {"number": i, "text": q["text"], "question_key": q["key"]},
               "parts": {"number": {"text": str(i)}, "text": {"key": q["key"], "params": q.get("parts", {})}}}
              for i, q in enumerate(questions, 1)]
    return lines + [{"key": "vendor.blank", "params": {}}, {"key": "vendor.outro", "params": {}}]
