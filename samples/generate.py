"""Render the synthetic sample quotes in samples/expected/*.json as PDFs.

    python samples/generate.py [--out DIR]

Each page carries a "SAMPLE - NOT A REAL QUOTATION" watermark and a real
text layer. The vendor, address, phone and figures are made up. Needs
PyMuPDF (see requirements-redact.txt). The PDFs are not committed.
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
WATERMARK = "SAMPLE - NOT A REAL QUOTATION"


def render(sample, out_path):
    import pymupdf

    doc = pymupdf.open()
    for number in sorted(sample["pages"], key=int):
        page = doc.new_page(width=595, height=842)
        centre = pymupdf.Point(70, 600)
        page.insert_text(centre, WATERMARK, fontsize=38, fontname="helv", color=(0.82, 0.82, 0.82),
                         morph=(centre, pymupdf.Matrix(-35)))
        page.insert_text((50, 40), WATERMARK, fontsize=9, fontname="helv", color=(0.6, 0, 0))
        y = 80
        for line in sample["pages"][number]:
            page.insert_text((50, y), line, fontsize=11, fontname="helv")
            y += 22
        page.insert_text((50, 815), f"{WATERMARK} | page {number}", fontsize=8, fontname="helv",
                         color=(0.6, 0, 0))
    doc.set_metadata({"title": f"Synthetic sample {sample['sample_id']}", "author": "", "producer": ""})
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()


def load_samples(directory=HERE / "expected"):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(directory).glob("S*.json"))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for sample in load_samples():
        path = out / f"{sample['sample_id']}.pdf"
        render(sample, path)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
