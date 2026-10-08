"""Extraction spike: run Nova models on named documents and score them per field
against a hand-written answer key.

    python -m src.extract.spike --files Q12,Q16 \\
        --models global.amazon.nova-2-lite-v1:0,apac.amazon.nova-pro-v1:0 \\
        --region ap-south-1 --in H:\\solar-data\\redacted --out H:\\solar-data\\spike_out \\
        --answer-key H:\\solar-data\\answer_key_spike.txt

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
    p.add_argument("--files", required=True, help="comma-separated document ids, e.g. Q12,Q16")
    p.add_argument("--models", required=True, help="comma-separated inference profile ids")
    p.add_argument("--region", default="ap-south-1")
    p.add_argument("--in", dest="in_dir", required=True, type=Path)
    p.add_argument("--out", dest="out_dir", required=True, type=Path)
    p.add_argument("--answer-key", type=Path)
    p.add_argument("--dry-run", action="store_true", help="stubbed replies, no AWS calls")
    p.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    p.add_argument("--max-pages", type=int, default=MAX_PAGES)
    p.add_argument("--retries", type=int, default=0, help="botocore retries per call (default 0)")
    p.add_argument("--request-limit", type=int, default=DEFAULT_REQUEST_LIMIT_BYTES,
                   help="largest serialised request in bytes")
    return p.parse_args(argv)


def run(args):
    docs = [d.strip() for d in args.files.split(",") if d.strip()]
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    out_root = args.out_dir.resolve()
    if out_root == REPO or REPO in out_root.parents:
        raise RunStopped("--out must be outside the repository.")
    if not args.in_dir.is_dir():
        raise RunStopped("--in is not a folder.")
    paths = {d: find_document(args.in_dir, d) for d in docs}

    key = {"docs": {}, "unknown": {}, "orphan_lines": 0, "duplicates": {}}
    if args.answer_key:
        if not args.answer_key.is_file():
            raise RunStopped("--answer-key file not found.")
        key = scoring.parse_answer_key(args.answer_key.read_text(encoding="utf-8-sig"))
        print(f"Answer key: {len(key['docs'])} document blocks")
        for d in docs:
            print(f"  {d}: {len(key['docs'].get(d, {}))} fields")
        for d, keys in key["unknown"].items():
            print(f"  {d}: keys not recognised, not scored: {', '.join(keys)}")
        for d, keys in key["duplicates"].items():
            print(f"  {d}: keys given twice, last one used: {', '.join(keys)}")
        if key["orphan_lines"]:
            print(f"  {key['orphan_lines']} key lines before the first document header were ignored")

    if args.dry_run:
        print("Dry run: stubbed replies, no AWS calls.")
    else:
        preflight(args.region, models)

    run_dir = out_root / datetime.now().strftime("%Y%m%d-%H%M%S")
    rendered = {}
    for d in docs:
        pages, skipped = render_document(paths[d], dpi=args.dpi, max_pages=args.max_pages)
        rendered[d] = (pages, skipped)
        largest = max((len(p.jpeg) for p in pages), default=0)
        print(f"{d}: {len(pages)} pages rendered, {len(skipped)} skipped, largest image {largest // 1024} KB")
    _write(run_dir / "run.json", {"files": docs, "models": models, "region": args.region,
                                  "dry_run": args.dry_run, "dpi": args.dpi, "max_pages": args.max_pages,
                                  "retries": args.retries, "request_limit": args.request_limit,
                                  "pages": {d: [p.page for p in rendered[d][0]] for d in docs},
                                  "skipped": {d: rendered[d][1] for d in docs}})

    results = {}
    try:
        for model in models:
            client = DryRunClient(args.region) if args.dry_run else make_client(args.region, args.retries)
            results[model] = {}
            for d in docs:
                pages, skipped = rendered[d]
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


def main(argv=None):
    args = parse_args(argv)
    try:
        return run(args)
    except RunStopped as e:
        print(f"Stopped: {e}", file=sys.stderr)
        return STOPPED


if __name__ == "__main__":
    sys.exit(main())
