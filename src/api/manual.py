"""POST /checks: run the checks on numbers the user typed in from their quote.

No document, no model call and nothing stored. The typed values go through the
same correction path as a fix on the review page, so each one is labelled as
entered by the user and never as quoted from a document.
"""

from checks import run_checks
from checks.contract import AMOUNT_FIELDS

from .common import ApiError, body_json, error_response, guarded, log, response

TEXT_FIELDS = {
    "stated_capacity": "stated_capacity",
    "panel_count": "module_groups[G1].count",
    "panel_wattage": "module_groups[G1].wattage",
    "panel_make_model": "module_groups[G1].make_model",
    "inverter_make_model": "inverters[I1].make_model",
    "inverter_rating": "inverters[I1].rating",
    "vendor_registration": "vendor_registration",
    **{name: name for name in AMOUNT_FIELDS},
}
CHARGE_FIELDS = {"label", "amount", "included_in_total"}
INCLUDED = {"yes", "no", "unclear"}
MAX_TEXT = 200
MAX_CHARGES = 10


def _bad(message):
    return ApiError(400, "bad_fields", message)


def _text(value, name):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise _bad(f"{name} must be text of up to {MAX_TEXT} characters.")
    return value.strip() or None


def blank_quote(charges):
    """A contract v1 quote with nothing found, one panel group, one inverter and
    the given number of extra charges, for the typed values to fill."""
    quote = {
        "contract_version": "v1", "processing_complete": True, "pages_processed": [], "options": [],
        "module_groups": [{"group_id": "G1", "option_id": None, "count": None, "wattage": None,
                           "make_model": None, "make_model_alternatives": []}],
        "inverters": [{"inverter_id": "I1", "option_id": None, "make_model": None, "rating": None,
                       "make_model_alternatives": []}],
        "extra_charges": [{"charge_id": f"E{n}", "option_id": None, "label": None, "amount": None,
                           "included_in_total": None, "total_label": None} for n in range(1, charges + 1)],
        "stated_capacity": None, "dcr_declaration": None, "vendor_registration": None,
        "flags": {"model_proposed": {}, "user_confirmed": {}},
    }
    quote.update({name: None for name in AMOUNT_FIELDS})
    return quote


def typed_corrections(fields):
    """(number of extra charges, corrections) from the form's fields."""
    unknown = sorted(set(fields) - set(TEXT_FIELDS) - {"dcr_declaration", "extra_charges"})
    if unknown:
        raise ApiError(400, "unknown_field", f"Unknown field: {', '.join(unknown)}")
    corrections = {}
    for name, path in TEXT_FIELDS.items():
        value = _text(fields.get(name), name)
        if value is not None:
            corrections[path] = int(value) if name == "panel_count" and value.isdigit() else value
    dcr = fields.get("dcr_declaration")
    if dcr is not None and not isinstance(dcr, bool):
        raise _bad("dcr_declaration must be true, false or null.")
    if dcr is not None:
        corrections["dcr_declaration"] = dcr
    charges = fields.get("extra_charges") or []
    if not isinstance(charges, list) or len(charges) > MAX_CHARGES:
        raise _bad(f"extra_charges must be a list of up to {MAX_CHARGES} charges.")
    for n, charge in enumerate(charges, 1):
        if not isinstance(charge, dict) or set(charge) - CHARGE_FIELDS:
            raise _bad("Each extra charge has only a label, an amount and included_in_total.")
        included = charge.get("included_in_total")
        if included is not None and included not in INCLUDED:
            raise _bad("included_in_total must be yes, no or unclear.")
        for key in ("label", "amount"):
            value = _text(charge.get(key), key)
            if value is not None:
                corrections[f"extra_charges[E{n}].{key}"] = value
        if included is not None:
            corrections[f"extra_charges[E{n}].included_in_total"] = included
    return len(charges), corrections


@guarded("manual")
def handler(event, context):
    """POST /checks {"fields": {...}, "answers": {...}, "confirmed": [field, ...]}

    confirmed names the typed numbers the household confirmed after a guard asked
    about them (a form field name such as "panel_wattage", or a charge's path)."""
    try:
        body = body_json(event)
        fields, answers = body.get("fields"), body.get("answers")
        fields, answers = {} if fields is None else fields, {} if answers is None else answers
        if not isinstance(fields, dict) or not isinstance(answers, dict):
            raise ApiError(400, "bad_inputs", "fields and answers must be JSON objects.")
        if any(isinstance(v, (dict, list)) for v in answers.values()):
            raise ApiError(400, "bad_inputs", "Each answer must be a single value.")
        confirmed = body.get("confirmed") or []
        if not isinstance(confirmed, list) or not all(isinstance(c, str) for c in confirmed):
            raise ApiError(400, "bad_inputs", "confirmed must be a list of field names.")
        charges, corrections = typed_corrections(fields)
        verified = [TEXT_FIELDS.get(c, c) for c in confirmed if TEXT_FIELDS.get(c, c) in corrections]
        # The user typed one set of figures, so there is one option.
        confirmations = {"multiple_options": False, **answers}
        try:
            result = run_checks(blank_quote(charges), {"corrections": corrections, "confirmations": confirmations,
                                                       "verified": verified})
        except KeyError as e:
            raise ApiError(400, "unknown_field", str(e).strip("'\"")) from None
        except (TypeError, ValueError, AttributeError):
            raise ApiError(400, "bad_inputs", "An answer has the wrong type.") from None
    except ApiError as e:
        log("manual_refused", reason=e.code, http_status=e.status)
        return error_response(e)
    log("manual_checked", http_status=200)
    view = {k: result[k] for k in ("findings", "questions", "vendor_message", "check_this")}
    names = {path: name for name, path in TEXT_FIELDS.items()}
    view["entry_checks"] = [{**e, "field": names.get(e["path"], e["path"])} for e in result["entry_checks"]]
    return response(200, {"mode": "manual", **view})
