from decimal import Decimal

import pytest

from checks.parse import parse_amount, parse_capacity


@pytest.mark.parametrize("raw,expected", [
    ("2,16,000", "216000"),
    ("Rs. 2,16,000/-", "216000"),
    ("Rs 1,97,000", "197000"),
    ("Rs.78,000/-", "78000"),
    ("₹ 1,23,456.50", "123456.50"),
    ("₹78000", "78000"),
    ("INR 85,800", "85800"),
    ("85,800 INR", "85800"),
    ("482,200.00", "482200.00"),
    ("05,700.00", "5700.00"),
    ("0078000", "78000"),
    ("12,345", "12345"),
    ("1,00,00,000", "10000000"),
    ("2.16 lakh", "216000.00"),
    ("Rs. 2 Lakhs", "200000"),
    ("1.5 lakhs", "150000.0"),
    ("Rs. 1,97,000/- only", "197000"),
    ("16020", "16020"),
])
def test_amount_ok(raw, expected):
    r = parse_amount(raw)
    assert r["parse_status"] == "ok" and r["parsed"] == Decimal(expected) and r["raw"] == raw


@pytest.mark.parametrize("raw", [
    "2,16,000 + GST",
    "Rs. 1,85,000 plus GST",
    "78,000 / 85,800",
    "Rupees Two Lakh Sixteen Thousand Only",
    "two lakh",
    "2,1600",
    "21,60,00",
    "1.2.3",
    "-1520",
    "approx 50000",
    "Rs. 2,16,000/- (Rupees Two Lakh Sixteen Thousand only)",
])
def test_amount_ambiguous(raw):
    r = parse_amount(raw)
    assert r["parse_status"] == "ambiguous" and r["parsed"] is None


@pytest.mark.parametrize("raw", ["at actuals", "as applicable", "N/A", "Rs.", "-"])
def test_amount_unparseable(raw):
    assert parse_amount(raw)["parse_status"] == "unparseable"


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_amount_empty(raw):
    assert parse_amount(raw) == {"raw": raw, "parsed": None, "parse_status": "empty"}


@pytest.mark.parametrize("raw,value,unit", [
    ("3.3 kWp", "3.3", "kWp"),
    ("3.3kwp", "3.3", "kWp"),
    ("2.825 KWp", "2.825", "kWp"),
    ("3 kW", "3", "kW"),
    ("3.3 KVA", "3.3", "kVA"),
    ("550Wp", "550", "Wp"),
    ("540 W", "540", "W"),
    ("550 - 550 Wp", "550", "Wp"),
])
def test_capacity_single(raw, value, unit):
    r = parse_capacity(raw)
    assert r["parse_status"] == "ok" and r["parsed"] == Decimal(value) and r["unit"] == unit


@pytest.mark.parametrize("raw", ["595-600Wp", "595 - 600 Wp", "595–600 Wp", "595Wp-600Wp", "595 to 600 Wp"])
def test_capacity_range_kept_as_range(raw):
    r = parse_capacity(raw)
    assert r["parse_status"] == "ok" and r["unit"] == "Wp"
    assert r["parsed"] == {"min": Decimal(595), "max": Decimal(600)}


@pytest.mark.parametrize("raw", ["3.3", "595W-600kW", "600-595 Wp", "3.3 kWp (6 x 550 Wp)", "5 kWh"])
def test_capacity_ambiguous(raw):
    assert parse_capacity(raw)["parse_status"] == "ambiguous"


def test_capacity_empty_and_unparseable():
    assert parse_capacity(None)["parse_status"] == "empty"
    assert parse_capacity("as per site")["parse_status"] == "unparseable"
