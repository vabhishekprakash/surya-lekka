"""Search each quote's own text for DCR, vendor registration and net-meter wording, and compare with the labels.

    python eval/search_details.py --manifest MANIFEST.json --redacted DIR --textract DIR [--textract DIR ...]

Text comes from the PDF text layer and from saved Textract LINE blocks (no new reading). Prints,
per quote, hit counts per group and the page numbers they are on, never the text itself.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

GROUPS = {
    "dcr": [r"\bDCR\b", r"domestic\s+content", r"domestically\s+manufactured", r"domestic\s+cell", r"non[\s-]*DCR"],
    "dcr_not": [r"\bALMM\b", r"made\s+in\s+india", r"make\s+in\s+india", r"indian\s+make"],
    "vendor_reg": [r"registration\s*no", r"\breg\s*\.?\s*no\b", r"empanel", r"vendor\s*code", r"vendor\s*id\b",
                   r"national\s+portal", r"surya\s*ghar", r"\bTGREDCO\b", r"\bTSREDCO\b", r"\bNREDCAP\b"],
    "vendor_not": [r"\bGSTIN\b", r"\bPAN\b", r"\bCIN\b", r"\budyam\b", r"\bMSME\b", r"\blicen[cs]e"],
    "net_meter": [r"net[\s-]*meter", r"net\s+metering", r"bi[\s-]*directional", r"meter\s+charges"],
}
PATTERNS = {g: [re.compile(p, re.I) for p in ps] for g, ps in GROUPS.items()}


def pages_of(doc, redacted, textract_dirs):
    """(page number, text) pairs from the text layer and saved Textract lines."""
    out = []
    pdf = Path(redacted) / f"{doc}.pdf"
    if pdf.is_file():
        import pymupdf
        with pymupdf.open(pdf) as document:
            out += [(n, page.get_text()) for n, page in enumerate(document, 1)]
    seen = set()
    for d in textract_dirs:
        for batch in sorted(Path(d).glob(f"**/{doc}/batch-*.json")):
            data = json.loads(batch.read_text(encoding="utf-8"))
            page = data["pages"][0]
            text = "\n".join(b.get("Text", "") for b in data["response"]["Blocks"] if b.get("BlockType") == "LINE")
            if (page, text) not in seen:
                seen.add((page, text))
                out.append((page, text))
    return out


def search(pages):
    hits = {g: 0 for g in GROUPS}
    where = defaultdict(set)
    for page, text in pages:
        for group, patterns in PATTERNS.items():
            n = sum(len(p.findall(text)) for p in patterns)
            if n:
                hits[group] += n
                where[group].add(page)
    return hits, where


def labelled_missing(manifest):
    """Per quote, which of the three details the labelled-data run reports as not found."""
    from extract import scoring
    import safety_harness as harness
    out = {}
    texts = {}
    for q in manifest["quotes"]:
        path = q["label_file"]
        texts.setdefault(path, scoring.parse_answer_key(Path(path).read_text(encoding="utf-8-sig"))["docs"])
        truth = texts[path][q["quote"]]
        # the same path as the published labelled-data run: scenario S-A, every operand confirmed
        result = harness.labelled(truth, harness.answers("S-A", truth))
        items = {f.get("item"): f.get("status") for f in result["findings"] if f.get("check_id") == "C4_missing_details"}
        # an item listed (missing or to confirm) means the labelled run asks about it
        out[q["quote"]] = {"dcr": "dcr_declaration" in items, "vendor_reg": "vendor_registration" in items,
                           "net_meter": "net_meter" in items, "options_gate": None in items and len(items) == 1,
                           "dcr_text_labelled": bool(str((truth.get("dcr_text") or {}).get("value") or "").strip())}
    return out


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--redacted", required=True)
    parser.add_argument("--textract", action="append", default=[])
    args = parser.parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    labels = labelled_missing(manifest)
    disagree = []
    for q in manifest["quotes"]:
        doc = q["quote"]
        pages = pages_of(doc, args.redacted, args.textract)
        hits, where = search(pages)
        print(f"{doc} ({q['set']}, {len({p for p, _ in pages})} pages with text): " + "; ".join(
            f"{g} {hits[g]}" + (f" p{','.join(map(str, sorted(where[g])))}" if where[g] else "") for g in GROUPS))
        label = labels[doc]
        for group in ("dcr", "vendor_reg", "net_meter"):
            if (hits[group] > 0) == label[group]:  # text mentions it but the label run calls it missing, or the reverse
                disagree.append((doc, group, hits[group], "missing" if label[group] else "recorded"))
    print("label run, quotes reported missing:", {g: sum(labels[d][g] for d in labels)
                                                   for g in ("dcr", "vendor_reg", "net_meter")},
          "| labels with dcr_text filled:", sum(v["dcr_text_labelled"] for v in labels.values()),
          "| stopped at the options question:", [d for d in labels if labels[d]["options_gate"]])
    for doc, group, n, label in disagree:
        print(f"  DISAGREE {doc} {group}: search hits {n}, labelled run says {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
