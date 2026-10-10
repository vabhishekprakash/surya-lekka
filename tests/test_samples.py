import json
from pathlib import Path

import pytest

from conftest import run_confirmed as run_checks  # checks after the household confirms the numbers
from checks.evidence import verify_evidence

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = sorted((ROOT / "samples" / "expected").glob("S*.json"))
FOOTER = "Made-up sample quote for testing"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def evidence_fields(node):
    if isinstance(node, dict):
        if "evidence_text" in node and "page" in node and node["evidence_text"]:
            yield node
        for v in node.values():
            yield from evidence_fields(v)
    elif isinstance(node, list):
        for v in node:
            yield from evidence_fields(v)


def test_three_samples():
    assert [p.stem for p in SAMPLES] == ["S1", "S2", "S3"]


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
def test_sample_findings_match_expected(path):
    sample = load(path)
    result = run_checks(sample["quote"], sample["user_inputs"])
    actual = [{"check_id": f["check_id"], "item": f["item"], "status": f["status"]} for f in result["findings"]]
    assert actual == sample["expected"]["findings"]
    assert [q["id"] for q in result["questions"]] == sample["expected"]["question_ids"]


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
def test_sample_evidence_is_on_its_page(path):
    sample = load(path)
    pages = {int(k): "\n".join(v) for k, v in sample["pages"].items()}
    fields = list(evidence_fields(sample["quote"]))
    assert fields
    for f in fields:
        assert verify_evidence(f, pages) == "text_matched", f["evidence_text"]


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
def test_sample_pdf_text_layer(path, tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    import runpy

    render = runpy.run_path(str(ROOT / "samples" / "generate.py"))["render"]
    sample = load(path)
    out = tmp_path / f"{sample['sample_id']}.pdf"
    render(sample, out)
    with pymupdf.open(out) as doc:
        texts = {i + 1: page.get_text() for i, page in enumerate(doc)}
        assert len(doc) == len(sample["pages"])
        for page in doc:  # one small footer at the bottom, away from the header; no watermark
            footer = [b for b in page.get_text("blocks") if FOOTER in b[4]]
            assert len(footer) == 1 and footer[0][1] > 0.9 * page.rect.height
    assert all(FOOTER in t and "NOT A REAL" not in t for t in texts.values())
    for f in evidence_fields(sample["quote"]):
        assert verify_evidence(f, texts) == "text_matched", f["evidence_text"]


def test_samples_use_made_up_vendor_only():
    for path in SAMPLES:
        text = path.read_text(encoding="utf-8")
        assert "Example Solar Pvt Ltd" in text and "+91 00000 00000" in text


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
def test_saved_reading_matches_the_sample(path):
    import runpy

    saved_reading = runpy.run_path(str(ROOT / "samples" / "generate.py"))["saved_reading"]
    sample = load(path)
    cached = load(ROOT / "samples" / "cached" / path.name)
    assert cached == saved_reading(sample)  # regenerate with: python samples/generate.py --saved-readings
    assert cached["reading"] == "saved" and "not read by a model" in cached["_reading"]
