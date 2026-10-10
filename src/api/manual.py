"""POST /checks: run the checks on numbers the user typed in from their quote.

No document, no model call and nothing stored. The typed values go through the
same correction path as a fix on the review page, so each one is labelled as
entered by the user and never as quoted from a document.
"""

import hashlib
import hmac
import json
import secrets
import time

from checks import run_checks
from checks.contract import AMOUNT_FIELDS

from .common import (ApiError, body_json, check_kill_switch, conditional_update, confirm_key, error_response,
                     guarded, log, response, table, take_slots)

CHALLENGE_SECONDS = 900  # how long a challenge for one set of typed values lasts

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


def _digest(fields, answers):
    return hashlib.sha256(json.dumps({"fields": fields, "answers": answers}, sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()


SESSION_PREFIX = "typed-"  # session records share the jobs table; job ids are UUIDs, so never clash


def _keyed(text):
    return hmac.new(confirm_key(), text.encode("utf-8"), hashlib.sha256).hexdigest()


def _session_key(session_id):
    return {"job_id": SESSION_PREFIX + session_id}


def challenge_for(fields, answers, offered=None, clock=None):
    """The challenge every token in this response is bound to: "<session id>.<counter>.<signature>".

    Typed-in numbers keep a short-lived session record holding only a random id, a counter and a
    keyed hash of the values (no numbers), expiring after CHALLENGE_SECONDS. A challenge counts
    only for its own session, at its current counter, for the same values. Whoever holds the
    session and sends changed values moves the counter on, so tokens issued before (even for
    values later changed back) no longer confirm anything. A missing, altered or expired
    challenge starts a new session with a new random id: nothing is derived from the clock."""
    clock = clock or time.time
    values = _keyed("values|" + _digest(fields, answers))
    now_s = int(clock())
    parts = offered.split(".") if isinstance(offered, str) else []
    if len(parts) == 3 and len(parts[0]) == 32 and parts[1].isdigit():
        session_id, counter = parts[0], int(parts[1])
        record = table().get_item(Key=_session_key(session_id), ConsistentRead=True).get("Item") or {}
        held = record.get("values_hash")
        owner = (held is not None and int(record.get("expires_at", 0)) > now_s and int(record["counter"]) == counter
                 and hmac.compare_digest(parts[2], _keyed(f"{session_id}|{counter}|{held}")))
        if owner and hmac.compare_digest(held, values):
            return offered
        if owner and conditional_update(
                SESSION_PREFIX + session_id, "SET #c = :next, #v = :values, #e = :expires", "#c = :counter",
                {"#c": "counter", "#v": "values_hash", "#e": "expires_at"},
                {":next": counter + 1, ":values": values, ":expires": now_s + CHALLENGE_SECONDS, ":counter": counter}):
            return f"{session_id}.{counter + 1}.{_keyed(f'{session_id}|{counter + 1}|{values}')}"
    session_id = secrets.token_hex(16)
    table().put_item(Item={**_session_key(session_id), "counter": 0, "values_hash": values,
                           "expires_at": now_s + CHALLENGE_SECONDS},
                     ConditionExpression="attribute_not_exists(job_id)")
    return f"{session_id}.0.{_keyed(f'{session_id}|0|{values}')}"


def still_current(challenge, fields, answers, clock=None):
    """True if the session is still at the counter and values this challenge was issued for: a
    conditional write, so a request that overlapped and moved the session on wins."""
    session_id, counter = challenge.split(".")[:2]
    values = _keyed("values|" + _digest(fields, answers))
    return conditional_update(
        SESSION_PREFIX + session_id, "SET #e = #e", "#c = :counter AND #v = :values AND #e > :now",
        {"#c": "counter", "#v": "values_hash", "#e": "expires_at"},
        {":counter": int(counter), ":values": values, ":now": int((clock or time.time)())})


def numbers_changed():
    return ApiError(409, "numbers_changed", "These numbers were changed in another request. Please confirm them again.")


def _checks(charges, corrections, confirmations, tokens=None, binding=None):
    try:
        return run_checks(blank_quote(charges), {"corrections": corrections, "confirmations": confirmations,
                                                 **(tokens or {})}, binding=binding)
    except KeyError as e:
        raise ApiError(400, "unknown_field", str(e).strip("'\"")) from None
    except (TypeError, ValueError, AttributeError):
        raise ApiError(400, "bad_inputs", "An answer has the wrong type.") from None


@guarded("manual")
def handler(event, context):
    """POST /checks {"fields": {...}, "answers": {...}, "challenge": "...", "verified": [token],
    "confirmed_operands": [token]}

    verified holds the tokens of typed numbers the household confirmed after a guard asked
    about them; confirmed_operands the tokens of operand sets the household confirmed. Both
    come from an earlier response and are signed for its challenge, which belongs to one
    short-lived session of typed values: a changed number moves the session on and needs
    confirming again, even if it is later changed back."""
    try:
        check_kill_switch()
        body = body_json(event)
        fields, answers = body.get("fields"), body.get("answers")
        fields, answers = {} if fields is None else fields, {} if answers is None else answers
        if not isinstance(fields, dict) or not isinstance(answers, dict):
            raise ApiError(400, "bad_inputs", "fields and answers must be JSON objects.")
        if any(isinstance(v, (dict, list)) for v in answers.values()):
            raise ApiError(400, "bad_inputs", "Each answer must be a single value.")
        tokens = {}
        for key in ("verified", "confirmed_operands"):
            tokens[key] = body.get(key) or []
            if not isinstance(tokens[key], list) or not all(isinstance(t, str) for t in tokens[key]):
                raise ApiError(400, "bad_inputs", f"{key} must be a list of tokens.")
        charges, corrections = typed_corrections(fields)
        # The user typed one set of figures, so there is one option.
        confirmations = {"multiple_options": False, **answers}
        # The whole request is checked first: a 400 spends no quota and leaves the session alone.
        _checks(charges, corrections, confirmations)
        take_slots(event, "typed")  # typed-in checks have their own daily quotas
        challenge = challenge_for(fields, answers, body.get("challenge"))
        result = _checks(charges, corrections, confirmations, tokens, {"key": confirm_key(), "challenge": challenge})
        if not still_current(challenge, fields, answers):
            raise numbers_changed()
    except ApiError as e:
        log("manual_refused", reason=e.code, http_status=e.status)
        return error_response(e)
    log("manual_checked", http_status=200)
    view = {k: result[k] for k in ("findings", "questions", "vendor_message", "vendor_message_lines", "check_this")}
    names = {path: name for name, path in TEXT_FIELDS.items()}
    view["entry_checks"] = [{**e, "field": names.get(e["path"], e["path"])} for e in result["entry_checks"]]
    return response(200, {"mode": "manual", "challenge": challenge, **view})
