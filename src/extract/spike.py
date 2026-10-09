"""Extraction spike: run Nova models, or Amazon Textract, on named documents and
score them per field against hand-written answer keys.

    python -m src.extract.spike --files Q12,Q16 \\
        --models global.amazon.nova-2-lite-v1:0,apac.amazon.nova-pro-v1:0 \\
        --region ap-south-1 --in H:\\solar-data\\redacted --out H:\\solar-data\\spike_out \\
        --answer-key H:\\solar-data\\answer_key_spike.txt

    python -m src.extract.spike --engine textract --files Q01,Q12 --in ... --out ... \\
        --answer-key labels_dev.txt --answer-key answer_key_spike.txt

With --engine textract each page is one AnalyzeDocument call. The run prints the
pages and the estimated cost first, refuses to go past --max-usd, prints the
applied request-rate quota from Service Quotas when readable, and saves raw
replies only under a textract folder of --out. The held-out documents (Q10,
Q11, Q13, Q14) are refused unless --final-heldout is given; a live held-out run
writes a marker in --out and no second one is allowed. Dry runs never count.

Before any model call it checks the AWS identity and lists the inference
profiles. Models run one after another. Raw responses, timings and token
counts are saved under --out; the console shows counts and scores only, never
document text. AccessDenied, ValidationException and quota errors stop the
whole run. --dry-run answers every call with a synthetic stubbed reply.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import argparse
import json
import re
from datetime import datetime

from checks import run_checks
from extract import scoring
from extract.batching import DEFAULT_REQUEST_LIMIT_BYTES, plan_batches
from extract.dryrun import DryRunClient
from extract.merge import merge_batches
from extract.nova_client import (ExtractionFailure, build_request, extract_batch, make_client,
                                 request_size)
from extract.render import DEFAULT_DPI, MAX_PAGES, render_document
from extract import textract_client
from extract.textract_queries import MONEY

HELDOUT = ("Q10", "Q11", "Q13", "Q14")
HELDOUT_MARKER = "HELDOUT_RUN_DONE.json"
CONFIDENCE_BUCKETS = (0, 50, 60, 70, 80, 90, 95)
# Report column -> scoring status. "conflict" is a missing field the merge marked as a conflict.
STATUS_COLUMNS = (("match", "correct"), ("wrong", "wrong"), ("missing", "missing"), ("conflict", None),
                  ("conflict_flagged", "conflict_flagged"), ("false+", "falsely_populated"),
                  ("abstained", "abstained"))

REPO = SRC.parent
SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png")
STOPPED = 2
HINTS = {
    "AccessDeniedException": "This account or role cannot call the model. Check Bedrock model access for the "
                             "inference profile and the IAM permission bedrock:InvokeModel.",
    "ValidationException": "Bedrock rejected the request. Check the model id and region.",
    "ThrottlingException": "A rate or quota limit was reached. Wait, or ask for a quota increase.",
    "ServiceQuotaExceededException": "A service quota was reached. Ask for a quota increase.",
}


class RunStopped(Exception):
    pass


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def find_document(in_dir, doc_id):
    pattern = re.compile(rf"{re.escape(doc_id)}(?:[^A-Za-z0-9].*)?", re.I)
    matches = [p for p in sorted(in_dir.iterdir())
               if p.is_file() and p.suffix.lower() in SUFFIXES and pattern.fullmatch(p.stem)]
    if len(matches) != 1:
        raise RunStopped(f"{doc_id}: expected one matching PDF or image in --in, found {len(matches)}.")
    return matches[0]


def preflight(region, models, sts=None, bedrock=None):
    """Check the AWS identity and that every model is an active inference profile."""
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        identity = (sts or boto3.client("sts", region_name=region)).get_caller_identity()
    except (ClientError, BotoCoreError) as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
        raise RunStopped(f"AWS identity check failed ({code}). Sign in (for example aws sso login) and "
                         "run again.") from None
    print(f"AWS identity: account {identity['Account']}, {identity['Arn']}")
    try:
        client = bedrock or boto3.client("bedrock", region_name=region)
        profiles = {}
        for page in client.get_paginator("list_inference_profiles").paginate(typeEquals="SYSTEM_DEFINED"):
            for p in page.get("inferenceProfileSummaries", []):
                profiles[p["inferenceProfileId"]] = p.get("status")
    except (ClientError, BotoCoreError) as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
        raise RunStopped(f"Listing inference profiles in {region} failed ({code}). "
                         + HINTS.get(code, "")) from None
    for model in models:
        if model not in profiles:
            raise RunStopped(f"{model} is not listed as an inference profile in {region}.")
        if profiles[model] != "ACTIVE":
            raise RunStopped(f"{model} is listed in {region} but its status is {profiles[model]}.")
    print(f"Inference profiles found in {region}: {', '.join(models)}")


def run_document(client, model, doc_id, pages, skipped, out_dir, request_limit):
    """Extract one document with one model. Saves every batch record; raises
    RunStopped after saving when a failure must stop the run."""
    def size_of(batch_pages):
        return request_size(build_request(model, batch_pages))

    batches, rejected = plan_batches(pages, size_of, request_limit)
    records, failures = [], []
    for n, batch_pages in enumerate(batches, 1):
        try:
            record = extract_batch(client, model, batch_pages, n, request_limit)
        except ExtractionFailure as failure:
            record = dict(failure.record or {"batch": n, "pages": [p.page for p in batch_pages]})
            record["failure"] = {"kind": failure.kind, "code": failure.code, "detail": failure.detail}
            _write(out_dir / f"batch-{n}.json", record)
            failures.append({"batch": n, "pages": record["pages"], "kind": failure.kind, "code": failure.code})
            if failure.stops_run:
                raise RunStopped(f"{model} on {doc_id}, batch {n}: {failure.code}. "
                                 + HINTS.get(failure.code, "")) from None
            continue
        _write(out_dir / f"batch-{n}.json", {k: v for k, v in record.items() if k != "contract"})
        records.append(record)
    quote = merge_batches(records, failures, sorted(set(skipped) | set(rejected)))
    _write(out_dir / "quote.json", quote)
    _write(out_dir / "checks.json", run_checks(quote)["findings"])
    usage = [r.get("usage") or {} for r in records]
    return {
        "quote": quote,
        "batches": len(batches),
        "failed": [{"batch": f["batch"], "kind": f["kind"], "code": f["code"]} for f in failures],
        "rejected_pages": rejected,
        "seconds": round(sum(r.get("seconds") or 0 for r in records), 3),
        "input_tokens": sum(u.get("inputTokens", 0) for u in usage),
        "output_tokens": sum(u.get("outputTokens", 0) for u in usage),
    }


def _slug(model):
    return re.sub(r"[^A-Za-z0-9.-]+", "_", model)


def _table(rows, headers):
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))] + [fmt.format(*map(str, r)) for r in rows]
    return "\n".join(line.rstrip() for line in lines)


def report(models, docs, results, key):
    lines = []
    summary_rows = []
    summaries = {}
    for model in models:
        per_doc = {d: scoring.score_document(results[model][d]["quote"], key["docs"].get(d, {}))
                   for d in docs if d in results.get(model, {})}
        s = scoring.summarise(per_doc)
        runs = results.get(model, {}).values()
        s.update({
            "batches": sum(r["batches"] for r in runs), "failed": sum(len(r["failed"]) for r in runs),
            "seconds": round(sum(r["seconds"] for r in runs), 1),
            "input_tokens": sum(r["input_tokens"] for r in runs),
            "output_tokens": sum(r["output_tokens"] for r in runs),
            "scores": per_doc,
        })
        summaries[model] = s
        pct = (lambda v: "-" if v is None else f"{v:.0%}")
        summary_rows.append([model, s["fields"], s["correct"], s["wrong"], s["missing"], s["falsely_populated"],
                             s["abstained"], pct(s["accuracy"]), pct(s["false_population_rate"]),
                             f"{s['pages_ok']}/{s['pages_checked']}", f"{s['failed']}/{s['batches']}",
                             s["seconds"], s["input_tokens"], s["output_tokens"]])
    lines.append(_table(summary_rows, ["model", "fields", "correct", "wrong", "missing", "false+", "abstained",
                                       "accuracy", "false+ rate", "pages ok", "failed batches", "seconds",
                                       "in tokens", "out tokens"]))
    side = []
    for d in docs:
        for k in key["docs"].get(d, {}):
            row = [d, k]
            for model in models:
                sc = summaries[model]["scores"].get(d, {}).get(k)
                row.append("-" if sc is None else sc["status"] + (" (conflict)" if sc["conflict"] else ""))
            side.append(row)
    lines.append("")
    lines.append(_table(side, ["doc", "field", *models]) if side else "No answer-key fields to compare.")
    return "\n".join(lines), summaries


def parse_args(argv):
    p = argparse.ArgumentParser(prog="python -m src.extract.spike", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=scoring.__doc__.split("\n\n", 1)[1]
                                + "\nFields scored: " + ", ".join(scoring.FIELDS) + ".")
    p.add_argument("--files", help="comma-separated document ids, e.g. Q12,Q16")
    p.add_argument("--engine", choices=("nova", "textract"), default="nova")
    p.add_argument("--models", help="comma-separated inference profile ids (nova only)")
    p.add_argument("--region", default="ap-south-1")
    p.add_argument("--in", dest="in_dir", type=Path)
    p.add_argument("--out", dest="out_dir", type=Path)
    p.add_argument("--answer-key", type=Path, action="append",
                   help="hand-written answer key; give it more than once to combine keys")
    p.add_argument("--final-heldout", action="store_true",
                   help="allow the held-out documents, once (a live run writes a marker in --out)")
    p.add_argument("--threshold", type=float, default=textract_client.CONFIDENCE_THRESHOLD,
                   help="textract: lowest answer confidence kept (0 to 100)")
    p.add_argument("--max-usd", type=float, default=5.0,
                   help="textract: refuse a live run estimated above this many US dollars")
    p.add_argument("--check-key", action="store_true",
                   help="only list the answer key's field names per document, then stop")
    p.add_argument("--dry-run", action="store_true", help="stubbed replies, no AWS calls")
    p.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    p.add_argument("--max-pages", type=int, default=MAX_PAGES)
    p.add_argument("--retries", type=int, default=0, help="botocore retries per call (default 0)")
    p.add_argument("--request-limit", type=int, default=DEFAULT_REQUEST_LIMIT_BYTES,
                   help="largest serialised request in bytes")
    args = p.parse_args(argv)
    needed = (["answer_key"] if args.check_key else
              ["files", "in_dir", "out_dir"] + (["models"] if args.engine == "nova" else []))
    missing = [n for n in needed if getattr(args, n) is None]
    if missing:
        p.error("missing: " + ", ".join("--" + n.replace("_dir", "").replace("_", "-") for n in missing))
    return args


_NAME_SHAPE = re.compile(r"[A-Za-z][A-Za-z _-]{0,40}")


def _safe_name(name):
    """A key name as written, or a placeholder when it does not look like a field name
    (so a stray value line is never printed)."""
    return name if _NAME_SHAPE.fullmatch(name) else "(a line that is not a field name)"


def describe_key(key, docs=None):
    """Field names per document, never values."""
    entries = [e for fields in key["docs"].values() for e in fields.values()]
    lines = [f"Answer key: {len(key['docs'])} document blocks"]
    tagged = sum(1 for e in entries if e.get("tags"))
    if tagged:
        left = sum(1 for e in entries if e["value"] and scoring._PAGE_TAG.search(e["value"]))
        lines.append(f"  page tags such as [p1] taken out of {tagged} values (their pages added); "
                     f"values still holding a tag: {left}")
    for d in docs or list(key["docs"]):
        fields = key["docs"].get(d, {})
        named = [f"{t['key']} -> {f}" if scoring.key_name(t["key"]) != f else f for f, t in fields.items()]
        lines.append(f"  {d}: {len(fields)} scored: {', '.join(named) or '-'}")
        if key["kept"].get(d):
            lines.append(f"  {d}: kept, not scored: {', '.join(key['kept'][d])}")
        if key["unknown"].get(d):
            lines.append(f"  {d}: not recognised, not scored: {', '.join(map(_safe_name, key['unknown'][d]))}")
        if key["duplicates"].get(d):
            lines.append(f"  {d}: given twice, last one used: {', '.join(key['duplicates'][d])}")
    if key["orphan_lines"]:
        lines.append(f"  {key['orphan_lines']} key lines before the first document header were ignored")
    return "\n".join(lines)


def read_key(path):
    if not path.is_file():
        raise RunStopped("--answer-key file not found.")
    return scoring.parse_answer_key(path.read_text(encoding="utf-8-sig"))


def read_keys(paths):
    """Several answer keys combined. A document may appear in only one of them."""
    combined = {"docs": {}, "kept": {}, "unknown": {}, "orphan_lines": 0, "duplicates": {}}
    for path in paths:
        key = read_key(path)
        clash = sorted(set(key["docs"]) & set(combined["docs"]))
        if clash:
            raise RunStopped(f"{', '.join(clash)} appear in more than one answer key.")
        for part in ("docs", "kept", "unknown", "duplicates"):
            combined[part].update(key[part])
        combined["orphan_lines"] += key["orphan_lines"]
    return combined


def check_heldout_names(docs, args):
    """The held-out documents are refused, before anything is read, unless --final-heldout."""
    held = [d for d in docs if d.upper() in HELDOUT]
    if held and not args.final_heldout:
        raise RunStopped(f"{', '.join(held)} are held out for the final evaluation. "
                         "Give --final-heldout to run them, once.")


def claim_heldout_run(docs, args, out_root):
    """Before a live run's first call: write the held-out marker, or refuse a second
    held-out run. A dry run never counts and never writes the marker."""
    held = [d for d in docs if d.upper() in HELDOUT]
    if not held or args.dry_run:
        return
    marker = out_root / HELDOUT_MARKER
    if marker.exists():
        raise RunStopped(f"The held-out run was already done ({marker.name} in --out). It runs once.")
    _write(marker, {"files": held, "engine": args.engine, "started": datetime.now().isoformat(timespec="seconds")})


def run(args):
    if args.check_key:
        print(describe_key(read_keys(args.answer_key)))
        return 0
    docs = [d.strip() for d in args.files.split(",") if d.strip()]
    out_root = args.out_dir.resolve()
    if out_root == REPO or REPO in out_root.parents:
        raise RunStopped("--out must be outside the repository.")
    if args.engine == "textract" and args.models:
        raise RunStopped("--models is for --engine nova only.")
    check_heldout_names(docs, args)
    if not args.in_dir.is_dir():
        raise RunStopped("--in is not a folder.")
    paths = {d: find_document(args.in_dir, d) for d in docs}

    key = {"docs": {}, "kept": {}, "unknown": {}, "orphan_lines": 0, "duplicates": {}}
    if args.answer_key:
        key = read_keys(args.answer_key)
        print(describe_key(key, docs))
    if args.engine == "textract":
        return run_textract(args, docs, paths, key, out_root)
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    if args.dry_run:
        print("Dry run: stubbed replies, no AWS calls.")
    else:
        preflight(args.region, models)
        claim_heldout_run(docs, args, out_root)

    run_dir = out_root / datetime.now().strftime("%Y%m%d-%H%M%S")
    rendered = {}
    for d in docs:
        result = render_document(paths[d], dpi=args.dpi, max_pages=args.max_pages)
        pages, skipped = result.pages, result.skipped
        rendered[d] = result
        largest = max((len(p.jpeg) for p in pages), default=0)
        print(f"{d}: {result.page_count} pages in the file, {len(pages)} rendered, {len(skipped)} skipped, "
              f"largest image {largest // 1024} KB")
        stated = ((key["kept"].get(d) or {}).get("pages_total") or {}).get("value")
        if stated and stated.strip().isdigit() and int(stated) != result.page_count:
            print(f"  warning: the answer key says {int(stated)} pages, the file has {result.page_count}")
    _write(run_dir / "run.json", {"files": docs, "models": models, "region": args.region,
                                  "dry_run": args.dry_run, "dpi": args.dpi, "max_pages": args.max_pages,
                                  "retries": args.retries, "request_limit": args.request_limit,
                                  "page_count": {d: rendered[d].page_count for d in docs},
                                  "pages": {d: [p.page for p in rendered[d].pages] for d in docs},
                                  "skipped": {d: rendered[d].skipped for d in docs}})

    results = {}
    try:
        for model in models:
            client = DryRunClient(args.region) if args.dry_run else make_client(args.region, args.retries)
            results[model] = {}
            for d in docs:
                pages, skipped = rendered[d].pages, rendered[d].skipped
                r = run_document(client, model, d, pages, skipped, run_dir / _slug(model) / d, args.request_limit)
                results[model][d] = r
                print(f"{model} {d}: {r['batches']} batches, {len(r['failed'])} failed, {r['seconds']} s, "
                      f"{r['input_tokens']} in / {r['output_tokens']} out tokens")
    finally:
        if results and key["docs"]:
            done = [m for m in models if m in results]
            text, summaries = report(done, docs, results, key)
            print()
            print(text)
            (run_dir / "summary.txt").write_text(text + "\n", encoding="utf-8")
            _write(run_dir / "scores.json", summaries)
        print(f"\nSaved under {run_dir}")
    return 0


# --- Amazon Textract ----------------------------------------------------------------------

def make_textract_client(region):
    return textract_client.make_client(region)


class DryRunTextract:
    """Answers every AnalyzeDocument call with an empty page, through a stubbed
    botocore client that checks each request against the service model."""

    def __init__(self, region="ap-south-1"):
        from extract.dryrun import stubbed_client

        self._client, self._stubber = stubbed_client("textract", region)

    def analyze_document(self, **request):
        self._stubber.add_response("analyze_document", {"DocumentMetadata": {"Pages": 1}, "Blocks": []})
        return self._client.analyze_document(**request)


def textract_preflight(region, sts=None, quotas=None):
    """The AWS identity, then the applied AnalyzeDocument rate quota if Service Quotas
    lets us read it (the default in Mumbai is understood to be 5 requests a second)."""
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        identity = (sts or boto3.client("sts", region_name=region)).get_caller_identity()
    except (ClientError, BotoCoreError) as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
        raise RunStopped(f"AWS identity check failed ({code}). Sign in and run again.") from None
    print(f"AWS identity: account {identity['Account']}, {identity['Arn']}")
    try:
        client = quotas or boto3.client("service-quotas", region_name=region)
        found = [q for page in client.get_paginator("list_service_quotas").paginate(ServiceCode="textract")
                 for q in page.get("Quotas", []) if "analyzedocument" in q.get("QuotaName", "").replace(" ", "").lower()]
    except (ClientError, BotoCoreError) as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
        print(f"Service Quotas: not readable ({code}). Calls are made one at a time with adaptive retries.")
        return
    for q in found:
        print(f"Service Quotas: {q['QuotaName']} = {q.get('Value')}")
    if not found:
        print("Service Quotas: no AnalyzeDocument quota listed. Calls are made one at a time with adaptive retries.")


def run_textract_document(engine, doc_id, pages, skipped, out_dir):
    """Read one document page by page. Saves each page's record, raw reply included,
    under out_dir; returns the merged quote and counts."""
    batches, rejected = plan_batches(pages, engine.request_size, engine.request_limit, engine.max_pages)
    records, failures, confidence, billed = [], [], [], 0
    for n, batch_pages in enumerate(batches, 1):
        try:
            record = engine.read(batch_pages, n)
        except ExtractionFailure as failure:
            record = dict(failure.record or {"batch": n, "pages": [p.page for p in batch_pages]})
            record["failure"] = {"kind": failure.kind, "code": failure.code}
            _write(out_dir / f"batch-{n}.json", record)
            failures.append({"batch": n, "pages": record["pages"], "kind": failure.kind, "code": failure.code})
            if failure.stops_run:
                raise RunStopped(f"Textract on {doc_id}, page {record['pages'][0]}: {failure.code}. "
                                 + HINTS.get(failure.code, "")) from None
            continue
        billed += ((record.get("response") or {}).get("DocumentMetadata") or {}).get("Pages", 0)
        confidence += record.get("confidence") or []
        _write(out_dir / f"batch-{n}.json", {k: v for k, v in record.items() if k != "contract"})
        records.append(record)
    quote = merge_batches(records, failures, sorted(set(skipped) | set(rejected)))
    _write(out_dir / "quote.json", quote)
    _write(out_dir / "checks.json", run_checks(quote)["findings"])
    return {"quote": quote, "pages": len(batches), "failed": failures, "billed": billed, "confidence": confidence,
            "seconds": round(sum(r.get("seconds") or 0 for r in records), 3)}


def status_counts(scores):
    """Report column -> count for one document's (or every document's) field scores."""
    counts = {column: 0 for column, _ in STATUS_COLUMNS}
    for s in scores:
        if s["status"] == "missing" and s["conflict"]:
            counts["conflict"] += 1
        else:
            counts[next(c for c, status in STATUS_COLUMNS if status == s["status"])] += 1
    return counts


def confidence_table(entries):
    """Answer counts per confidence bucket, money fields apart from the rest, with how
    many were kept and why the others were dropped. Never any answer text."""
    rows = []
    for group, chosen in (("money", [e for e in entries if e["alias"] in MONEY]),
                          ("other", [e for e in entries if e["alias"] not in MONEY])):
        for low, high in zip(CONFIDENCE_BUCKETS, CONFIDENCE_BUCKETS[1:] + (101,)):
            inside = [e for e in chosen if low <= e["confidence"] < high]
            why = {w: sum(1 for e in inside if e["why"] == w) for w in ("low_confidence", "unparsed", "not_on_page",
                                                                      "gstin")}
            rows.append([group, f"{low}-{min(high, 100)}", len(inside), sum(e["kept"] for e in inside),
                         why["low_confidence"], why["unparsed"], why["not_on_page"], why["gstin"]])
    return _table(rows, ["fields", "confidence", "answers", "kept", "below threshold", "unparsed", "not on page",
                         "gstin"])


def textract_report(docs, results, key):
    per_doc = {d: scoring.score_document(results[d]["quote"], key["docs"].get(d, {})) for d in docs if d in results}
    rows = []
    for d, scores in per_doc.items():
        rows.append([d, *status_counts(scores.values()).values()])
    rows.append(["total", *status_counts([s for scores in per_doc.values() for s in scores.values()]).values()])
    lines = [_table(rows, ["doc", *(c for c, _ in STATUS_COLUMNS)]), ""]
    side = [[d, k, s["status"] + (" (conflict)" if s["conflict"] and s["status"] == "missing" else "")]
            for d, scores in per_doc.items() for k, s in scores.items()]
    lines.append(_table(side, ["doc", "field", "status"]) if side else "No answer-key fields to compare.")
    return "\n".join(lines), {"scores": per_doc, "summary": scoring.summarise(per_doc)}


def run_textract(args, docs, paths, key, out_root):
    if out_root.name.lower() != "textract":
        out_root = out_root / "textract"  # raw Textract replies are kept only in their own folder
    rendered = {}
    for d in docs:
        result = render_document(paths[d], dpi=args.dpi, max_pages=args.max_pages)
        rendered[d] = result
        print(f"{d}: {result.page_count} pages in the file, {len(result.pages)} to read, "
              f"{len(result.skipped)} skipped")
    pages = sum(len(r.pages) for r in rendered.values())
    cost = textract_client.estimated_cost(pages)
    print(f"Textract: {pages} pages to read, estimated ${cost:.2f} at ${textract_client.PRICE_PER_PAGE_USD:.3f} "
          f"per page")
    if args.dry_run:
        print("Dry run: stubbed empty replies, no AWS calls.")
        client = DryRunTextract(args.region)
    else:
        if cost > args.max_usd:
            raise RunStopped(f"The estimate ${cost:.2f} is over --max-usd {args.max_usd:.2f}.")
        textract_preflight(args.region)
        claim_heldout_run(docs, args, out_root)
        client = make_textract_client(args.region)
    engine = textract_client.TextractEngine(client, args.threshold)
    run_dir = out_root / datetime.now().strftime("%Y%m%d-%H%M%S")
    _write(run_dir / "run.json", {"files": docs, "engine": "textract", "region": args.region,
                                  "dry_run": args.dry_run, "dpi": args.dpi, "threshold": args.threshold,
                                  "pages": {d: [p.page for p in rendered[d].pages] for d in docs},
                                  "skipped": {d: rendered[d].skipped for d in docs}})
    results = {}
    try:
        for d in docs:
            r = run_textract_document(engine, d, rendered[d].pages, rendered[d].skipped, run_dir / d)
            results[d] = r
            print(f"textract {d}: {r['pages']} pages, {len(r['failed'])} failed, {r['seconds']} s")
    finally:
        if results:
            billed = sum(r["billed"] for r in results.values())
            print(f"\nPages billed: {billed}, estimated ${textract_client.estimated_cost(billed):.2f}")
            print("\nAnswer confidence (threshold " + f"{args.threshold:g}):")
            print(confidence_table([e for r in results.values() for e in r["confidence"]]))
            if key["docs"]:
                text, summary = textract_report([d for d in docs if d in results], results, key)
                print()
                print(text)
                (run_dir / "summary.txt").write_text(text + "\n", encoding="utf-8")
                _write(run_dir / "scores.json", summary)
        print(f"\nSaved under {run_dir}")
    return 0


def main(argv=None):
    args = parse_args(argv)
    try:
        return run(args)
    except RunStopped as e:
        print(f"Stopped: {e}", file=sys.stderr)
        return STOPPED


if __name__ == "__main__":
    sys.exit(main())
