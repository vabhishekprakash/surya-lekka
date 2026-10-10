"""Build the Telugu pack the results screen loads (web/te.js) from web/i18n/te.json.

    python scripts/build_telugu.py                     # te.json -> te.js
    python scripts/build_telugu.py --apply REVIEWED.csv --draft DRAFT.csv --new NEW.csv [--changes CHANGES.csv]
                                                       # check, then write te.json from the review

--changes applies later edits (key, kind, English, before, after), such as a native speaker's
wording, on top of the review; each "after" passes the same checks.

--apply checks the reviewed CSV (UTF-8, every key and English line the same as the app's current
strings, the drafts unchanged) and every Telugu line, reviewed or new: the same {placeholders},
numbers and ₹ amounts as the English, the same leading and trailing spaces, and a zero-width
non-joiner (U+200C) wherever a loanword such as కొటేషన్ takes a suffix. Any problem stops the
build and is listed. Prints counts and key names only.
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from checks.message_parts import ENGLISH  # noqa: E402

TE_JSON = ROOT / "web" / "i18n" / "te.json"
TE_JS = ROOT / "web" / "te.js"
RESULTS_JS = ROOT / "web" / "results.js"
ZWNJ = "‌"
PLACEHOLDER = re.compile(r"\{\w+\}")
NUMBER = re.compile(r"₹?[0-9][0-9,]*(?:\.[0-9]+)?")
# Loanwords ending in a virama that must keep U+200C before a Telugu suffix.
LOANWORDS = ("కొటేషన్", "ఆప్షన్", "కనెక్షన్", "వెండర్", "మెసేజ్", "రీజియన్", "అకౌంట్", "ఛత్తీస్", "అవుట్")
GLUED = re.compile("(" + "|".join(LOANWORDS) + ")(?=[క-హ])")
# Keys whose words are punctuation only: Telugu uses the English form.
PUNCTUATION = re.compile(r"^[\s{}\w,.;:()+=\"-]*$")


def ui_english():
    """UI_EN from web/results.js, the English the screen shows."""
    text = RESULTS_JS.read_text(encoding="utf-8")
    body = text[text.index("export const UI_EN = {") + len("export const UI_EN = "):]
    body = body[:body.index("\n};\n") + 2]
    body = "\n".join(line for line in body.splitlines() if not line.strip().startswith("//"))
    return json.loads(re.sub(r",\s*}$", "}", body.strip()))


def english():
    """{"strings": message vocabulary, "ui": screen words}, in English."""
    return {"strings": dict(ENGLISH), "ui": ui_english()}


def problems(key, english_text, telugu):
    out = []
    if not telugu:
        return [f"{key}: empty"]
    if Counter(PLACEHOLDER.findall(english_text)) != Counter(PLACEHOLDER.findall(telugu)):
        out.append(f"{key}: placeholders differ")
    plain_en, plain_te = PLACEHOLDER.sub("", english_text), PLACEHOLDER.sub("", telugu)
    if Counter(NUMBER.findall(plain_en)) != Counter(NUMBER.findall(plain_te)):
        out.append(f"{key}: numbers or amounts differ")
    lead = lambda s: len(s) - len(s.lstrip(" "))  # noqa: E731
    trail = lambda s: len(s) - len(s.rstrip(" "))  # noqa: E731
    if lead(english_text) != lead(telugu) or trail(english_text) != trail(telugu):
        out.append(f"{key}: leading or trailing spaces differ")
    if "�" in telugu:
        out.append(f"{key}: replacement character")
    if GLUED.search(telugu):
        out.append(f"{key}: loanword joined to a suffix without U+200C")
    return out


def read_csv(path):
    raw = Path(path).read_bytes()
    raw.decode("utf-8")  # strict: refuses anything that isn't UTF-8
    return list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))


def apply(reviewed_path, draft_path, new_path, changes_path=None):
    en = english()
    reviewed, draft, new = read_csv(reviewed_path), read_csv(draft_path), read_csv(new_path)
    found = []
    if len(reviewed) != 147:
        found.append(f"reviewed CSV has {len(reviewed)} rows, not 147")
    if [r["key"] for r in reviewed] != [r["key"] for r in draft]:
        found.append("reviewed keys differ from the draft's")
    strings, kept_zwnj = {}, 0
    for r, d in zip(reviewed, draft):
        key = r["key"]
        if key not in en["strings"]:
            found.append(f"{key}: not a current key")
            continue
        if r["English"] != en["strings"][key]:
            found.append(f"{key}: English differs from the current string")
        if r["English"] != d["English"] or r["Telugu draft"] != d["Telugu draft"]:
            found.append(f"{key}: English or draft changed from te_review.csv")
        final = r["correction"] if r["correction"].strip() else r["Telugu draft"]
        kept_zwnj += final.count(ZWNJ)
        found += problems(key, r["English"], final)
        strings[key] = final
    ui = {}
    for r in new:
        key, kind = r["key"], r["kind"]
        table = en["strings"] if kind == "vocab" else en["ui"]
        if key not in table:
            found.append(f"{key}: not a current {kind} key")
            continue
        if r["English"] != table[key]:
            found.append(f"{key}: English differs from the current string")
        found += problems(key, r["English"], r["Telugu"])
        (strings if kind == "vocab" else ui)[key] = r["Telugu"]
    changed = 0
    for r in read_csv(changes_path) if changes_path else []:
        key, kind = r["key"], r["kind"]
        table = en["strings"] if kind == "vocab" else en["ui"]
        if key not in table or r["English"] != table[key]:
            found.append(f"{key}: not a current {kind} key, or its English changed")
            continue
        found += problems(key, r["English"], r["after"])
        (strings if kind == "vocab" else ui)[key] = r["after"]
        changed += 1
    missing_ui = sorted(set(en["ui"]) - set(ui))
    missing_vocab = sorted(k for k in set(en["strings"]) - set(strings) if not PUNCTUATION.match(en["strings"][k])
                           or re.search(r"[A-Za-z]", PLACEHOLDER.sub("", en["strings"][k])))
    found += [f"{k}: no Telugu" for k in missing_ui + missing_vocab]
    if found:
        print(f"{len(found)} problems; nothing written:")
        for p in found:
            print("  " + p)
        return 1
    pack = {
        "_status": "reviewed: Amazon Translate draft corrected by a reviewer, checked by an independent back-translation",
        "_source": "scripts/build_telugu.py --apply (te_review_reviewed.csv, te_results_screen.csv"
                   + (", te_native_review.csv)" if changes_path else ")"),
        "language": "te", "strings": dict(sorted(strings.items())), "ui": dict(sorted(ui.items())),
    }
    TE_JSON.write_text(json.dumps(pack, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    written = sum(v.count(ZWNJ) for v in strings.values())
    print(f"te.json: {len(strings)} message strings ({len(reviewed)} reviewed, {len(strings) - len(reviewed)} new), "
          f"{len(ui)} screen strings, {changed} later changes; U+200C kept {written} of {kept_zwnj + sum(r['Telugu'].count(ZWNJ) for r in new if r['kind'] == 'vocab')}")
    return build()


def pack_for_web():
    """te.json with the punctuation-only keys filled from English, as te.js ships it."""
    pack = json.loads(TE_JSON.read_text(encoding="utf-8"))
    strings = {**{k: v for k, v in ENGLISH.items() if k not in pack["strings"]}, **pack["strings"]}
    return {"language": pack["language"], "strings": dict(sorted(strings.items())), "ui": pack["ui"]}


def te_js():
    return ("// Built by scripts/build_telugu.py from web/i18n/te.json; edit that file, then rebuild.\n"
            "export default " + json.dumps(pack_for_web(), ensure_ascii=False, indent=1) + ";\n")


def build():
    TE_JS.write_text(te_js(), encoding="utf-8")
    print(f"te.js written: {len(pack_for_web()['strings'])} message strings, {len(pack_for_web()['ui'])} screen strings")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--apply", metavar="REVIEWED_CSV")
    p.add_argument("--draft")
    p.add_argument("--new")
    p.add_argument("--changes")
    args = p.parse_args(argv)
    if args.apply:
        return apply(args.apply, args.draft, args.new, args.changes)
    return build()


if __name__ == "__main__":
    sys.exit(main())
