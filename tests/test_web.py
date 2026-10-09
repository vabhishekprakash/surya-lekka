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


def js_string(name):
    return json.loads(re.search(rf'^\s*{name}: (".*"),$', APP, re.M).group(1))


def privacy_line(kind, region):
    names = {"ap-south-1": "AWS's Mumbai region (India)", "ap-southeast-2": "AWS's Sydney region (Australia)"}
    assert f'"{region}": "{names[region]}"' in APP
    return js_string(kind).replace("{where}", names[region])


def test_footer_says_what_is_not_checked():
    assert ("This is not financial or legal advice. Eligibility (DCR panels, registration, inspection) "
            "is not verified.") in INDEX


def test_privacy_line_names_the_deploy_region():
    assert '<p id="privacy-line"></p>' in INDEX and "cross-Region inference. Files are" not in INDEX
    assert privacy_line("inRegion", "ap-south-1") == (
        "Your pages are processed in AWS's Mumbai region (India) and deleted after reading. If reading fails, "
        "they're removed automatically, usually within two days.")
    assert privacy_line("inRegion", "ap-southeast-2") == (
        "Your pages are processed in AWS's Sydney region (Australia) and deleted after reading. If reading fails, "
        "they're removed automatically, usually within two days.")
    # With an inference profile pages may be read elsewhere, so the notice must not claim one Region.
    assert "may be read in other AWS regions" in privacy_line("crossRegion", "ap-south-1")
    assert js_string("local") == "This copy runs on your own computer, so your pages stay on it."


def test_textract_privacy_lines_depend_on_the_confirmed_opt_out():
    assert privacy_line("textractOptedOut", "ap-south-1") == (
        "Your pages are read by Amazon Textract in AWS's Mumbai region (India) and deleted from our storage after "
        "reading. This AWS account has opted out of AWS using them to improve its services. If reading fails, "
        "they're removed automatically, usually within two days.")
    assert privacy_line("textract", "ap-south-1") == (
        "Your pages are read by Amazon Textract in AWS's Mumbai region (India) and deleted from our storage after "
        "reading. AWS may keep and use them to improve its AI services and may store some of that content in "
        "another AWS region. If reading fails, they're removed automatically, usually within two days.")
    assert ('privacyLine(String(CONFIG.REGION || ""), CONFIG.CROSS_REGION === true, String(CONFIG.ENGINE || ""), '
            'CONFIG.AI_OPT_OUT === true)') in APP
    body = APP[APP.index("function privacyLine("):APP.index("function init()")]
    assert 'engine === "textract"' in body and "optOut ? PRIVACY.textractOptedOut : PRIVACY.textract" in body


def test_reading_off_message_matches_the_api_and_leads_to_manual_entry():
    from api.common import READING_UNAVAILABLE

    assert f'const READING_UNAVAILABLE = "{READING_UNAVAILABLE}";' in APP
    assert 'error.code === "reading_unavailable"' in APP
    problem = INDEX[INDEX.index('data-view="problem"'):INDEX.index('data-view="review"')]
    assert 'data-go="manual">Type the numbers instead</button>' in problem


def test_every_result_has_a_mode_label():
    labels = re.search(r"const MODE_LABELS = \{(.*?)\};", APP, re.S).group(1)
    assert 'saved: "Sample (saved reading)"' in labels
    assert 'nova: "Read by Amazon Nova"' in labels
    assert 'textract: "Read by Amazon Textract"' in labels
    assert 'manual: "Entered by you"' in labels
    from api.common import READING_ENGINES

    assert all(re.search(rf"\b{engine}: \"", labels) for engine in READING_ENGINES)
    assert INDEX.count("data-mode-label") == 2  # review and results


def test_api_base_comes_from_the_config_file():
    assert re.search(r'API_BASE:\s*"", REGION: "", CROSS_REGION: false, ENGINE: "", AI_OPT_OUT: false', CONFIG)
    assert INDEX.index('src="config.js"') < INDEX.index('src="app.js"')
    assert "window.SURYA_CONFIG" in APP


def test_scripts_are_pinned_and_only_from_cdnjs():
    urls = re.findall(r"https://[^\s\"'`]+", INDEX + APP)
    scripts = [u for u in urls if u.endswith((".js", ".mjs"))]
    assert scripts and all(u.startswith("https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/") for u in scripts)


def test_pdfjs_is_checked_against_pinned_hashes():
    pinned = dict(re.findall(r'url: "(https://cdnjs[^"]+)",\s*integrity: "(sha384-[A-Za-z0-9+/]{64})"', APP))
    assert set(pinned) == {"https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.min.mjs",
                           "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.worker.min.mjs"}
    import_map = json.loads(re.search(r'<script type="importmap">(.*?)</script>', INDEX, re.S).group(1))
    main = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.min.mjs"
    assert import_map == {"integrity": {main: pinned[main]}}
    assert INDEX.index('type="importmap"') < INDEX.index('src="app.js"')
    assert 'rel: "modulepreload", href: url, integrity, crossorigin: "anonymous"' in APP
    assert 'fetch(url, { integrity, mode: "cors", credentials: "omit" })' in APP
    assert "import(PDFJS.url)" in APP and "import(PDFJS)" not in APP


def test_samples_never_ask_for_a_live_reading():
    assert "live=1" not in APP and "?live" not in APP


def test_page_limits_match_the_api():
    from extract.render import MAX_IMAGE_BYTES, MAX_PAGES, MAX_SIDE_PX

    assert f"const MAX_PAGES = {MAX_PAGES};" in APP
    assert f"const MAX_IMAGE_BYTES = {MAX_IMAGE_BYTES};" in APP
    assert f"const MAX_SIDE = {MAX_SIDE_PX};" in APP


def test_copy_has_no_dashes_or_model_hype():
    from api.common import READING_UNAVAILABLE

    text = (INDEX + APP + CONFIG).replace(READING_UNAVAILABLE, "")  # sentences the product owner chose
    text = text.replace(privacy_line("textract", "ap-south-1").replace("AWS's Mumbai region (India)", "{where}"), "")
    assert "—" not in text and "–" not in text
    assert not re.search(r"\bAI\b|artificial intelligence|LLM|GPT", text)


def test_document_text_is_never_inserted_as_html():
    assert "innerHTML" not in APP and "insertAdjacentHTML" not in APP and "document.write" not in APP
