import json
import runpy
from pathlib import Path

import pytest

from extract import scoring
from extract.dryrun import load_dry_run_wire
from extract.merge import merge_batches
from extract.wire_schema import normalise_batch, validate

ROOT = Path(__file__).resolve().parent.parent

KEY = """# synthetic answer key, written with the hand-written key's own names
[S1]
quote_date: 01/10/2026 | 1
vendor_name: example solar  PVT LTD | 1
vendor_state: not stated
options: 1
capacity_stated: 3.3 kWp; 3 kW inverter | 1
capacity_basis: DC | 1
panel_count: 6 | 1
panel_wattage_w: 550 | 1
panel_make: Example PV EXM-550 | 1
inverter_rating: 3 kW | 1
inverter_make: Example Inverters EXI-3K | 1
total_price: Rs. 1,97,000 | 2
gst: extra | 2
extra_charges_outside_total: not stated
subsidy: 78,000 | 2
subsidy_type: central
net_cost_stated: 1,19,000 | 2
dcr_text: modules and cells are DCR (domestic content) | 1
pages_total: 2
contradictions: none
Total system cost incl everything: 123

## S2
options: more than 1
gst: unclear
S3:
panel_wattage_w: 550 + 545
"""
ALIAS_TABLE = {
    "quote_date": "quote_date", "vendor_name": "vendor_name", "vendor_state": "vendor_state",
    "options": "multiple_options", "capacity_stated": "stated_capacity", "capacity_basis": "capacity_basis",
    "panel_count": "panel_count", "panel_wattage_w": "panel_wattage", "panel_make": "module_make_model",
    "inverter_rating": "inverter_rating", "inverter_make": "inverter_make_model", "total_price": "gross_total",
    "gst": "gst_treatment", "extra_charges_outside_total": "extra_charges_outside_total", "subsidy": "subsidy",
    "subsidy_type": "subsidy_type", "net_cost_stated": "net_cost", "dcr_text": "dcr_text",
}


def sample_quote(change=None):
    data = load_dry_run_wire()
    if change:
        change(data)
    cleaned, errors, _ = validate(data, [1, 2])
    assert errors == []
    return merge_batches([{"batch": 1, "pages": [1, 2], "contract": normalise_batch(cleaned, 1)}])


def truth(value, *pages):
    return {"value": value, "pages": list(pages)}


# --- answer key ------------------------------------------------------------------------

def test_alias_table_covers_every_key_name():
    assert scoring.ALIASES == ALIAS_TABLE
    assert set(ALIAS_TABLE.values()) <= set(scoring.FIELDS)
    assert scoring.KEPT == ("pages_total", "contradictions")


def test_parse_answer_key():
    key = scoring.parse_answer_key("stray: 1\n" + KEY)
    assert set(key["docs"]) == {"S1", "S2", "S3"}
    s1 = key["docs"]["S1"]
    assert set(s1) == set(ALIAS_TABLE.values())
    assert s1["stated_capacity"] == {"value": "3.3 kWp; 3 kW inverter", "pages": [1], "key": "capacity_stated"}
    assert s1["vendor_state"]["value"] is None and s1["extra_charges_outside_total"]["value"] is None
    assert key["kept"]["S1"]["pages_total"]["value"] == "2" and "contradictions" in key["kept"]["S1"]
    assert key["unknown"] == {"S1": ["Total system cost incl everything"]}
    assert set(key["docs"]["S2"]) == {"multiple_options", "gst_treatment"}
    assert key["orphan_lines"] == 1


def test_header_styles_and_comments():
    text = "== Q12 ==\ntotal_price: 1\n# a comment line\n// another\n-- Q16 --\ntotal_price: 2\nDocument: Q20\ngst: extra\n"
    key = scoring.parse_answer_key(text)
    assert {d: list(v) for d, v in key["docs"].items()} == {
        "Q12": ["gross_total"], "Q16": ["gross_total"], "Q20": ["gst_treatment"]}
    key = scoring.parse_answer_key("[Q12]\ntotal_price: 1\ntotal_price: 2\n550\n")
    assert key["duplicates"] == {"Q12": ["gross_total"]} and key["docs"]["Q12"]["gross_total"]["value"] == "2"
    assert set(key["docs"]) == {"Q12"}  # a bare number is not a document header


# --- scoring ---------------------------------------------------------------------------

def test_every_key_scores_against_the_sample():
    key = scoring.parse_answer_key(KEY)["docs"]["S1"]
    scores = scoring.score_document(sample_quote(), key)
    expected = {k: "correct" for k in key}
    expected.update(vendor_state="abstained", extra_charges_outside_total="abstained")
    assert {k: v["status"] for k, v in scores.items()} == expected
    assert scores["stated_capacity"]["notes"] == ["3 kW inverter"]
    assert scores["stated_capacity"]["page_ok"] is True and scores["gross_total"]["page_ok"] is True


def test_options_values():
    quote = sample_quote()
    assert scoring.score_field(quote, "multiple_options", truth("1"))["status"] == "correct"
    assert scoring.score_field(quote, "multiple_options", truth("more than 1"))["status"] == "wrong"


def test_gst_is_the_basis_not_the_amount():
    quote = sample_quote()
    assert scoring.score_field(quote, "gst_treatment", truth("extra"))["status"] == "correct"
    assert scoring.score_field(quote, "gst_treatment", truth("included"))["status"] == "wrong"
    assert scoring.score_field(quote, "gst_treatment", truth("Rs. 16,020"))["status"] == "wrong"


def test_capacity_and_wattage_matching():
    quote = sample_quote()
    score = scoring.score_field
    assert score(quote, "stated_capacity", truth("3300 W"))["status"] == "correct"
    assert score(quote, "stated_capacity", truth("3.3 kWp (DC)"))["status"] == "correct"
    assert score(quote, "stated_capacity", truth("3.3 kVA"))["status"] == "wrong"
    assert score(quote, "panel_wattage", truth("550 Wp"))["status"] == "correct"
    assert score(quote, "panel_wattage", truth("540-560"))["status"] == "wrong"
    ranged = sample_quote(lambda d: d["module_groups"][0].update(wattage="540-560 Wp"))
    assert score(ranged, "panel_wattage", truth("540-560"))["status"] == "correct"
    assert score(quote, "capacity_basis", truth("AC"))["status"] == "wrong"
    unclear = sample_quote(lambda d: d["capacity_basis"].update(value="unspecified"))
    assert score(unclear, "capacity_basis", truth("unclear"))["status"] == "correct"
    assert score(unclear, "capacity_basis", truth(None))["status"] == "abstained"


def test_text_ignores_case_and_whitespace_only():
    quote = sample_quote()
    score = scoring.score_field
    assert score(quote, "module_make_model", truth("example pv  exm-550"))["status"] == "correct"
    assert score(quote, "module_make_model", truth("Example PV EXM 550"))["status"] == "wrong"
    assert score(quote, "vendor_name", truth("Example Solar"))["status"] == "wrong"


def test_subsidy_and_subsidy_type():
    quote = sample_quote()
    score = scoring.score_field
    assert score(quote, "subsidy", truth("Rs 78000"), {"subsidy_type": truth("central")})["status"] == "correct"
    assert score(quote, "subsidy", truth("Rs 78000"), {"subsidy_type": truth("state")})["status"] == "missing"
    assert score(quote, "subsidy_type", truth("central"))["status"] == "correct"
    assert score(quote, "subsidy_type", truth("combined"))["status"] == "wrong"
    assert score(quote, "subsidy_type", truth(None))["status"] == "falsely_populated"
    unspecified = sample_quote(lambda d: d["subsidies"][0].update(kind="unspecified"))
    assert score(unspecified, "subsidy_type", truth(None))["status"] == "abstained"
    assert score(unspecified, "subsidy", truth("78,000"), {"subsidy_type": truth(None)})["status"] == "correct"


def test_extra_charges_outside_total():
    outside = sample_quote(lambda d: d["extra_charges"][0].update(included_in_total="no"))
    score = scoring.score_field
    assert score(outside, "extra_charges_outside_total", truth("Net meter Rs 2,500"))["status"] == "correct"
    assert score(outside, "extra_charges_outside_total", truth("2500; 5000"))["status"] == "wrong"
    assert score(outside, "extra_charges_outside_total", truth(None))["status"] == "falsely_populated"
    assert score(sample_quote(), "extra_charges_outside_total", truth("2500"))["status"] == "missing"


def test_dcr_text():
    quote = sample_quote()
    score = scoring.score_field
    assert score(quote, "dcr_text", truth("DCR declaration: modules and cells are DCR"))["status"] == "correct"
    assert score(quote, "dcr_text", truth("Non-DCR panels"))["status"] == "wrong"
    assert score(quote, "dcr_text", truth(None))["status"] == "falsely_populated"
    no_dcr = sample_quote(lambda d: d.pop("dcr_declaration"))
    assert score(no_dcr, "dcr_text", truth(None))["status"] == "abstained"
    assert score(no_dcr, "dcr_text", truth("DCR"))["status"] == "missing"


def test_score_statuses_and_pages():
    quote = sample_quote()
    assert scoring.score_field(quote, "discount", truth("1,520", 2)) == {
        "status": "correct", "conflict": False, "page_ok": True}
    assert scoring.score_field(quote, "base_price", truth("180000", 1))["page_ok"] is False
    assert scoring.score_field(quote, "gst_amount", truth("Rs. 99"))["status"] == "wrong"
    assert scoring.score_field(quote, "subsidy_state", truth(None))["status"] == "abstained"
    assert scoring.score_field(quote, "net_cost", truth(None))["status"] == "falsely_populated"
    assert scoring.score_field(sample_quote(lambda d: d["prices"].pop(2)), "discount",
                               truth("1,520"))["status"] == "missing"
    assert scoring.score_field(quote, "panel_count", truth("6 + 4"))["status"] == "wrong"


def test_conflict_scores_as_missing_or_falsely_populated():
    quote = sample_quote()
    quote["gross_total"] = {"value": {"raw": None, "parsed": None, "parse_status": "conflict"},
                            "conflict": True, "candidates": []}
    assert scoring.score_field(quote, "gross_total", truth("197000")) == {
        "status": "missing", "conflict": True, "page_ok": None}
    assert scoring.score_field(quote, "gross_total", truth(None))["status"] == "falsely_populated"


def test_summary():
    scores = {"S1": {"a": {"status": "correct", "conflict": False, "page_ok": True},
                     "b": {"status": "missing", "conflict": True, "page_ok": None},
                     "c": {"status": "abstained", "conflict": False, "page_ok": None},
                     "d": {"status": "falsely_populated", "conflict": False, "page_ok": None}}}
    s = scoring.summarise(scores)
    assert s["accuracy"] == 0.5 and s["false_population_rate"] == 0.5
    assert s["conflicts"] == 1 and (s["pages_ok"], s["pages_checked"]) == (1, 1) and s["fields"] == 4


# --- CLI -------------------------------------------------------------------------------

spike = pytest.importorskip("extract.spike")


def test_find_document(tmp_path):
    for name in ("Q1.pdf", "Q12_redacted.pdf", "Q120.pdf", "Q16.jpg", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    assert spike.find_document(tmp_path, "Q12").name == "Q12_redacted.pdf"
    assert spike.find_document(tmp_path, "Q1").name == "Q1.pdf"
    assert spike.find_document(tmp_path, "Q16").name == "Q16.jpg"
    (tmp_path / "Q12.pdf").write_bytes(b"x")
    with pytest.raises(spike.RunStopped, match="found 2"):
        spike.find_document(tmp_path, "Q12")
    with pytest.raises(spike.RunStopped, match="found 0"):
        spike.find_document(tmp_path, "Q99")


def preflight_clients(profiles=("global.amazon.nova-2-lite-v1:0",), list_error=None, sts_error=None):
    from extract.dryrun import stubbed_client

    sts, sts_stub = stubbed_client("sts")
    bedrock, bedrock_stub = stubbed_client("bedrock")
    if sts_error:
        sts_stub.add_client_error("get_caller_identity", service_error_code=sts_error, http_status_code=403)
    else:
        sts_stub.add_response("get_caller_identity", {"UserId": "AIDEXAMPLE", "Account": "000000000000",
                                                      "Arn": "arn:aws:iam::000000000000:user/example"})
    if list_error:
        bedrock_stub.add_client_error("list_inference_profiles", service_error_code=list_error,
                                      http_status_code=403)
    elif not sts_error:
        bedrock_stub.add_response("list_inference_profiles", {"inferenceProfileSummaries": [
            {"inferenceProfileName": p, "inferenceProfileArn": f"arn:aws:bedrock:ap-south-1::inference-profile/{p}",
             "models": [{"modelArn": "arn:aws:bedrock:ap-south-1::foundation-model/example"}],
             "inferenceProfileId": p, "status": "ACTIVE", "type": "SYSTEM_DEFINED"}
            for p in profiles]})
    return sts, bedrock


def test_preflight(capsys):
    pytest.importorskip("boto3")
    sts, bedrock = preflight_clients()
    spike.preflight("ap-south-1", ["global.amazon.nova-2-lite-v1:0"], sts, bedrock)
    assert "account 000000000000" in capsys.readouterr().out
    sts, bedrock = preflight_clients()
    with pytest.raises(spike.RunStopped, match="apac.amazon.nova-pro-v1:0 is not listed"):
        spike.preflight("ap-south-1", ["apac.amazon.nova-pro-v1:0"], sts, bedrock)
    sts, bedrock = preflight_clients(list_error="AccessDeniedException")
    with pytest.raises(spike.RunStopped, match="AccessDeniedException"):
        spike.preflight("ap-south-1", ["global.amazon.nova-2-lite-v1:0"], sts, bedrock)
    sts, bedrock = preflight_clients(sts_error="ExpiredTokenException")
    with pytest.raises(spike.RunStopped, match="identity check failed"):
        spike.preflight("ap-south-1", ["global.amazon.nova-2-lite-v1:0"], sts, bedrock)


@pytest.fixture
def sample_dir(tmp_path):
    pytest.importorskip("pymupdf")
    pytest.importorskip("boto3")
    render = runpy.run_path(str(ROOT / "samples" / "generate.py"))["render"]
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    for sid in ("S1", "S2"):
        sample = json.loads((ROOT / "samples" / "expected" / f"{sid}.json").read_text(encoding="utf-8"))
        render(sample, in_dir / f"{sid}.pdf")
    (tmp_path / "key.txt").write_text(KEY, encoding="utf-8")
    return tmp_path


def args(tmp, *extra):
    return ["--files", "S1,S2", "--models", "global.amazon.nova-2-lite-v1:0,apac.amazon.nova-pro-v1:0",
            "--in", str(tmp / "in"), "--out", str(tmp / "out"), "--answer-key", str(tmp / "key.txt"), *extra]


def test_check_key_prints_names_only(sample_dir, capsys):
    assert spike.main(["--check-key", "--answer-key", str(sample_dir / "key.txt")]) == 0
    out = capsys.readouterr().out
    assert "capacity_stated -> stated_capacity" in out and "options -> multiple_options" in out
    assert "kept, not scored: pages_total, contradictions" in out
    assert "not recognised, not scored: Total system cost incl everything" in out
    for value in ("01/10/2026", "1,97,000", "Example PV", "78,000", "550"):
        assert value not in out


def test_pages_total_warning(sample_dir, capsys):
    (sample_dir / "key.txt").write_text("[S1]\npages_total: 3\n[S2]\npages_total: 2\n", encoding="utf-8")
    assert spike.main(args(sample_dir, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert out.count("warning: the answer key says") == 1
    assert "warning: the answer key says 3 pages, the file has 2" in out


def test_dry_run_end_to_end(sample_dir, capsys):
    assert spike.main(args(sample_dir, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and "correct" in out and "abstained" in out
    assert "S1: 2 pages in the file, 2 rendered, 0 skipped" in out
    for text in ("1,80,000", "1,97,000", "EX-VR-0001", "Example PV", "Net meter", "01/10/2026"):
        assert text not in out  # never prints document text
    (run_dir,) = (sample_dir / "out").iterdir()
    assert {p.name for p in run_dir.iterdir()} == {
        "run.json", "summary.txt", "scores.json", "global.amazon.nova-2-lite-v1_0", "apac.amazon.nova-pro-v1_0"}
    doc = run_dir / "global.amazon.nova-2-lite-v1_0" / "S1"
    batch = json.loads((doc / "batch-1.json").read_text(encoding="utf-8"))
    assert batch["usage"]["inputTokens"] == 1500 and batch["pages"] == [1, 2] and "seconds" in batch
    assert json.loads((doc / "quote.json").read_text(encoding="utf-8"))["processing_complete"] is True
    scores = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))
    assert scores["apac.amazon.nova-pro-v1:0"]["scores"]["S1"]["gross_total"]["status"] == "correct"


def test_out_inside_repo_refused(sample_dir, capsys):
    argv = args(sample_dir, "--dry-run")
    argv[argv.index("--out") + 1] = str(ROOT / "spike_out")
    assert spike.main(argv) == spike.STOPPED
    assert "outside the repository" in capsys.readouterr().err
    assert not (ROOT / "spike_out").exists()


def test_access_denied_stops_the_whole_run(sample_dir, capsys, monkeypatch):
    from extract.dryrun import stubbed_client

    calls = []

    class Denied:
        def __init__(self, region):
            calls.append(region)
            self.client, stubber = stubbed_client("bedrock-runtime")
            stubber.add_client_error("converse", service_error_code="AccessDeniedException",
                                     service_message="synthetic", http_status_code=403)

        def converse(self, **request):
            return self.client.converse(**request)

    monkeypatch.setattr(spike, "DryRunClient", Denied)
    assert spike.main(args(sample_dir, "--dry-run")) == spike.STOPPED
    assert len(calls) == 1  # the second model never ran
    assert "AccessDeniedException" in capsys.readouterr().err
    (run_dir,) = (sample_dir / "out").iterdir()
    batch = json.loads((run_dir / "global.amazon.nova-2-lite-v1_0" / "S1" / "batch-1.json").read_text("utf-8"))
    assert batch["failure"]["code"] == "AccessDeniedException"
    assert not (run_dir / "apac.amazon.nova-pro-v1_0").exists()
