"""S4: a hidden tester sample, opened only by a direct link. It is synthetic S2 with one
deliberately wrong saved reading: panel wattage 550 W where the page prints 500 W. The wrong
value keeps its true source line, so a tester can catch it at the confirm step."""

import json
import runpy
from pathlib import Path

from conftest import run_confirmed

ROOT = Path(__file__).resolve().parent.parent
GENERATE = runpy.run_path(str(ROOT / "samples" / "generate.py"))
S2 = json.loads((ROOT / "samples" / "cached" / "S2.json").read_text(encoding="utf-8"))
S4 = json.loads((ROOT / "samples" / "cached" / "S4.json").read_text(encoding="utf-8"))
ANSWERS = {"state": "Telangana", "consumer_type": "individual_household", "portal_application_on_or_after_cutoff": True,
           "first_system": True, "prior_central_subsidy": False, "give_it_up": False}


def test_s4_is_s2_with_only_the_panel_wattage_read_wrong():
    assert S4 == GENERATE["hidden_reading"]("S4")  # regenerate with: python samples/generate.py --saved-readings
    s2, s4 = json.loads(json.dumps(S2)), json.loads(json.dumps(S4))
    wrong = s4["quote"]["module_groups"][0]["wattage"]
    assert wrong["value"] == {"raw": "550 W", "parsed": "550", "unit": "W", "parse_status": "ok"}
    assert wrong["evidence_text"] == s2["quote"]["module_groups"][0]["wattage"]["evidence_text"]
    assert "500 W" in wrong["evidence_text"] and wrong["page"] == 1
    s4["quote"]["module_groups"][0]["wattage"] = s2["quote"]["module_groups"][0]["wattage"]
    assert s4["quote"] == s2["quote"] and s4["pages"] == s2["pages"] and s4["sample_id"] == "S4"


def test_the_wrong_value_is_listed_with_its_true_line_at_the_confirm_step():
    from checks import run_checks
    held = run_checks(S4["quote"], {"confirmations": ANSWERS})
    (c1,) = [f for f in held["findings"] if f["check_id"] == "C1_capacity"]
    (watts,) = [o for o in c1["operands"] if o["field"] == "module_groups[0].wattage_w"]
    assert watts["value"] == "550" and "500 W" in watts["source"]["text"]
    confirmed = run_confirmed(S4["quote"], {"confirmations": ANSWERS})
    (c1,) = [f for f in confirmed["findings"] if f["check_id"] == "C1_capacity"]
    assert c1["status"] == "inconsistent"  # 5 x 550 W, accepted without a look, isn't the 3 kWp stated


def test_s4_is_served_but_not_listed():
    from api import jobs
    assert "S4" in jobs.sample_ids()
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'data-sample="S4"' not in index and "S4" not in index
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "#sample=" in app or "sample=" in app


def test_s4_gets_s2s_page_images(tmp_path):
    render = runpy.run_path(str(ROOT / "scripts" / "render_samples.py"))["render_samples"]
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("needs PyMuPDF")
    written = render(tmp_path)
    assert written["S4"] == written["S2"]
    assert (tmp_path / "S4" / "page-01.jpg").read_bytes() == (tmp_path / "S2" / "page-01.jpg").read_bytes()
    assert json.loads((tmp_path / "S4" / "reading.json").read_text(encoding="utf-8")) == S4
