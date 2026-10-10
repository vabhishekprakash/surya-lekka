"""Draft the interface messages in another language once, with Amazon Translate, for review.

    python scripts/translate_messages.py --languages te,hi --csv-dir H:/solar-data/translations [--dry-run]

Every finding message, vendor question and vendor-message line is keyed (src/checks/messages.py).
This sends them, as one HTML document per language, to Amazon Translate in ap-south-1. Placeholders,
amounts, numbers, units, "GST", "DCR" and other fixed terms are marked translate="no" so they come
back exactly as written. It writes:

  web/i18n/<lang>.json      the draft for languages listed in --ship (Telugu); the app loads only the
                            reviewed pack that scripts/build_telugu.py builds from it (web/te.js)
  <csv-dir>/<lang>_review.csv   key | English | <language> draft | correction | problems, outside the repo

Translate is never called at runtime. Each string is checked: every placeholder, number and ₹
amount must survive unchanged; any that doesn't is listed under "problems" for the reviewer.
"""

import argparse
import csv
import html
import json
import re
import sys
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from checks import messages  # noqa: E402
from checks.message_parts import WORDS  # noqa: E402
from checks.questions import INTRO, OUTRO, TEMPLATES as QUESTIONS  # noqa: E402

REGION = "ap-south-1"
PRICE_PER_CHARACTER_USD = 15 / 1_000_000
NAMES = {"te": "Telugu", "hi": "Hindi"}
# Kept exactly as written: placeholders, rupee amounts, numbers with their units, and fixed terms.
KEEP = re.compile(r"\{\w+\}|₹[0-9,]+(?:\.[0-9]+)?|\b\d+(?:\.\d+)?\s*(?:kWp|kW|kVA|W)\b|\b\d+(?:[.,]\d+)*\b"
                  r"|\b(?:kWp|kW|kVA|GST|DCR|CFA|MNRE|RWAs?|PV)\b|Give It Up|National Portal")


def strings():
    """{key: English text} for every keyed message, and every word the checks fill into one
    (checks.message_parts.WORDS) that has words in it: punctuation-only joins are not sent."""
    out = {key: template for key, (_, template) in messages.TEMPLATES.items()}
    out.update({f"question.{qid}": text for qid, text in QUESTIONS.items()})
    out.update({"vendor.intro": INTRO, "vendor.outro": OUTRO})
    out.update({k: v for k, v in WORDS.items() if re.search(r"[A-Za-z]", re.sub(r"\{\w+\}", "", v))})
    return out


def protect(text):
    """HTML with each fixed piece marked translate="no"."""
    parts, pos = [], 0
    for m in KEEP.finditer(text):
        parts.append(html.escape(text[pos:m.start()]))
        parts.append(f'<span translate="no">{html.escape(m.group(0))}</span>')
        pos = m.end()
    parts.append(html.escape(text[pos:]))
    return "".join(parts)


def document(texts):
    body = "".join(f'<p id="{html.escape(key)}">{protect(text)}</p>' for key, text in texts.items())
    return f'<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>{body}</body></html>'


class _Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.key = {}, None

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self.key = dict(attrs).get("id")
            self.out[self.key] = ""

    def handle_endtag(self, tag):
        if tag == "p":
            self.key = None

    def handle_data(self, data):
        if self.key is not None:
            self.out[self.key] += data


def read_document(text):
    parser = _Paragraphs()
    parser.feed(text)
    return {k: " ".join(v.split()) for k, v in parser.out.items()}


def problems(english, translated):
    """What didn't survive translation exactly: placeholders, numbers and ₹ amounts."""
    out = []
    if not translated:
        return ["missing"]
    def count(piece, text):  # whole pieces only: "₹1" is not inside "₹10"
        return len(re.findall(rf"(?<![\d.,]){re.escape(piece)}(?![\d]|[.,]\d)", text))
    for piece in sorted(set(KEEP.findall(english))):
        if piece[0] in "{₹0123456789" and count(piece, english) != count(piece, translated):
            out.append(f"{piece} appears {count(piece, translated)} times, not {count(piece, english)}")
    return out


# Placeholders the checks fill with English words or phrases (field names, "more", the worked sum).
# A translated sentence around them still shows those words in English until they are keyed too.
ENGLISH_FILLS = {"sums", "what", "direction", "hint", "fields", "panels", "charges", "gaps", "options", "text",
                 "question_key", "cutoff"}


def notes(english):
    filled = sorted(set(re.findall(r"\{(\w+)\}", english)) & ENGLISH_FILLS)
    return [f"fills in English words: {', '.join('{' + f + '}' for f in filled)}"] if filled else []


def recheck(path):
    """Rewrite the problems column of an existing review CSV, without calling Translate."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    header, body = rows[0], rows[1:]
    for row in body:
        row[4] = "; ".join(problems(row[1], row[2]) + notes(row[1]))
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerows([header] + body)
    return sum(bool(row[4]) for row in body)


def translate(texts, language, client):
    if not texts:
        return {}
    reply = client.translate_document(
        Document={"Content": document(texts).encode("utf-8"), "ContentType": "text/html"},
        SourceLanguageCode="en", TargetLanguageCode=language)
    drafted = read_document(reply["TranslatedDocument"]["Content"].decode("utf-8"))
    # A join word such as " and " keeps the spaces around it: they are part of the sentence.
    return {k: _spaced(texts[k], v) for k, v in drafted.items() if k in texts}


def _spaced(english, drafted):
    lead = english[:len(english) - len(english.lstrip())]
    trail = english[len(english.rstrip()):]
    return lead + drafted.strip() + trail


def read_review(path):
    """{key: (draft, correction)} from an existing review CSV, or {}."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    return {row[0]: (row[2], row[3]) for row in rows[1:] if row}


class DryRun:
    """Answers like Translate would, without AWS: each text comes back unchanged."""

    def translate_document(self, Document, SourceLanguageCode, TargetLanguageCode):
        return {"TranslatedDocument": {"Content": Document["Content"]}}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--languages", default="te,hi")
    p.add_argument("--ship", default="te", help="languages whose draft is written under web/i18n/")
    p.add_argument("--csv-dir", type=Path, required=True)
    p.add_argument("--profile", default="default")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--recheck", action="store_true", help="only rewrite the problems column of existing CSVs")
    p.add_argument("--update", action="store_true",
                   help="keep every draft and correction in the existing CSVs; translate only keys they lack")
    args = p.parse_args(argv)
    if args.recheck:
        for language in [x.strip() for x in args.languages.split(",") if x.strip()]:
            path = args.csv_dir / f"{language}_review.csv"
            print(f"{language}: {recheck(path)} strings flagged in {path}")
        return 0
    if ROOT in args.csv_dir.resolve().parents or args.csv_dir.resolve() == ROOT:
        p.error("--csv-dir must be outside the repository")
    texts = strings()
    languages = [x.strip() for x in args.languages.split(",") if x.strip()]
    kept = {lang: read_review(args.csv_dir / f"{lang}_review.csv") if args.update else {} for lang in languages}
    todo = {lang: {k: v for k, v in texts.items() if k not in kept[lang]} for lang in languages}
    characters = sum(len(t) for lang in languages for t in todo[lang].values())
    print(f"{len(texts)} strings, {sum(len(todo[lang]) for lang in languages)} to translate, {characters} characters "
          f"for {', '.join(languages)}: estimated ${characters * PRICE_PER_CHARACTER_USD:.2f} at "
          f"${PRICE_PER_CHARACTER_USD * 1e6:.0f} per million")
    if args.dry_run:
        client = DryRun()
    else:
        import boto3
        client = boto3.Session(profile_name=args.profile).client("translate", region_name=REGION)
    args.csv_dir.mkdir(parents=True, exist_ok=True)
    for language in languages:
        drafted = {k: draft for k, (draft, _) in kept[language].items()}
        drafted.update(translate(todo[language], language, client))
        corrections = {k: fix for k, (_, fix) in kept[language].items()}
        flagged = 0
        path = args.csv_dir / f"{language}_review.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["key", "English", f"{NAMES.get(language, language)} draft", "correction", "problems"])
            for key, english in texts.items():
                issues = problems(english, drafted.get(key))
                flagged += bool(issues)
                w.writerow([key, english, drafted.get(key, ""), corrections.get(key, ""), "; ".join(issues)])
        print(f"{language}: {len(todo[language])} newly drafted, {len(texts)} in all, "
              f"{sum(bool(c) for c in corrections.values())} corrections kept, {flagged} flagged; review CSV {path}")
        if language in args.ship.split(","):
            out = ROOT / "web" / "i18n" / f"{language}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({
                "_status": "draft: machine translation, not yet reviewed; the app does not show it",
                "_source": f"Amazon Translate ({REGION}), drafted {date.today().isoformat()}"
                           + (" (dry run)" if args.dry_run else ""),
                "language": language,
                "strings": {k: corrections.get(k) or drafted.get(k, "") for k in texts},
            }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"{language}: draft written to {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
