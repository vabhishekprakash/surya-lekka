"""Render the synthetic sample quotes to page JPEGs for the "Try a sample" route.

    python scripts/render_samples.py --out .build/samples

Writes <out>/<id>/page-NN.jpg, <out>/<id>/manifest.json ({"pages": n}) and
<out>/<id>/reading.json (the saved reading from samples/cached/), the layout
POST /samples/{id} reads from s3://<bucket>/samples/. Needs PyMuPDF.
"""

import argparse
import json
import runpy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from api.boxes import _evidence  # noqa: E402
from extract.render import render_document  # noqa: E402


def with_boxes(reading, pdf):
    """The saved reading with each piece of evidence's boxes on its page, found in the sample
    PDF's text layer. Only page numbers and coordinates are added; no value changes."""
    import pymupdf

    reading = json.loads(json.dumps(reading))
    with pymupdf.open(pdf) as doc:
        for f in _evidence(reading["quote"]):
            if not 1 <= f["page"] <= len(doc):
                continue
            page = doc[f["page"] - 1]
            width, height = page.rect.width, page.rect.height
            found = []
            for part in str(f["evidence_text"]).split("\n"):
                for r in page.search_for(part.strip()) if part.strip() else []:
                    found.append({"page": f["page"], "box": [round(r.x0 / width, 4), round(r.y0 / height, 4),
                                                            round(r.width / width, 4), round(r.height / height, 4)]})
            if found:
                f["boxes"] = found
    return reading


def render_samples(out_dir):
    generate = runpy.run_path(str(ROOT / "samples" / "generate.py"))
    written, pdfs = {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        for sample in generate["load_samples"]():
            sid = sample["sample_id"]
            pdf = Path(tmp) / f"{sid}.pdf"
            generate["render"](sample, pdf)
            result = render_document(pdf)
            if result.skipped:
                raise SystemExit(f"{sid}: pages {result.skipped} could not be rendered")
            folder = Path(out_dir) / sid
            folder.mkdir(parents=True, exist_ok=True)
            for page in result.pages:
                (folder / f"page-{page.page:02d}.jpg").write_bytes(page.jpeg)
            (folder / "manifest.json").write_text(json.dumps({"pages": len(result.pages)}), encoding="utf-8")
            saved = json.loads((ROOT / "samples" / "cached" / f"{sid}.json").read_text(encoding="utf-8"))
            (folder / "reading.json").write_text(json.dumps(with_boxes(saved, pdf), ensure_ascii=False),
                                                 encoding="utf-8")
            pdfs[sid] = pdf
            written[sid] = len(result.pages)
        for sid, spec in generate["HIDDEN"].items():  # a hidden sample shows its listed sample's pages
            source, folder = Path(out_dir) / spec["from"], Path(out_dir) / sid
            folder.mkdir(parents=True, exist_ok=True)
            for page in sorted(source.glob("page-*.jpg")):
                (folder / page.name).write_bytes(page.read_bytes())
            (folder / "manifest.json").write_bytes((source / "manifest.json").read_bytes())
            saved = json.loads((ROOT / "samples" / "cached" / f"{sid}.json").read_text(encoding="utf-8"))
            (folder / "reading.json").write_text(json.dumps(with_boxes(saved, pdfs[spec["from"]]), ensure_ascii=False),
                                                 encoding="utf-8")
            written[sid] = written[spec["from"]]
    return written


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, default=ROOT / ".build" / "samples")
    args = p.parse_args(argv)
    for sid, pages in render_samples(args.out).items():
        print(f"{sid}: {pages} pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
