"""The outside red-team's synthetic reproducers (01-23 against 3a5fd4b, 24-28 against 43b59c0),
one test per fixture number. 29-33 (against d2515a3) are request sequences: the gross-total
finding of the named request must not be definitive.

A pass means no wrong value and no false "doesn't match": leaving a value unresolved, with
its finding at "needs confirmation", is a pass. Cases 13 to 15 exercise FORMS and
AnalyzeExpense, which stay disabled; they are kept as documented expected failures."""

import json
from pathlib import Path

import pytest

from checks import run_checks
from extract import textract_client as tc
from extract.merge import merge_batches

ALL = sorted((Path(__file__).parent / "fixtures" / "redteam").glob("[0-9][0-9]_*.json"))
FIXTURES = [p for p in ALL if int(p.name[:2]) <= 28]
ROUND4 = [p for p in ALL if int(p.name[:2]) >= 29]
DISABLED = {13: "FORMS stays disabled", 14: "AnalyzeExpense stays disabled", 15: "AnalyzeExpense stays disabled"}


def run(case):
    wire, _ = tc.map_page(case["textract_reply"], 1, sources=frozenset(case["sources"]), expense=case["expense"])
    c = tc.to_contract(wire, 1)
    q = merge_batches([{"batch": 1, "pages": [1], "model_id": "textract", "contract": c}])
    return c, q, run_checks(q, {"confirmations": case["confirmations"]})


def facts(c, name):
    return [f for f in c["facts"] if f["name"] == name]


def certain(f):
    return f is not None and not f.get("conflict") and f.get("value") is not None


def check(n, c, q, r):
    assert not any(f["status"] == "inconsistent" for f in r["findings"]), "a false doesn't match"
    if n == 1:
        assert not any(f["field"]["value"]["parsed"] == "100000" for f in facts(c, "gst_amount"))
    elif n in (2, 11, 13, 14):
        assert not facts(c, "gst_amount" if n == 2 else "gross_total")
    elif n == 3:
        assert not any(certain(g.get("count")) or certain(g.get("wattage")) for g in c["module_groups"])
    elif n in (4, 5):
        assert not any(f["name"].startswith("subsidy") for f in c["facts"])
        if n == 5:
            assert [f["field"]["value"]["parsed"] for f in facts(c, "gross_total")] in ([], ["200000"])
    elif n == 6:
        assert not any(certain(i.get("rating")) and i["rating"]["value"]["parsed"] == "6" for i in c["inverters"])
    elif n in (7, 8):
        assert not any(certain(g.get("count")) for g in c["module_groups"])
    elif n == 9:
        assert not facts(c, "gross_total")
    elif n == 10:
        assert not any(certain(g.get("make_model")) for g in c["module_groups"])
    elif n == 12:
        assert (c.get("supplier_gst_state") or {}).get("value") in (None, "Karnataka")
    elif n == 15:
        assert not any(certain(g.get("count")) and g["count"]["value"] == 60 for g in c["module_groups"])
    elif n == 16:
        assert not facts(c, "subsidy_combined")
    elif n == 17:
        assert facts(c, "gst_amount") and facts(c, "base_price")
    elif n == 18:
        assert q["stated_capacity"] and not q["stated_capacity"].get("conflict")
    elif n == 19:
        assert not c["facts"]
    elif n == 20:
        assert not c["module_groups"]
    elif n == 21:
        assert not facts(c, "gross_total") and facts(c, "subsidy_unspecified")
    elif n == 22:
        assert facts(c, "gst_amount")[0]["field"]["value"]["parsed"] == "18000"
    elif n == 23:
        assert all(g["option_id"] is not None for g in c["module_groups"])
    elif n == 24:
        assert not any(certain(g.get("count")) or certain(g.get("wattage")) for g in c["module_groups"])
    elif n == 25:
        assert not facts(c, "net_cost")
    elif n == 26:
        assert c["supplier_gst_state"] is None
    elif n == 27:
        assert not any(certain(g.get("count")) for g in c["module_groups"])
    elif n == 28:
        assert all(g["option_id"] in ("A", "B") for g in c["module_groups"])
        assert [g["wattage"]["value"]["parsed"] for g in q["module_groups"] if g["option_id"] == "A"] == ["550"]
        assert (q["flags"]["model_proposed"]["multiple_options"] or {}).get("conflict")


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_redteam_reproducer(path):
    case = json.loads(path.read_text(encoding="utf-8"))
    n = int(case["name"][:2])
    if n in DISABLED:
        pytest.xfail(f"{DISABLED[n]}: documented expected failure, not shipped")
    check(n, *run(case))


@pytest.mark.parametrize("n", sorted(DISABLED))
def test_disabled_sources_are_not_in_the_reader(n):
    assert not {"forms", "expense"} & tc.SOURCES


# --- round 4: request sequences against d2515a3 ------------------------------------------------------

def mapped(case):
    wire, _ = tc.map_page(case["textract_reply"], 1)
    return merge_batches([{"batch": 1, "pages": [1], "model_id": "textract", "contract": tc.to_contract(wire, 1)}])


# fixture number: (request index, what the gross-total finding must be)
# 30 asked for "needs confirmation" when its GST line was read as an amount; since case 29's rule the
# line gives no amount at all, so "missing" is the right non-definitive result.
ROUND4_EXPECTED = {29: (0, "not_inconsistent"), 30: (0, "not_definitive"), 31: (0, "needs_confirmation"),
                   32: (1, "needs_confirmation"), 33: (1, "needs_confirmation")}


@pytest.mark.parametrize("path", ROUND4, ids=lambda p: p.stem)
def test_round4_reproducer(path):
    case = json.loads(path.read_text(encoding="utf-8"))
    index, expected = ROUND4_EXPECTED[int(case["name"][:2])]
    result = run_checks(mapped(case), case["requests"][index])
    (f,) = [f for f in result["findings"] if f["check_id"] == "C3_gross_total"]
    if expected == "not_inconsistent":
        assert f["status"] != "inconsistent", f["message"]
    elif expected == "not_definitive":
        assert f["status"] not in ("consistent", "inconsistent"), f["message"]
    else:
        assert f["status"] == expected, f["message"]


def test_round4_29_reads_no_gst_amount_from_a_tax_base():
    case = json.loads(next(p for p in ROUND4 if p.name.startswith("29")).read_text(encoding="utf-8"))
    assert mapped(case)["gst_amount"] is None
