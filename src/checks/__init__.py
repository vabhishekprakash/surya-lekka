import copy

from .arithmetic import check_gross_total, check_net_cost
from .capacity import check_capacity
from .common import USER_KINDS
from . import check_this
from .contract import effective_option, normalise
from .evidence import verify_evidence
from .missing import check_missing_details
from .questions import vendor_message, vendor_questions
from .recheck import apply_user_inputs
from .subsidy import check_central_subsidy


def _annotate(findings, page_texts):
    for f in findings:
        for e in f["evidence"]:
            if e.get("kind") in USER_KINDS:
                e["evidence_status"] = e["kind"]
            elif e.get("kind") == "quoted":
                e["evidence_status"] = verify_evidence(e, page_texts)


def run_checks(quote, user_inputs=None, page_texts=None, rules=None):
    """Run every check on a contract v1 quote after applying the user's inputs.

    Deterministic: the same quote and inputs always give the same result, and
    running again on result["quote"] with the same inputs changes nothing.
    page_texts (optional) adds an evidence_status to each quoted evidence entry.
    """
    original = copy.deepcopy(quote)
    effective, corrected = apply_user_inputs(quote, user_inputs)
    selected = ((effective.get("flags") or {}).get("user_confirmed") or {}).get("selected_option")
    to_check = check_this.mark(effective, selected, effective_option(effective))
    view = normalise(effective)
    findings = [
        check_capacity(view),
        check_central_subsidy(view, rules),
        check_gross_total(view),
        check_net_cost(view),
        *check_missing_details(view),
    ]
    if page_texts is not None:
        _annotate(findings, page_texts)
    return {
        "contract_version": view["contract_version"],
        "findings": findings,
        "corrected_fields": corrected,
        "check_this": to_check,
        "questions": vendor_questions(findings),
        "vendor_message": vendor_message(findings),
        "quote": effective,
        "original_quote": original,
    }
