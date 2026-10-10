import json
import re
from pathlib import Path

from rules import load_cfa_rules

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
INDEX = (WEB / "index.html").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")
RES = (WEB / "results.js").read_text(encoding="utf-8")  # the results screen, in English or Telugu
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
    assert ('return [String(CONFIG.REGION || ""), CONFIG.CROSS_REGION === true, String(CONFIG.ENGINE || ""), '
            'CONFIG.AI_OPT_OUT === true];') in APP and "privacyLine(...privacySettings())" in APP
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
    assert 'saved: "Saved reading of a made-up quote. Not read live."' in labels
    assert 'nova: "Read by Amazon Nova"' in labels
    assert 'textract: "Read by Amazon Textract"' in labels
    assert 'manual: "Entered by you"' in labels
    from api.common import READING_ENGINES

    assert all(re.search(rf"\b{engine}: \"", labels) for engine in READING_ENGINES)
    assert INDEX.count("data-mode-label") == 1 and 'id="results-mode"' in INDEX  # review; results in its language
    for engine in ("saved", "nova", "textract", "manual"):
        assert re.search(rf'"mode.{engine}": "', RES)


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


def test_finding_thumbnails_float_beside_their_line():
    css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
    assert ".finding li .thumb { float: right;" in css and ".finding li::after" in css


def test_review_lets_the_household_add_a_missed_charge():
    review = APP[APP.index("function renderReviewFields()"):APP.index("function renderOptionChoice()")]
    assert 'text: "Add a charge"' in review and "addReviewCharge()" in review
    assert 'const ADDED_INCLUDED_CHOICES = [["unclear", "Not sure"], ["yes", "Yes"], ["no", "No"]];' in APP
    collect = APP[APP.index("function collectReview()"):APP.index("async function submitReview(")]
    assert "extra_charges[U${++added}]" in collect  # the ids the backend accepts for added charges
    assert "answers.extra_charges_complete = answerValue(complete.value)" in collect
    assert '"(entered by you{was})"' in RES.replace("{label}: {value} ", "")


def test_every_charge_question_sits_beside_the_charges_and_is_never_prefilled():
    assert 'text: "Is every charge on your quote listed here?", beside: "charges"' in APP
    row = APP[APP.index("function chargesCompleteRow()"):APP.index("function renderReviewFlags()")]
    assert '[["unclear", "Not sure"], ["true", "Yes"], ["false", "No"]]' in row and "proposed" not in row
    assert "FLAGS.filter((flag) => !flag.beside)" in APP
    manual = INDEX[INDEX.index('<select name="extra_charges_complete"'):]
    assert "Is every charge on your quote listed here?" in INDEX and '<option value="false">No</option>' in manual


def test_amounts_on_the_page_use_the_rupee_sign_without_a_space():
    assert 'return "₹" + n.toLocaleString("en-IN", { maximumFractionDigits: 2 });' in RES
    assert "Rs " not in APP.replace("Rs ${", "") and "Rs " not in RES and 'placeholder="Rs"' not in INDEX


def test_the_suppliers_gst_state_is_shown_as_information_only():
    review = APP[APP.index("function renderReviewFields()"):APP.index("function renderOptionChoice()")]
    assert "q.supplier_gst_state" in review
    assert "registered for GST in" in review and "not where your house is" in review
    assert "supplier_gst_state" not in APP[APP.index("function renderQuestions("):APP.index("// ---------------------------------------------------------------- manual entry")]


def test_amounts_the_household_enters_show_in_rupees():
    branch = RES[RES.index('if (e.kind === "user_corrected") {'):RES.index('if (e.kind === "user_confirmed") {')]
    assert "shownValue(e.field, e.value, e.raw, ctx)" in branch
    shown = RES[RES.index("function shownValue("):RES.index("function evidenceItem(")]
    assert "isAmountField(field)" in shown and "formatInr(value)" in shown and "export function isAmountField(" in RES


def test_values_to_check_each_have_their_own_tick_and_no_bulk_accept():
    assert "data-verify" in APP and "verified" in APP
    assert 'el("input", { type: "checkbox", id, "data-verify": entry.token, "data-verify-path": path })' in APP
    for bulk in ("confirm all", "accept all", "check all", "select all", "tick all"):
        assert bulk not in APP.lower() and bulk not in INDEX.lower()
    # a conflicting value has no tick: the household types the right one
    assert 'if (!entry.reasons.includes("conflict"))' in APP
    # a ticked value that was also changed is sent as a correction, not as checked
    assert "!(path in corrections)" in APP and "map((box) => box.dataset.verify)" in APP


def test_check_this_reasons_cover_every_reason_the_checks_give():
    from checks.check_this import REASONS
    words = re.search(r"const CHECK_REASONS = \{(.*?)\};", APP, re.S).group(1)
    assert sorted(re.findall(r"^\s*(\w+):", words, re.M)) == sorted(REASONS)


def test_typed_numbers_the_guards_ask_about_get_their_own_tick():
    assert '"data-confirm-entry": entry.token' in APP and "This number is right as I typed it" in APP
    collect = APP[APP.index("function collectManual()"):APP.index("async function submitManual(")]
    assert "return { fields, answers, verified }" in collect and "data-confirm-entry" in collect
    # a note goes away as soon as the number is changed, so a tick never confirms a new number
    assert 'input.addEventListener("input", () => note.remove(), { once: true })' in APP
    assert "state.entryChecks = result.entry_checks || []" in APP


def test_a_held_finding_asks_about_its_numbers_with_yes_or_fix():
    card = RES[RES.index("function operandItem(o, ctx)"):RES.index("function ruleLine(")]
    assert 't(lang, "op.yes")' in card and 't(lang, "op.fix")' in card and '"op.typed"' in card
    assert '"op.yes": "Yes"' in RES and '"op.fix": "Fix a number"' in RES and "(you typed this)" in RES
    assert "f.operands_confirmed !== false" in card and "state.confirmedOperands.add(token)" in APP
    # a fresh form submission starts with nothing confirmed; "Yes" sends what was confirmed since,
    # and the server decides which still apply
    assert APP.count("state.confirmedOperands = new Set();") >= 3 and APP.count("confirmed_operands: []") == 2
    assert APP.count("confirmed_operands: [...state.confirmedOperands]") == 1
    # typed-in numbers: the challenge from the last answer goes back with the next request
    assert "if (result.challenge) state.challenge = result.challenge;" in APP
    assert "body.challenge = state.challenge" in APP and "challenge: state.challenge" in APP
    assert "state.confirmedOperands = new Set();" in APP[APP.index("function openReview("):]


def test_values_are_highlighted_on_their_page():
    viewer = APP[APP.index("function openPage("):APP.index("function closePage(")] if "function closePage(" in APP \
        else APP[APP.index("function openPage("):]
    assert 'class: "hl"' in viewer and "left:${left * 100}%" in viewer and "scrollIntoView" in viewer
    assert "state.boxIndex = indexBoxes(view.extraction)" in APP and "state.pageImages = view.page_images || []" in APP
    # tapping a quoted value opens its page at its box, in the review, the results and the confirm step
    assert APP.count("quoteButton(") >= 3 and RES.count("ctx.quoteButton(") >= 3
    # conflicting evidence shows each candidate with its own box
    assert "c.boxes" in APP[APP.index("function evidenceLine("):APP.index("function readFromQuote(")]
    # after a correction, what the quote said stays visible with its source
    assert "Read from the quote: ${input.dataset.original}" in APP
    assert '"op.typed_read": "{label}: {value} (you typed this). Read from the quote: {read}, "' in RES
    css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
    assert ".page-frame .hl { position: absolute;" in css and ".page-frame { position: relative;" in css


def test_uploads_send_the_page_plan_with_original_numbers_and_left_out_pages():
    assert 'import { PagePlan, REASONS } from "./pages.js";' in APP
    assert 'api("POST", "/jobs", state.plan.request())' in APP
    pdf = APP[APP.index("async function renderPdf("):APP.index("async function photoToJpeg(")]
    for reason in ('plan.omit("over_limit")', 'plan.omit("too_large")', 'plan.omit("unreadable")'):
        assert reason in pdf
    assert "state.pages.find((p) => p.page === pageNumber)" in APP  # page images by their own number


def test_requests_time_out_with_a_clear_message():
    assert "const API_TIMEOUT_MS = 20000;" in APP and "const UPLOAD_TIMEOUT_MS = 60000;" in APP
    assert "controller.abort()" in APP and 'new ApiError(0, "timeout", TIMED_OUT)' in APP
    assert 'showProblem("The checker didn\'t answer in time", error.message)' in APP


def test_answers_to_replaced_requests_are_ignored():
    for name in ("async function submitReview(", "async function submitManual(", "async function startSample(",
                 "async function confirmOperands("):
        body = APP[APP.index(name):APP.index("\n}\n", APP.index(name))]
        assert "nextRequest()" in body and "stillCurrent(seq)" in body, name


def test_a_failed_upload_can_be_tried_again_for_the_same_check():
    assert 'id="retry-upload"' in INDEX and "Try the upload again" in INDEX
    rest = APP[APP.index("async function uploadRest()"):APP.index("function uploadFailed(")]
    assert "if (done.has(target.page)) continue;" in rest and "done.add(target.page)" in rest
    assert "state.upload.until" in APP  # only while the check's upload links last


def test_focus_moves_to_every_new_screens_heading_and_confirming_keeps_the_place():
    assert '<h1 tabindex="-1">' in INDEX
    show = APP[APP.index("function show(view)"):APP.index("function canShow(")]
    assert "heading.focus" in show and 'view !== "home"' not in show
    results = APP[APP.index("function openResults("):APP.index("// With Textract, the opt-out")]
    assert "keepPlace" in results and "return;" in results
    assert "openResults(result, { keepPlace: true, focusCheck: check })" in APP


def test_an_error_about_a_field_is_linked_to_that_field():
    assert 'named.setAttribute("aria-invalid", "true")' in APP
    assert 'named.setAttribute("aria-describedby", status.id)' in APP
    assert APP.count("markInvalid(") >= 3


def test_the_privacy_note_sits_beside_the_upload_button():
    upload = INDEX[INDEX.index('id="view-upload"'):INDEX.index('id="view-wait"')]
    assert 'id="privacy-upload"' in upload
    assert '$("#privacy-upload").textContent = privacy' in APP


def test_the_typed_numbers_screen_says_exactly_what_is_kept():
    manual = INDEX[INDEX.index('id="view-manual"'):INDEX.index('id="manual-form"')]
    assert "only a random id, a counter and a hash of the values (no numbers)" in manual
    assert "15 minutes" in manual


def test_the_results_screen_has_a_telugu_english_switch_remembered_safely():
    results = INDEX[INDEX.index('id="view-results"'):INDEX.index("</main>")]
    assert 'data-lang="te" lang="te"' in results and ">తెలుగు</button>" in results
    assert 'data-lang="en" lang="en" aria-pressed="true">English</button>' in results  # English by default
    remember = APP[APP.index("async function chooseLanguage("):APP.index("function rememberedLanguage()")]
    assert "try {\n    localStorage.setItem(LANG_KEY, code);\n  } catch {" in remember
    read = APP[APP.index("function rememberedLanguage()"):]
    assert "try {\n    return localStorage.getItem(LANG_KEY)" in read and "} catch {\n    return \"en\";" in read
    # Telugu text carries lang="te"; a result with any piece lacking Telugu is shown wholly in English
    assert 'node.setAttribute("lang", lang.code)' in APP and "NOT_IN_TELUGU" in APP
    render = APP[APP.index("function renderResults()"):APP.index("function setLang(")]
    assert "error instanceof MissingText" in render and "lang = ENGLISH;" in render
    # the upload and review screens stay English
    assert 'applyLanguage(view === "results" ? state.shownLang : ENGLISH)' in APP
    assert 'await import("./te.js")' in APP and "translate" not in APP.lower().replace("translated", "")


def test_only_telugu_ships_and_hindi_is_not_offered():
    names = {p.name for p in WEB.iterdir() if p.is_file()}
    assert "te.js" in names and not any(n.startswith("hi") for n in names)
