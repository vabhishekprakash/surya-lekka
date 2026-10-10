import copy
import json
from pathlib import Path

import pytest

from checks.parse import parse_amount, parse_capacity

_FIXTURES = Path(__file__).parent / "fixtures"
_FIXTURE = json.loads((_FIXTURES / "quote_contract_v0.json").read_text())
_FIXTURE_V1 = json.loads((_FIXTURES / "quote_contract_v1.json").read_text(encoding="utf-8"))


def field(value, text="synthetic", page=1):
    return {"value": value, "evidence_text": text, "page": page}


@pytest.fixture
def quote():
    return copy.deepcopy(_FIXTURE)


@pytest.fixture
def quote_v1():
    return copy.deepcopy(_FIXTURE_V1)


def _jsonable(parsed):
    if isinstance(parsed, dict):
        return {k: str(v) for k, v in parsed.items()}
    return None if parsed is None else str(parsed)


def field_v1(value, text="synthetic", page=1):
    return {"value": value, "evidence_text": text, "page": page, "batch": 1}


def amount_field(raw, page=2, text=None):
    v = parse_amount(raw)
    value = {"raw": raw, "parsed": _jsonable(v["parsed"]), "parse_status": v["parse_status"]}
    return field_v1(value, text or raw, page)


def capacity_field(raw, page=1, text=None):
    v = parse_capacity(raw)
    value = {"raw": raw, "parsed": _jsonable(v["parsed"]), "unit": v["unit"],
             "parse_status": v["parse_status"]}
    return field_v1(value, text or raw, page)


def wire_fact(data, name):
    """The price, subsidy or capacity fact called name in a tool input."""
    if name == "stated_capacity":
        return data["capacities"][0]
    if name.startswith("subsidy_"):
        return next(s for s in data["subsidies"] if s["kind"] == name[len("subsidy_"):])
    return next(p for p in data["prices"] if p["kind"] == name)


def batch_fact(part, name, option=None):
    """The field for a fact in one normalised batch, or None."""
    return next((f["field"] for f in part["facts"] if f["name"] == name and f["option_id"] == option), None)



def run_confirmed(quote, user_inputs=None, **kwargs):
    """run_checks after the household confirms every operand set exactly as shown: what a check
    finds once its numbers are confirmed. It ticks no "check this" value and confirms no typed
    number the guards ask about."""
    from checks import run_checks

    first = run_checks(copy.deepcopy(quote), copy.deepcopy(user_inputs), **kwargs)
    tokens = [f["confirm_token"] for f in first["findings"] if f.get("confirm_token")]
    inputs = {**copy.deepcopy(user_inputs or {}), "confirmed_operands": tokens}
    return run_checks(quote, inputs, **kwargs)
