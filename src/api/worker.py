"""Worker: runs when uploads/{job_id}/manifest.json lands in the bucket.

It claims the job with conditional updates (awaiting_upload -> uploaded ->
processing), so a duplicate or racing S3 event finds nothing to do. It then
extracts the pages with Nova in batches, merges them, runs the initial checks,
saves the extraction and findings, and deletes the uploaded objects. Any error
sets the job to failed with a fixed reason code; no exception text is stored
or logged.
"""

import json
import os
import re
import time
from functools import lru_cache
from urllib.parse import unquote_plus

from checks import run_checks
from extract.batching import plan_batches
from extract.merge import merge_batches
from extract.nova_client import ExtractionFailure, build_request, extract_batch, make_client, request_size
from extract.render import MAX_IMAGE_BYTES, PageImage

from .common import (
    MANIFEST_MAX_BYTES,
    error_code,
    job_ttl_seconds,
    log,
    manifest_key,
    now,
    page_key,
    s3,
    table,
    transition,
)

MANIFEST = re.compile(r"uploads/([0-9a-f-]{36})/manifest\.json")
DEFAULT_MODEL = "global.amazon.nova-2-lite-v1:0"
RESULT_MAX_BYTES = 350_000
# Safe reason codes for an error that stops the job.
REASONS = {
    "AccessDeniedException": "model_access_denied",
    "ThrottlingException": "model_busy",
    "ServiceQuotaExceededException": "model_busy",
    "ValidationException": "model_rejected_request",
}


class JobFailed(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def model_id():
    return os.environ.get("MODEL_ID", DEFAULT_MODEL)


@lru_cache(maxsize=None)
def bedrock_client():
    region = os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION")
    return make_client(region, retries=0)


def handler(event, context):
    outcomes = []
    for record in event.get("Records") or []:
        key = unquote_plus(record.get("s3", {}).get("object", {}).get("key", ""))
        match = MANIFEST.fullmatch(key)
        if not match:
            log("ignored_object")
            continue
        outcomes.append(process_job(record["s3"]["bucket"]["name"], match.group(1)))
    return {"outcomes": outcomes}


def process_job(bucket, job_id):
    started = time.perf_counter()
    item = table().get_item(Key={"job_id": job_id}).get("Item")
    if item is None:
        log("unknown_job", job_id=job_id)
        return "unknown"
    transition(job_id, "awaiting_upload", "uploaded")
    if not transition(job_id, "uploaded", "processing", claimed_at=now()):
        log("duplicate_event", job_id=job_id, status=item["status"])
        return "duplicate"
    log("job_claimed", job_id=job_id, status="processing")
    page_count = int(item["page_count"])
    try:
        pages = _load_pages(bucket, job_id, page_count)
        quote, stats = _extract(pages)
        _save(job_id, quote, {**stats, "pages": page_count, "seconds": round(time.perf_counter() - started, 3)})
    except JobFailed as e:
        return _fail(job_id, e.reason, started)
    except Exception:  # never log or store the exception text: it could quote the document
        return _fail(job_id, "internal_error", started)
    _delete_uploads(bucket, job_id, page_count)
    log("job_done", job_id=job_id, status="done", **stats, pages=page_count,
        seconds=round(time.perf_counter() - started, 3))
    return "done"


def _read(bucket, key, limit):
    from botocore.exceptions import ClientError

    try:
        body = s3().get_object(Bucket=bucket, Key=key)["Body"].read(limit + 1)
    except ClientError as e:
        raise JobFailed("missing_upload" if error_code(e) in ("NoSuchKey", "404") else "storage_error") from None
    if len(body) > limit:
        raise JobFailed("upload_too_large")
    return body


def _load_pages(bucket, job_id, page_count):
    try:
        manifest = json.loads(_read(bucket, manifest_key(job_id), MANIFEST_MAX_BYTES))
    except ValueError:
        raise JobFailed("bad_manifest") from None
    if not isinstance(manifest, dict) or manifest.get("pages") != page_count:
        raise JobFailed("bad_manifest")
    pages = []
    for n in range(1, page_count + 1):
        data = _read(bucket, page_key(job_id, n), MAX_IMAGE_BYTES)
        if not data.startswith(b"\xff\xd8\xff"):
            raise JobFailed("not_a_jpeg")
        pages.append(PageImage(n, data, 0, 0, 0, 0))
    return pages


def _extract(pages):
    model, client = model_id(), bedrock_client()

    def size_of(batch):
        return request_size(build_request(model, batch))

    batches, rejected = plan_batches(pages, size_of)
    records, failures = [], []
    for n, batch in enumerate(batches, 1):
        try:
            records.append(extract_batch(client, model, batch, n))
        except ExtractionFailure as f:
            if f.stops_run:
                raise JobFailed(REASONS.get(f.code, "model_unavailable")) from None
            failures.append({"batch": n, "pages": [p.page for p in batch], "kind": f.kind, "code": f.code})
            log("batch_failed", reason=f.kind, batches=n)
    if not records:
        raise JobFailed("extraction_failed")
    usage = [r.get("usage") or {} for r in records]
    stats = {"batches": len(batches), "failed_batches": len(failures),
             "input_tokens": sum(u.get("inputTokens", 0) for u in usage),
             "output_tokens": sum(u.get("outputTokens", 0) for u in usage)}
    return merge_batches(records, failures, rejected), stats


def _save(job_id, quote, stats):
    result = run_checks(quote)
    extraction = json.dumps(quote, default=str)
    checked = json.dumps({k: result[k] for k in ("findings", "questions", "vendor_message")}, default=str)
    if len(extraction) + len(checked) > RESULT_MAX_BYTES:
        raise JobFailed("result_too_large")
    if not transition(job_id, "processing", "done", extraction=extraction, result=checked,
                      processing_complete=bool(quote["processing_complete"]), stats=json.dumps(stats),
                      finished_at=now(), expires_at=now() + job_ttl_seconds()):
        raise JobFailed("state_changed")


def _fail(job_id, reason, started):
    transition(job_id, "processing", "failed", reason=reason, finished_at=now(),
               expires_at=now() + job_ttl_seconds())
    log("job_failed", job_id=job_id, status="failed", reason=reason,
        seconds=round(time.perf_counter() - started, 3))
    return "failed"


def _delete_uploads(bucket, job_id, page_count):
    keys = [page_key(job_id, n) for n in range(1, page_count + 1)] + [manifest_key(job_id)]
    s3().delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True})
