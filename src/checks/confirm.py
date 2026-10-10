"""Confirmation before any finding.

No check says "matches" or "doesn't match" until the household has confirmed the exact set
of numbers it used: each operand's field, the option being checked, its value as read or
typed, and whether it was read or typed. Each operand set gets a token; the household
confirms it by sending the token back. Any change to any part (a correction, an option
switch, a reformat that parses to the same number) gives a different token, so an earlier
confirmation no longer applies. A correction is never a confirmation by itself.

The same tokens bind a value the household ticked ("check this") or a typed number it
confirmed to the option, field and value it saw.

Behind the API, tokens are HMAC-SHA256 with a per-stack secret over a scope as well: the job
and its review revision (or, for typed-in numbers, a signed challenge), so the server accepts
only tokens it issued for that job and its current revision. Without a key (local runs, tests)
they are a plain SHA-256 of the same payload.
"""

import hashlib
import hmac
import json

from .common import NEEDS_CONFIRMATION, jsonable

DEFINITIVE = ("consistent", "inconsistent")
QUESTION = "Are these the numbers on your quote?"
_DETAIL_FIELDS = ("dcr_declaration", "vendor_registration", "gst_treatment")


def _plain(payload):
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def signer(key=None, scope=None):
    """A token function. With a key, HMAC-SHA256 over the scope (job and revision, or a
    challenge) and the payload; without one, a plain SHA-256 of the payload."""
    if not key:
        return _plain

    def sign(payload):
        text = json.dumps({"scope": scope or {}, "payload": payload}, sort_keys=True, ensure_ascii=False, default=str)
        return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()
    return sign


def signers(binding=None):
    """(sign values, sign operand sets) for run_checks' binding: {"key", "job", "revision"} or
    {"key", "challenge"}. A ticked value is bound to the job or challenge, its option, field and
    value; an operand set also to the review revision, so any change of answers, values or
    option asks again."""
    if not binding:
        return _plain, _plain
    base = {k: binding[k] for k in ("job", "challenge") if binding.get(k) is not None}
    return (signer(binding["key"], base),
            signer(binding["key"], {**base, "revision": binding.get("revision")}))


def value_token(path, option, field, sign=None):
    """Token for one value as the household sees it, in the option being checked."""
    return (sign or _plain)({"path": path, "option": option, "value": jsonable((field or {}).get("value")),
                             "provenance": (field or {}).get("provenance") or "read"})


def _operand(e):
    typed = e.get("kind") == "user_corrected"
    out = {"field": e["field"], "value": jsonable(e.get("value")), "raw": e.get("raw"),
           "provenance": e.get("provenance") or "read"}
    out["source"] = ("you typed this" if typed else
                     {"text": e.get("evidence_text"), "page": e.get("page")} if e.get("evidence_text") else None)
    return out


def _details(view):
    """The detail fields C4 found, as operands, for its "every detail found" finding."""
    from .common import quoted
    out = []
    for i, g in enumerate(view.get("module_groups") or []):
        if g.get("make_model") is not None:
            out.append(quoted(f"module_groups[{i}].make_model", g["make_model"]))
    for i, inv in enumerate(view.get("inverters") or []):
        for key in ("make_model", "rating_kw", "rating_kva"):
            if inv.get(key) is not None:
                out.append(quoted(f"inverters[{i}].{key}", inv[key]))
    out += [quoted(name, view[name]) for name in _DETAIL_FIELDS if view.get(name) is not None]
    return out


def operands(finding, view):
    """The values a definitive finding rests on: every number or answer read from the quote or
    typed by the household. The household's own answers about the house are not operands."""
    evidence = finding["evidence"] or (_details(view) if finding["check_id"] == "C4_missing_details" else [])
    return [_operand(e) for e in evidence if e.get("kind") in ("quoted", "user_corrected")]


def hold_until_confirmed(findings, view, option, confirmed, sign=None):
    """Hold each definitive finding at "needs confirmation" unless the household confirmed its
    exact operand set; in place. Every definitive finding gets "operands" and "confirm_token"."""
    confirmed = set(confirmed or ())
    for f in findings:
        if f["status"] not in DEFINITIVE:
            continue
        ops = operands(f, view)
        token = (sign or _plain)({"check": f["check_id"], "item": f.get("item"), "option": option, "operands": ops})
        f["operands"], f["confirm_token"] = ops, token
        if token in confirmed:
            f["operands_confirmed"] = True
            continue
        f.update(status=NEEDS_CONFIRMATION, message=QUESTION, question=None, question_params={},
                 operands_confirmed=False)


def tokens(values):
    """A list of tokens from user input, or a TypeError."""
    if values is None:
        return []
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise TypeError("expected a list of tokens")
    return values
