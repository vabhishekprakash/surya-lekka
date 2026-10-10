"""Typing guards for numbers the household typed in.

A typed number is asked about when it is outside the range a home rooftop system has
(src/rules/entry_guards.json), or when it is about 10, 100 or 1000 times what the other
numbers imply: panels x wattage against the system size, the inverter against the system
size, base price + GST against the total, and total minus subsidy against the net cost.
A guard never clips or converts anything. Until the household confirms that one number,
normalise() reads it as Unparsed, so every check that uses it asks for it.
"""

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .check_this import _paths
from .confirm import value_token
from .common import rupees
from .contract import TO_KW, TO_WATTS

DATA = json.loads((Path(__file__).resolve().parent.parent / "rules" / "entry_guards.json").read_text(encoding="utf-8"))
TYPED = "user_corrected"
AMOUNTS = set(DATA["amounts"]) - {"extra_charge"}
WORDS = {"stated_capacity": "The system size", "count": "The number of panels", "wattage": "The panel wattage",
         "rating": "The inverter rating", "base_price": "The base price", "gst_amount": "The GST amount",
         "gross_total": "The total", "net_cost": "The net cost", "discount": "The discount",
         "subsidy_central": "The central subsidy", "subsidy_state": "The state subsidy",
         "subsidy_combined": "The subsidy", "subsidy_unspecified": "The subsidy", "amount": "The extra charge"}


def _number(text):
    try:
        n = Decimal(str(text))
    except (InvalidOperation, ValueError):
        return None
    return n if n.is_finite() else None


def _value(path, field):
    """(kind, Decimal in the guard's unit) for a typed or read number, else None."""
    if not field or field.get("conflict") or field.get("value") is None:
        return None
    v, key = field["value"], path.rsplit(".", 1)[-1]
    if key == "count":
        return ("count", _number(v)) if isinstance(v, int) and not isinstance(v, bool) else None
    if not isinstance(v, dict) or v.get("parse_status") != "ok" or isinstance(v.get("parsed"), dict):
        return None
    n = _number(v.get("parsed"))
    if n is None:
        return None
    if key == "wattage":
        return ("watts", n * TO_WATTS[v["unit"]]) if v.get("unit") in TO_WATTS else None
    if key in ("stated_capacity", "rating"):
        return ("kw", n * TO_KW[v["unit"]]) if v.get("unit") in TO_KW else None
    return ("amount", n)


def _range(path):
    key = path.rsplit(".", 1)[-1]
    if key == "wattage":
        return DATA["panel_wattage_w"]
    if key == "stated_capacity":
        return DATA["system_size_kw"]
    if key == "count":
        return DATA["panel_count"]
    if path.startswith("extra_charges[") and key == "amount":
        return DATA["amounts"]["extra_charge"]
    return DATA["amounts"].get(path)


def _times(a, b):
    """10, 100 or 1000 when a is about that many times b, or b that many times a; else None."""
    if not a or not b or a <= 0 or b <= 0:
        return None
    ratio = max(a, b) / min(a, b)
    tolerance = Decimal(str(DATA["scale_tolerance"]))
    return next((t for t in DATA["scale_ratios"] if abs(ratio / t - 1) <= tolerance), None)


def check(quote, selected, own_option, verified=()):
    """[{"path", "reason", "params", "message", "token"}] for typed numbers to confirm, sorted by
    path; each such field gets "entry_check" in place. A typed number whose token (its option,
    field and value) the household confirmed is used as typed."""
    fields = {path: field for path, _, field in _paths(quote, selected, own_option) if field}
    verified = set(verified)
    typed = {p for p, f in fields.items() if f.get("provenance") == TYPED
             and value_token(p, own_option, f) not in verified}
    values = {p: v for p, f in fields.items() if (v := _value(p, f)) is not None}
    out = {}
    for path in sorted(typed):
        bounds = _range(path)
        if path in values and bounds is not None:
            lo, hi = (Decimal(str(b)) for b in bounds)
            if not lo <= values[path][1] <= hi:
                out[path] = {"path": path, "reason": "range", "params": {"low": bounds[0], "high": bounds[1]}}

    def num(p):
        return values[p][1] if p in values else None

    groups = sorted({p.rsplit(".", 1)[0] for p in values if p.startswith("module_groups[")})
    panels = [(f"{g}.count", f"{g}.wattage") for g in groups]
    relations = []
    if panels and all(num(c) is not None and num(w) is not None for c, w in panels):
        panel_kw = sum(num(c) * num(w) for c, w in panels) / 1000
        relations.append(([p for pair in panels for p in pair] + ["stated_capacity"], panel_kw, num("stated_capacity")))
    for inverter in sorted(p for p in values if p.startswith("inverters[") and p.endswith(".rating")):
        relations.append(([inverter, "stated_capacity"], num(inverter), num("stated_capacity")))
    base, gst, gross = num("base_price"), num("gst_amount"), num("gross_total")
    if base is not None and gross is not None:
        relations.append((["base_price", "gst_amount", "gross_total"], base + (gst or 0), gross))
    subsidy = sum(n for p in ("subsidy_central", "subsidy_state", "subsidy_combined", "subsidy_unspecified")
                  if (n := num(p)) is not None)
    if gross is not None and num("net_cost") is not None:
        relations.append((["gross_total", "subsidy_central", "subsidy_state", "subsidy_combined",
                           "subsidy_unspecified", "net_cost"], gross - subsidy, num("net_cost")))
    for members, a, b in relations:
        if any(m in out and out[m]["reason"] == "range" for m in members):
            continue  # an out-of-range number already explains it
        times = _times(a, b)
        if times:
            for m in members:
                if m in typed and m in values and m not in out:
                    out[m] = {"path": m, "reason": "scale", "params": {"times": times}}
    for path, entry in out.items():
        entry["message"] = message(entry)
        entry["token"] = value_token(path, own_option, fields[path])
        fields[path]["entry_check"] = entry["reason"]
    return [out[p] for p in sorted(out)]


def message(entry):
    """The plain words shown beside a typed number to confirm."""
    path, params = entry["path"], entry["params"]
    key = path.rsplit(".", 1)[-1]
    if entry["reason"] == "scale":
        words = WORDS.get(key if "[" in path else path, "This number")
        return (f"{words} is about {params['times']} times what the other numbers suggest. "
                "Please check the number you entered.")
    lo, hi = params["low"], params["high"]
    if key == "wattage":
        usual = f"A home system's panels are usually {lo} to {hi} W each."
    elif key == "stated_capacity":
        usual = f"A home system is usually {lo} to {hi} kW."
    elif key == "count":
        usual = f"A home system usually has {lo} to {hi} panels."
    else:
        words = WORDS.get(key if "[" in path else path, "This amount")
        usual = f"{words} for a home system is usually {rupees(Decimal(lo))} to {rupees(Decimal(hi))}."
    return f"{usual} Please check the number you entered."
