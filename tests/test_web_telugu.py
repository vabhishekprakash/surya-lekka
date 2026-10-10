"""The results screen and the vendor message in Telugu: every finding, note, working, label and
vendor line from S1-S3, the message corpus and the positive controls renders fully in Telugu,
with no English words except the allowed terms and text quoted from the quote. Run with node."""

import copy
import json
import re
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import message_corpus as mc
from checks import run_checks

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node")
sys.path.insert(0, str(ROOT / "eval"))

ALLOWED = {"GST", "DCR", "DC", "CFA", "MNRE", "PV", "RWA", "RWAs", "Give", "It", "Up", "National", "Portal",
           "kWp", "kW", "kVA", "W", "Wp"}
LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'_-]*")

HARNESS = r"""
import { readFileSync } from "node:fs";
import * as R from "./web/results.js";
import pack from "./web/te.js";

const TE = R.language(pack);
function el(tag, attrs = {}, ...children) {
  const node = { tag, attrs: {}, children: [], text: "", quoted: false, hidden: false,
    addEventListener() {}, select() {} };
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") node.text = String(v);
    else if (k === "quoted") node.quoted = true;
    else if (!k.startsWith("on")) node.attrs[k] = v;
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.children.push(c);
  return node;
}
function collect(node, out) {
  if (typeof node === "string" || typeof node === "number") { out.shown.push(String(node)); return; }
  (node.quoted ? out.quoted : out.shown).push(node.text);
  for (const k of ["aria-label", "title"]) if (node.attrs[k]) out.shown.push(String(node.attrs[k]));
  for (const c of node.children) collect(c, node.quoted ? { shown: out.quoted, quoted: out.quoted } : out);
}
function context(lang, mode) {
  return { el, lang, mode, modeKey: mode, option: "", entryChecks: 1, readingField: () => null,
    quoteButton: (page, text) => el("button", { "aria-label": R.t(lang, "page.where", { n: page || 1 }) },
      el("q", { text, quoted: true })),
    pageButton: (page) => (page ? el("button", { "aria-label": R.t(lang, "page.show", { n: page }) }) : null),
    onConfirm() {}, onFix() {}, copyText: async () => {} };
}
const cases = JSON.parse(readFileSync(process.argv[2], "utf-8"));
const out = [];
for (const c of cases) {
  const row = { name: c.name };
  try {
    const te = R.buildResults(c.result, context(TE, c.mode));
    const texts = { shown: [te.summary, te.mode], quoted: [] };
    [...te.groups, ...te.vendor].forEach((n) => collect(n, texts));
    row.shown = texts.shown;
    row.quoted = texts.quoted;
    row.vendor = R.vendorMessage(c.result, TE) || "";
    row.footer = [R.t(TE, "foot.advice"), R.privacyText(TE, "ap-south-1", false, "textract", true),
      R.privacyText(TE, "ap-south-1", false, "textract", false), R.privacyText(TE, "ap-south-1", false, "", false),
      R.privacyText(TE, "ap-south-1", true, "", false), R.privacyText(TE, "", false, "", false)];
    const en = R.buildResults(c.result, context(R.ENGLISH, c.mode));
    const enTexts = { shown: [en.summary], quoted: [] };
    en.groups.forEach((n) => collect(n, enTexts));
    row.english = enTexts.shown;
    row.englishVendor = R.vendorMessage(c.result, R.ENGLISH) || "";
  } catch (error) {
    row.error = `${error.constructor.name}: ${error.message}`;
  }
  out.push(row);
}
console.log(JSON.stringify(out));
"""


def quote_text(quote):
    """Strings the quote itself printed (evidence, raw values, labels): shown as printed."""
    found = []

    def walk(node, in_flags=False):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, in_flags or k == "flags")
        elif isinstance(node, list):
            for v in node:
                walk(v, in_flags)
        elif isinstance(node, str) and not (in_flags and re.fullmatch(r"[a-z_]+", node)):
            found.append(node)  # a flag's value is our answer code, not text the quote printed
    walk(quote)
    return sorted(set(found), key=len, reverse=True)


def corpus():
    harness = runpy.run_path(str(ROOT / "eval" / "safety_harness.py"))
    from extract import scoring
    labels = scoring.parse_answer_key((ROOT / "eval" / "positive_controls.txt").read_text(encoding="utf-8"))["docs"]
    cases = []
    for name, quote, inputs in mc.cases():
        mode = "manual" if name.startswith("manual/") else "saved"
        quote = {**quote, "typed_by_household": (inputs or {}).get("corrections")}  # shown as typed
        cases.append({"name": name + "/held", "mode": mode, "quote": quote,
                      "result": run_checks({k: copy.deepcopy(v) for k, v in quote.items() if k != "typed_by_household"},
                                           copy.deepcopy(inputs))})
        cases.append({"name": name, "mode": mode, "quote": quote,
                      "result": mc.confirmed_run({k: v for k, v in quote.items() if k != "typed_by_household"}, inputs)})
    s4 = json.loads((ROOT / "samples" / "cached" / "S4.json").read_text(encoding="utf-8"))
    s4 = s4.get("quote", s4)
    bare = {"confirmations": mc.FULL, "corrections": {"stated_capacity": "3", "module_groups[G1].wattage": "500",
                                                      f"inverters[{s4['inverters'][0]['inverter_id']}].rating": "3"}}
    cases.append({"name": "bare_units", "mode": "saved", "quote": {**s4, "typed_by_household": bare["corrections"]},
                  "result": run_checks(copy.deepcopy(s4), copy.deepcopy(bare))})
    for doc, truth in labels.items():
        quote = harness["labelled_quote"](truth)
        result = harness["labelled"](truth, harness["answers"]("S-A", truth))
        cases.append({"name": f"control/{doc}", "mode": "textract", "quote": quote, "result": result})
    return cases


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    cases = corpus()
    folder = tmp_path_factory.mktemp("te")
    data = folder / "cases.json"
    data.write_text(json.dumps([{k: c[k] for k in ("name", "mode", "result")} for c in cases], default=str),
                    encoding="utf-8")
    script = ROOT / ".te_harness.mjs"
    script.write_text(HARNESS, encoding="utf-8")
    try:
        run = subprocess.run([NODE, str(script), str(data)], capture_output=True, text=True, cwd=ROOT, timeout=300,
                             encoding="utf-8")
    finally:
        script.unlink()
    assert run.returncode == 0, run.stderr[-2000:]
    return {c["name"]: (c, row) for c, row in zip(cases, json.loads(run.stdout))}


def english_words(text, quote):
    for printed in quote_text(quote):
        text = re.sub(r"(?<!\w)" + re.escape(printed) + r"(?!\w)", " ", text)
    return [w for w in LATIN_WORD.findall(text) if w not in ALLOWED]


def test_every_result_renders_fully_in_telugu(rendered):
    assert len(rendered) > 150
    for name, (case, row) in rendered.items():
        assert "error" not in row, (name, row.get("error"))
        shown = "\n".join(row["shown"] + [row["vendor"]] + row["footer"])
        assert not english_words(shown, case["quote"]), (name, english_words(shown, case["quote"])[:8])
        assert "{" not in shown and "}" not in shown and "undefined" not in shown and "NaN" not in shown, name
        assert "extra_charges[" not in shown, name
        assert re.search("[ఀ-౿]", shown), name


def test_quoted_text_is_only_text_the_quote_printed(rendered):
    for name, (case, row) in rendered.items():
        printed = quote_text(case["quote"])
        for q in row["quoted"]:
            assert q in printed or q == "", (name, "quoted text not from the quote")


def test_english_is_unchanged_by_the_switch(rendered):
    for name, (case, row) in rendered.items():
        result = case["result"]
        for f in result["findings"]:
            assert f["message"] in row["english"], name
            for note in f["notes"]:
                assert note in row["english"], name
        assert row["englishVendor"] == (result["vendor_message"] or ""), name


def test_the_vendor_message_is_telugu_line_by_line(rendered):
    with_questions = [(n, c, r) for n, (c, r) in rendered.items() if c["result"]["vendor_message"]]
    assert with_questions
    for name, case, row in with_questions:
        lines = row["vendor"].split("\n")
        assert len(lines) == len(case["result"]["vendor_message"].split("\n")), name
        assert lines[0].startswith("నమస్కారం"), name


def test_both_fills_of_what_read_right_in_c3_matches():
    pack = json.loads((ROOT / "web" / "i18n" / "te.json").read_text(encoding="utf-8"))["strings"]
    for word in ("word.total", "word.net_cost"):
        filled = pack["C3.matches"].replace("{what}", pack[word])
        assert pack[word] + "తో" in filled
    assert pack["word.total"] == "మొత్తం ధర" and pack["word.net_cost"] == "సబ్సిడీ తర్వాత ఖర్చు"


def test_every_dcr_rules_and_charges_note_is_keyed(rendered):
    seen = set()
    for _, (case, _) in rendered.items():
        for f in case["result"]["findings"]:
            for part in f["notes_parts"]:
                seen.add(part["key"])
    assert {"note.dcr", "note.rules_checked", "note.charges_not_added"} <= seen


def test_the_add_the_unit_messages_render_in_telugu(rendered):
    case, row = rendered["bare_units"]
    keys = {f["message_key"] for f in case["result"]["findings"]}
    assert {"C1.size_unit", "C4.inverter_rating.add_unit"} <= keys
    assert "error" not in row and any("యూనిట్" in text for text in row["shown"])
