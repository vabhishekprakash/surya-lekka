import json
import re
from pathlib import Path

from rules import load_cfa_rules

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
INDEX = (WEB / "index.html").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")
CONFIG = (WEB / "config.js").read_text(encoding="utf-8")


def js_list(name):
    match = re.search(rf"const {name} = (\[.*?\]);", APP, re.S)
    return json.loads(re.sub(r",\s*\]", "]", match.group(1)))


def test_state_list_matches_the_rules():
    rules = load_cfa_rules()
    states = rules["special_category"]["states"] + rules["general_category"]["states"]
    territories = rules["special_category"]["union_territories"] + rules["general_category"]["union_territories"]
    assert js_list("STATES") == sorted(states)
    assert js_list("UNION_TERRITORIES") == sorted(territories)


def test_footer_says_what_is_not_checked_and_where_documents_go():
    assert ("This is not financial or legal advice. Eligibility (DCR panels, registration, inspection) "
            "is not verified.") in INDEX
    assert ("Documents may be processed outside India through AWS cross-Region inference. "
            "Files are deleted after reading.") in INDEX


def test_every_result_has_a_mode_label():
    labels = re.search(r"const MODE_LABELS = \{(.*?)\};", APP, re.S).group(1)
    assert 'saved: "Sample (saved reading)"' in labels
    assert 'nova: "Read by Amazon Nova"' in labels
    assert 'manual: "Entered by you"' in labels
    assert INDEX.count("data-mode-label") == 2  # review and results


def test_api_base_comes_from_the_config_file():
    assert re.search(r'API_BASE:\s*""', CONFIG)
    assert INDEX.index('src="config.js"') < INDEX.index('src="app.js"')
    assert "window.SURYA_CONFIG" in APP


def test_scripts_are_pinned_and_only_from_cdnjs():
    urls = re.findall(r"https://[^\s\"'`]+", INDEX + APP)
    scripts = [u for u in urls if u.endswith((".js", ".mjs"))]
    assert scripts and all(u.startswith("https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/") for u in scripts)


def test_samples_never_ask_for_a_live_reading():
    assert "live=1" not in APP and "?live" not in APP


def test_page_limits_match_the_api():
    from extract.render import MAX_IMAGE_BYTES, MAX_PAGES, MAX_SIDE_PX

    assert f"const MAX_PAGES = {MAX_PAGES};" in APP
    assert f"const MAX_IMAGE_BYTES = {MAX_IMAGE_BYTES};" in APP
    assert f"const MAX_SIDE = {MAX_SIDE_PX};" in APP


def test_copy_has_no_dashes_or_model_hype():
    text = INDEX + APP + CONFIG
    assert "—" not in text and "–" not in text
    assert not re.search(r"\bAI\b|artificial intelligence|LLM|GPT", text)


def test_document_text_is_never_inserted_as_html():
    assert "innerHTML" not in APP and "insertAdjacentHTML" not in APP and "document.write" not in APP
