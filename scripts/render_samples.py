"""Render the synthetic sample quotes to page JPEGs for the "Try a sample" route.

    python scripts/render_samples.py --out .build/samples

Writes <out>/<id>/page-NN.jpg and <out>/<id>/manifest.json ({"pages": n}), the
layout POST /samples/{id} reads from s3://<bucket>/samples/. Needs PyMuPDF.
"""

import argparse
import json
import runpy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from extract.render import render_document  # noqa: E402


def render_samples(out_dir):
    generate = runpy.run_path(str(ROOT / "samples" / "generate.py"))
    written = {}
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
            written[sid] = len(result.pages)
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
