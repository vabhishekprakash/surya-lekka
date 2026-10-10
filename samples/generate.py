"""Render the synthetic sample quotes in samples/expected/*.json as PDFs.

    python samples/generate.py [--out DIR]
    python samples/generate.py --saved-readings

Each page carries a small footer, "Made-up sample quote for testing", at the
bottom, away from the header (a large watermark was once read as the vendor's
name), and a real text layer. The vendor, address, phone and figures are made up. Needs
PyMuPDF (see requirements-redact.txt). The PDFs are not committed.
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
FOOTER = "Made-up sample quote for testing"


def render(sample, out_path):
    import pymupdf

    doc = pymupdf.open()
    for number in sorted(sample["pages"], key=int):
        page = doc.new_page(width=595, height=842)
        y = 80
        for line in sample["pages"][number]:
            page.insert_text((50, y), line, fontsize=11, fontname="helv")
            y += 22
        page.insert_text((50, 815), f"{FOOTER} | page {number}", fontsize=8, fontname="helv",
                         color=(0.45, 0.45, 0.45))
    doc.set_metadata({"title": f"Synthetic sample {sample['sample_id']}", "author": "", "producer": ""})
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()


def load_samples(directory=HERE / "expected"):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(directory).glob("S*.json"))]


def saved_reading(sample):
    """The saved reading the "Try a sample" button shows: the sample's expected
    quote and page text, not a model's output."""
    sid = sample["sample_id"]
    return {
        "_reading": (f"Saved reading for synthetic sample {sid}, made from samples/expected/{sid}.json. "
                     "It was not read by a model. Every value is made up."),
        "sample_id": sid,
        "reading": "saved",
        "quote": sample["quote"],
        "pages": sample["pages"],
    }


def write_saved_readings(directory=HERE / "cached"):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for sample in load_samples():
        path = directory / f"{sample['sample_id']}.json"
        path.write_text(json.dumps(saved_reading(sample), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


# Hidden tester samples, opened only by a direct link (#sample=S4). Each is a listed sample with
# one saved value deliberately read wrong; the value keeps its true source line.
HIDDEN = {
    "S4": {"from": "S2", "note": "panel wattage saved as 550 W where the page prints 500 W",
           "group": 0, "key": "wattage", "value": {"raw": "550 W", "parsed": "550", "unit": "W", "parse_status": "ok"}},
}


def hidden_reading(sid):
    spec = HIDDEN[sid]
    base = json.loads((HERE / "cached" / f"{spec['from']}.json").read_text(encoding="utf-8"))
    base["sample_id"] = sid
    base["_reading"] = (f"Hidden tester sample {sid}: the saved reading of synthetic sample {spec['from']} with one "
                        f"value deliberately wrong ({spec['note']}). Every value is made up.")
    field = base["quote"]["module_groups"][spec["group"]][spec["key"]]
    field["value"] = dict(spec["value"])
    for candidate in field.get("candidates") or []:
        candidate["value"] = dict(spec["value"])
    return base


def write_hidden_readings(directory=HERE / "cached"):
    paths = []
    for sid in HIDDEN:
        path = Path(directory) / f"{sid}.json"
        path.write_text(json.dumps(hidden_reading(sid), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE))
    ap.add_argument("--saved-readings", action="store_true",
                    help="write samples/cached/<id>.json instead of the PDFs")
    args = ap.parse_args(argv)
    if args.saved_readings:
        for path in write_saved_readings() + write_hidden_readings():
            print(f"wrote {path}")
        return
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for sample in load_samples():
        path = out / f"{sample['sample_id']}.pdf"
        render(sample, path)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
