import json
import runpy
from pathlib import Path

import pytest

from extract import scoring
from extract.dryrun import load_dry_run_wire
from extract.merge import merge_batches
from extract.wire_schema import to_contract, validate

ROOT = Path(__file__).resolve().parent.parent

KEY = """# synthetic answer key
[S1]
stated_capacity: 3.3 kWp | 1
Base Price: Rs. 1,80,000 | p2
discount: 1,520 | page 2
subsidy_state: null
subsidy_combined: -
panel count: 6 | 1
total system cost incl everything: 123
gst_amount = Rs. 99 | 2

## S2
dcr: yes
net: not found
multiple_options: no
S3:
panel_wattage: 550 Wp + 545 Wp
"""


def sample_quote():
    cleaned, errors, _ = validate(load_dry_run_wire(), [1, 2])
    assert errors == []
    return merge_batches([{"batch": 1, "pages": [1, 2], "contract": to_contract(cleaned, 1)}])


# --- answer key ------------------------------------------------------------------------

def test_parse_answer_key():
    key = scoring.parse_answer_key("stray: 1\n" + KEY)
    assert set(key["docs"]) == {"S1", "S2", "S3"}
    s1 = key["docs"]["S1"]
    assert s1["stated_capacity"] == {"value": "3.3 kWp", "pages": [1]}
    assert s1["base_price"] == {"value": "Rs. 1,80,000", "pages": [2]}
    assert s1["discount"]["pages"] == [2]
    assert s1["subsidy_state"]["value"] is None and s1["subsidy_combined"]["value"] is None
    assert s1["panel_count"]["value"] == "6"
    assert s1["gst_amount"]["value"] == "Rs. 99"
    assert key["unknown"] == {"S1": ["total system cost incl everything"]}
    assert key["docs"]["S2"] == {"dcr_declaration": {"value": "yes", "pages": []},
                                 "net_cost": {"value": None, "pages": []},
                                 "multiple_options": {"value": "no", "pages": []}}
    assert key["orphan_lines"] == 1


def test_header_styles_and_comments():
    text = "== Q12 ==\nbase_price: 1\n# a comment line\n// another\n-- Q16 --\nbase_price: 2\nDocument: Q20\ngst: 3\n"
    key = scoring.parse_answer_key(text)
    assert {d: list(v) for d, v in key["docs"].items()} == {
        "Q12": ["base_price"], "Q16": ["base_price"], "Q20": ["gst_amount"]}
    key = scoring.parse_answer_key("[Q12]\nbase_price: 1\nbase_price: 2\n550\n")
    assert key["duplicates"] == {"Q12": ["base_price"]} and key["docs"]["Q12"]["base_price"]["value"] == "2"
    assert set(key["docs"]) == {"Q12"}  # a bare number is not a document header


# --- scoring ---------------------------------------------------------------------------

def test_score_statuses():
    quote = sample_quote()
    key = scoring.parse_answer_key(KEY)["docs"]["S1"]
    scores = scoring.score_document(quote, key)
    assert scores["stated_capacity"] == {"status": "correct", "conflict": False, "page_ok": True}
    assert scores["base_price"]["status"] == "correct" and scores["base_price"]["page_ok"] is True
    assert scores["discount"]["status"] == "correct"
    assert scores["gst_amount"]["status"] == "wrong"
    assert scores["subsidy_state"]["status"] == "abstained"
    assert scores["panel_count"]["status"] == "correct"
    quote["discount"] = None
    assert scoring.score_field(quote, "discount", key["discount"])["status"] == "missing"
    assert scoring.score_field(quote, "net_cost", {"value": None, "pages": []})["status"] == "falsely_populated"
    wrong_page = scoring.score_field(quote, "base_price", {"value": "180000", "pages": [1]})
    assert wrong_page == {"status": "correct", "conflict": False, "page_ok": False}


def test_flags_lists_and_units():
    quote = sample_quote()
    score = scoring.score_field
    assert score(quote, "multiple_options", {"value": "no", "pages": []})["status"] == "correct"
    assert score(quote, "dcr_declaration", {"value": "yes", "pages": []})["status"] == "correct"
    assert score(quote, "capacity_basis", {"value": "DC kWp", "pages": []})["status"] == "correct"
    assert score(quote, "panel_wattage", {"value": "0.55 kW", "pages": []})["status"] == "correct"
    assert score(quote, "stated_capacity", {"value": "3300 W", "pages": []})["status"] == "correct"
    assert score(quote, "inverter_rating", {"value": "3", "pages": []})["status"] == "correct"
    assert score(quote, "panel_count", {"value": "6 + 4", "pages": []})["status"] == "wrong"
    assert score(quote, "module_make_model", {"value": "example pv exm 550", "pages": []})["status"] == "correct"
    assert score(quote, "give_it_up", {"value": None, "pages": []})["status"] == "abstained"


def test_conflict_scores_as_missing_or_falsely_populated():
    quote = sample_quote()
    quote["gross_total"] = {"value": {"raw": None, "parsed": None, "parse_status": "conflict"},
                            "conflict": True, "candidates": []}
    assert scoring.score_field(quote, "gross_total", {"value": "197000", "pages": []}) == {
        "status": "missing", "conflict": True, "page_ok": None}
    assert scoring.score_field(quote, "gross_total", {"value": None, "pages": []})["status"] == "falsely_populated"


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


def test_dry_run_end_to_end(sample_dir, capsys):
    assert spike.main(args(sample_dir, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and "correct" in out and "abstained" in out
    for text in ("1,80,000", "EX-VR-0001", "Example PV", "Net meter"):
        assert text not in out  # never prints document text
    (run_dir,) = (sample_dir / "out").iterdir()
    assert {p.name for p in run_dir.iterdir()} == {
        "run.json", "summary.txt", "scores.json", "global.amazon.nova-2-lite-v1_0", "apac.amazon.nova-pro-v1_0"}
    doc = run_dir / "global.amazon.nova-2-lite-v1_0" / "S1"
    batch = json.loads((doc / "batch-1.json").read_text(encoding="utf-8"))
    assert batch["usage"]["inputTokens"] == 1500 and batch["pages"] == [1, 2] and "seconds" in batch
    assert json.loads((doc / "quote.json").read_text(encoding="utf-8"))["processing_complete"] is True
    scores = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))
    assert scores["apac.amazon.nova-pro-v1:0"]["scores"]["S1"]["base_price"]["status"] == "correct"


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
