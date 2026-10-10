import copy

from .arithmetic import check_gross_total, check_net_cost
from .capacity import check_capacity
from .common import NEEDS_CONFIRMATION, USER_KINDS, field_words, join_words
from . import check_this, confirm, guards, messages
from .contract import effective_option, normalise
from .evidence import verify_evidence
from .missing import check_missing_details
from .questions import vendor_message, vendor_questions
from .recheck import apply_user_inputs
from .subsidy import check_central_subsidy


def _ask_for_typed_numbers(findings, view):
    """A check that uses a typed number still to confirm asks for that number first."""
    for f in findings:
        held = [e["field"] for e in f["evidence"] if isinstance(e.get("value"), dict)
                and e["value"].get("parse_status") == "check_entry"]
        if held:
            f["status"] = NEEDS_CONFIRMATION
            f["message"] = f"Please check the number you entered for {join_words(field_words(n, view) for n in held)}."


def _add_keys(findings):
    """Each finding's message key and parameters. A message no template matches is keyed
    "unkeyed" with its text, so a household still gets its result; tests allow none."""
    for f in findings:
        try:
            f["message_key"], f["message_params"] = messages.identify(f["check_id"], f["message"])
        except LookupError:
            f["message_key"], f["message_params"] = "unkeyed", {"text": f["message"]}


def _annotate(findings, page_texts):
    for f in findings:
        for e in f["evidence"]:
            if e.get("kind") in USER_KINDS:
                e["evidence_status"] = e["kind"]
            elif e.get("kind") == "quoted":
                e["evidence_status"] = verify_evidence(e, page_texts)


def run_checks(quote, user_inputs=None, page_texts=None, rules=None, binding=None):
    """Run every check on a contract v1 quote after applying the user's inputs.

    Deterministic: the same quote and inputs always give the same result, and
    running again on result["quote"] with the same inputs changes nothing.
    page_texts (optional) adds an evidence_status to each quoted evidence entry. binding
    (optional, from the API) signs every token for one job and review revision, or one challenge.
    """
    original = copy.deepcopy(quote)
    verified = confirm.tokens((user_inputs or {}).get("verified"))
    confirmed = confirm.tokens((user_inputs or {}).get("confirmed_operands"))
    effective, corrected = apply_user_inputs(quote, user_inputs)
    selected = ((effective.get("flags") or {}).get("user_confirmed") or {}).get("selected_option")
    option = effective_option(effective)
    sign_values, sign_operands = confirm.signers(binding)
    to_check = check_this.mark(effective, selected, option, verified=verified, sign=sign_values)
    entries = guards.check(effective, selected, option, verified=verified, sign=sign_values)
    view = normalise(effective)
    findings = [
        check_capacity(view),
        check_central_subsidy(view, rules),
        check_gross_total(view),
        check_net_cost(view),
        *check_missing_details(view),
    ]
    _ask_for_typed_numbers(findings, view)
    confirm.hold_until_confirmed(findings, view, option, confirmed, sign=sign_operands)
    _add_keys(findings)
    if page_texts is not None:
        _annotate(findings, page_texts)
    questions = messages.keyed_questions(vendor_questions(findings), findings)
    return {
        "contract_version": view["contract_version"],
        "findings": findings,
        "corrected_fields": corrected,
        "check_this": to_check,
        "entry_checks": entries,
        "questions": questions,
        "vendor_message": vendor_message(findings),
        "vendor_message_lines": messages.vendor_message_lines(questions),
        "quote": effective,
        "original_quote": original,
    }
