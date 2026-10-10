"""Telugu (and Hindi) drafts of the keyed messages: drafted once by a script, stored as static
files, never translated at runtime. Fixed pieces must come back exactly."""

import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
T = runpy.run_path(str(ROOT / "scripts" / "translate_messages.py"))


def test_every_keyed_message_is_sent():
    from checks import messages
    keys = set(T["strings"]())
    assert set(messages.TEMPLATES) <= keys and "vendor.intro" in keys and "question.total_mismatch" in keys


@pytest.mark.parametrize("text,kept", [
    ("The quote's {what} is {stated}, which is {difference} {direction}.", ["{what}", "{stated}", "{difference}",
                                                                          "{direction}"]),
    ("within ₹1 of the {stated}", ["₹1", "{stated}"]),
    ("within 0.01 kWp of the quote's {stated_kwp} kWp.", ["0.01 kWp", "{stated_kwp}", "kWp"]),
    ("panels and cells. The central subsidy requires DCR", ["DCR"]),
    ("Give It Up", ["Give It Up"]),
])
def test_fixed_pieces_are_marked_not_to_translate(text, kept):
    html = T["protect"](text)
    for piece in kept:
        assert f'<span translate="no">{piece}</span>' in html.replace("&#x27;", "'")


def test_a_document_reads_back_by_key():
    texts = {"a.b": "Please check {fields}.", "c": "Hello & thanks"}
    assert T["read_document"](T["document"](texts)) == texts


@pytest.mark.parametrize("english,translated,flagged", [
    ("Total is {stated}.", "మొత్తం {stated}.", False),
    ("Total is {stated}.", "మొత్తం.", True),  # a placeholder lost
    ("within ₹1 of {stated}", "{stated} కి ₹10 లోపు", True),  # an amount changed
    ("30,000 x 2", "30,000 x 2", False),
    ("", "", True),
])
def test_problems_flag_anything_that_did_not_survive(english, translated, flagged):
    assert bool(T["problems"](english, translated)) is flagged


def test_a_dry_run_writes_the_draft_and_the_review_csvs(tmp_path, monkeypatch):
    monkeypatch.setitem(T["main"].__globals__, "ROOT", tmp_path / "repo")
    assert T["main"](["--languages", "te,hi", "--csv-dir", str(tmp_path / "csv"), "--dry-run"]) == 0
    draft = json.loads((tmp_path / "repo" / "web" / "i18n" / "te.json").read_text(encoding="utf-8"))
    assert "not yet reviewed" in draft["_status"] and set(draft["strings"]) == set(T["strings"]())
    assert not (tmp_path / "repo" / "web" / "i18n" / "hi.json").exists()  # Hindi ships only after a reviewer signs off
    header = (tmp_path / "csv" / "hi_review.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert header == "key,English,Hindi draft,correction,problems"


def test_the_csvs_must_be_written_outside_the_repo():
    with pytest.raises(SystemExit):
        T["main"](["--csv-dir", str(ROOT / "out"), "--dry-run"])


def test_the_app_never_calls_translate():
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "translate.ap-south-1" not in app and "TranslateText" not in app
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert 'client("translate"' not in text and "translate_document" not in text and "translate_text" not in text


def test_strings_that_fill_in_english_words_are_flagged_for_the_reviewer():
    assert T["notes"]("{sums}. The quote's {what} is {stated}.") == ["fills in English words: {sums}, {what}"]
    assert T["notes"]("Total is {stated}.") == []


def test_the_reviewed_telugu_covers_every_word_and_ships_only_as_te_js():
    pack = json.loads((ROOT / "web" / "i18n" / "te.json").read_text(encoding="utf-8"))
    assert set(T["strings"]()) <= set(pack["strings"]) and all(pack["strings"].values())
    assert pack["_status"].startswith("reviewed")
    build = runpy.run_path(str(ROOT / "scripts" / "build_telugu.py"))
    assert (ROOT / "web" / "te.js").read_text(encoding="utf-8") == build["te_js"]()  # rebuilt, not hand-edited
    assert set(pack["ui"]) == set(build["ui_english"]())
    assert not (ROOT / "web" / "i18n" / "hi.json").exists() and not (ROOT / "web" / "hi.js").exists()


def test_every_filled_in_word_is_sent_but_punctuation_is_not():
    keys = set(T["strings"]())
    assert {"word.total", "word.more", "join.and", "join.or", "range.to", "field.base_price", "gap.range",
            "month.feb", "charge.outside", "panels.many"} <= keys
    assert not {"join.comma", "join.semicolon", "join.none", "field.quoted", "sum.term", "sum.equals"} & keys


def test_a_join_word_keeps_its_spaces():
    assert T["_spaced"](" and ", "మరియు") == " మరియు " and T["_spaced"]("total", " మొత్తం ") == "మొత్తం"


def test_an_update_keeps_drafts_and_corrections_and_sends_only_new_keys(tmp_path, monkeypatch):
    import csv
    repo, csv_dir = tmp_path / "repo", tmp_path / "csv"
    csv_dir.mkdir()
    texts = T["strings"]()
    old = [k for k in texts if not k.startswith(("word.", "field.", "join.", "gap.", "month.", "charge.", "panels.",
                                                  "sum.", "range.", "hint.", "date"))]
    with open(csv_dir / "te_review.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["key", "English", "Telugu draft", "correction", "problems"])
        for k in old:
            w.writerow([k, texts[k], "OLD DRAFT", "MY FIX" if k == "confirm.operands" else "", ""])
    sent = []

    class Recording(T["DryRun"]):
        def translate_document(self, Document, SourceLanguageCode, TargetLanguageCode):
            sent.append(T["read_document"](Document["Content"].decode("utf-8")))
            return super().translate_document(Document, SourceLanguageCode, TargetLanguageCode)
    monkeypatch.setitem(T["main"].__globals__, "ROOT", repo)
    monkeypatch.setitem(T["main"].__globals__, "DryRun", Recording)
    assert T["main"](["--languages", "te", "--ship", "te", "--csv-dir", str(csv_dir), "--dry-run", "--update"]) == 0
    assert set(sent[0]) == set(texts) - set(old)  # only the new keys went to Translate
    rows = {r[0]: r for r in csv.reader(open(csv_dir / "te_review.csv", encoding="utf-8-sig"))}
    assert rows["confirm.operands"][2:4] == ["OLD DRAFT", "MY FIX"] and rows["C1.matches"][2] == "OLD DRAFT"
    draft = json.loads((repo / "web" / "i18n" / "te.json").read_text(encoding="utf-8"))["strings"]
    assert draft["confirm.operands"] == "MY FIX" and draft["C1.matches"] == "OLD DRAFT"  # a correction wins
