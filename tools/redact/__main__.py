"""Local redaction review tool.

    python -m tools.redact --root H:\\solar-data serve     review UI on 127.0.0.1
    python -m tools.redact --root H:\\solar-data detect    precompute proposed boxes
    python -m tools.redact --root H:\\solar-data status    review progress
    python -m tools.redact --root H:\\solar-data verify    re-check exported files

The root holds originals, redacted, review and pii_terms and must be outside
the repository. Output prints doc ids and counts only, never document text.
"""

import argparse
import json
import webbrowser

from . import export as exporter
from . import state as review_state
from .config import ORDER, ConfigError, Paths
from .server import HOST, doc_summary, make_server


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.redact")
    ap.add_argument("--root", required=True,
                    help="data root holding originals, redacted, review, pii_terms")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8770)
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--no-ocr", action="store_true")
    d = sub.add_parser("detect")
    d.add_argument("ids", nargs="*")
    d.add_argument("--no-ocr", action="store_true")
    sub.add_parser("status")
    sub.add_parser("verify")
    args = ap.parse_args(argv)
    try:
        paths = Paths.from_root(args.root)
    except ConfigError as e:
        ap.error(str(e))

    if args.cmd == "serve":
        srv = make_server(paths, args.port, use_ocr=not args.no_ocr)
        url = f"http://{HOST}:{srv.server_address[1]}/"
        print(f"Review UI at {url}  (Ctrl+C to stop)")
        if not args.no_browser:
            webbrowser.open(url)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.server_close()
    elif args.cmd == "detect":
        for doc_id in args.ids or ORDER:
            if doc_id not in ORDER:
                print(f"{doc_id}: not in the review list")
                continue
            if paths.original(doc_id) is None:
                print(f"{doc_id}: original not found")
                continue
            st = review_state.get_or_build(paths, doc_id, use_ocr=not args.no_ocr)
            n = sum(len(p["boxes"]) for p in st["pages"])
            flagged = sum(bool(p["warnings"] or p["hints"]) for p in st["pages"])
            note = "" if st["denylist_found"] else "  (no denylist)"
            print(f"{doc_id}: {len(st['pages'])} pages, {n} proposed boxes, "
                  f"{flagged} pages flagged{note}")
    elif args.cmd == "status":
        for doc_id in ORDER:
            s = doc_summary(paths, doc_id)
            pages = s["pages"] if s["pages"] is not None else "?"
            flags = [f for f, on in (("exported", s["exported"]), ("held-out", s["held_out"]),
                                      ("no denylist", not s["denylist_found"]),
                                      ("missing", not s["original_found"])) if on]
            print(f"{doc_id}: {s['approved']}/{pages} approved  {' '.join(flags)}")
    elif args.cmd == "verify":
        try:
            entries = json.loads(paths.manifest.read_text(encoding="utf-8"))["documents"]
        except FileNotFoundError:
            entries = []
        if not entries:
            print("no exported documents in the manifest")
        for e in entries:
            problems = exporter.verify_exported(paths, e["doc_id"], e)
            print(f"{e['doc_id']}: " + ("automated checks passed" if not problems
                                        else "FAILED: " + "; ".join(problems)))
        if entries:
            print("Automated checks do not prove redaction is complete. Reopen each file and look.")


if __name__ == "__main__":
    main()
