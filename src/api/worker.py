"""Worker: runs when uploads/{job_id}/manifest.json lands in the bucket.

It claims the job with conditional updates (awaiting_upload -> uploaded ->
processing), so a duplicate or racing S3 event finds nothing to do. It then
extracts the pages with Nova in batches, saving each batch's reading on the
job as soon as it arrives, merges them, runs the initial checks, saves the
extraction and findings, and deletes the uploaded objects.

A model call starts only when there is time left for it to time out and for
the job to be saved; otherwise the job stops as failed with reason timed_out.
A retry (POST /jobs/{id}/retry, or Lambda's own retry after a claim has gone
stale) reuses the saved batches, so only the rest are sent to the model.

Any error sets the job to failed with a fixed reason code; no exception text
is stored or logged.
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
    MAX_RETRIES,
    RETRYABLE_REASONS,
    claim,
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
BATCH_SAVE_MAX_BYTES = 100_000
READ_TIMEOUT_SECONDS = 120
CONNECT_TIMEOUT_SECONDS = 10
SAVE_RESERVE_SECONDS = 30
# A call starts only with time left for it to time out and for the job to be saved.
CALL_BUDGET_MS = (READ_TIMEOUT_SECONDS + CONNECT_TIMEOUT_SECONDS + SAVE_RESERVE_SECONDS) * 1000
SAVED_KEYS = ("batch", "pages", "model_id", "contract", "usage")
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


class OutOfTime(Exception):
    pass


def model_id():
    return os.environ.get("MODEL_ID", DEFAULT_MODEL)


@lru_cache(maxsize=None)
def bedrock_client():
    region = os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION")
    return make_client(region, retries=0, read_timeout=READ_TIMEOUT_SECONDS)


def handler(event, context):
    outcomes = []
    for record in event.get("Records") or []:
        key = unquote_plus(record.get("s3", {}).get("object", {}).get("key", ""))
        match = MANIFEST.fullmatch(key)
        if not match:
            log("ignored_object")
            continue
        outcomes.append(process_job(record["s3"]["bucket"]["name"], match.group(1), context))
    return {"outcomes": outcomes}


def process_job(bucket, job_id, context=None):
    started = time.perf_counter()
    item = table().get_item(Key={"job_id": job_id}).get("Item")
    if item is None:
        log("unknown_job", job_id=job_id)
        return "unknown"
    transition(job_id, "awaiting_upload", "uploaded")
    if not claim(job_id):
        log("duplicate_event", job_id=job_id, status=item["status"])
        return "duplicate"
    item = table().get_item(Key={"job_id": job_id}, ConsistentRead=True)["Item"]
    log("job_claimed", job_id=job_id, status="processing")
    page_count = int(item["page_count"])
    retries_left = int(item.get("retries", 0)) < MAX_RETRIES
    try:
        pages = _load_pages(bucket, job_id, page_count)
        quote, stats = _extract(job_id, pages, item.get("batches") or {}, context)
        _save(job_id, quote, {**stats, "pages": page_count, "seconds": round(time.perf_counter() - started, 3)})
    except OutOfTime:
        _fail(bucket, job_id, page_count, "timed_out", retries_left, started)
        return "interrupted"
    except JobFailed as e:
        return _fail(bucket, job_id, page_count, e.reason, retries_left, started)
    except Exception:  # never log or store the exception text: it could quote the document
        return _fail(bucket, job_id, page_count, "internal_error", retries_left, started)
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


def _time_left_ms(context):
    """Remaining Lambda time, or None when there is no limit (local runs)."""
    remaining = getattr(context, "get_remaining_time_in_millis", None)
    return remaining() if remaining else None


def _saved_record(text, batch, pages, model):
    """A batch saved by an earlier run, if it covers the same pages with the same model."""
    try:
        record = json.loads(text) if text else None
    except ValueError:
        return None
    if not isinstance(record, dict) or (record.get("batch"), record.get("pages"), record.get("model_id")) != (
            batch, pages, model):
        return None
    return record


def _save_batch(job_id, record):
    """Keep one batch's reading on the job so a retry need not pay for it again.
    A batch that can't be saved only costs a repeat call on retry."""
    from botocore.exceptions import ClientError

    text = json.dumps(record, default=str)
    if len(text) > BATCH_SAVE_MAX_BYTES:
        log("batch_not_saved", job_id=job_id, batches=record["batch"], reason="too_large")
        return
    try:
        table().update_item(
            Key={"job_id": job_id}, UpdateExpression="SET #batches.#n = :record",
            ConditionExpression="#status = :processing",
            ExpressionAttributeNames={"#batches": "batches", "#n": str(record["batch"]), "#status": "status"},
            ExpressionAttributeValues={":record": text, ":processing": "processing"})
    except ClientError as e:
        if error_code(e) == "ConditionalCheckFailedException":
            raise JobFailed("state_changed") from None
        log("batch_not_saved", job_id=job_id, batches=record["batch"], reason=error_code(e) or "client_error")


def _extract(job_id, pages, saved, context):
    model, client = model_id(), bedrock_client()

    def size_of(batch):
        return request_size(build_request(model, batch))

    batches, rejected = plan_batches(pages, size_of)
    records, failures, resumed = [], [], 0
    for n, batch in enumerate(batches, 1):
        numbers = [p.page for p in batch]
        record = _saved_record(saved.get(str(n)), n, numbers, model)
        if record is not None:
            records.append(record)
            resumed += 1
            continue
        left = _time_left_ms(context)
        if left is not None and left < CALL_BUDGET_MS:
            log("out_of_time", job_id=job_id, batches=n)
            raise OutOfTime()
        try:
            result = extract_batch(client, model, batch, n)
        except ExtractionFailure as f:
            if f.stops_run:
                raise JobFailed(REASONS.get(f.code, "model_unavailable")) from None
            failures.append({"batch": n, "pages": numbers, "kind": f.kind, "code": f.code})
            log("batch_failed", reason=f.kind, batches=n)
            continue
        record = {k: result.get(k) for k in SAVED_KEYS}
        record["model_id"] = model
        records.append(record)
        _save_batch(job_id, record)
    if not records:
        raise JobFailed("extraction_failed")
    usage = [r.get("usage") or {} for r in records]
    stats = {"batches": len(batches), "failed_batches": len(failures), "resumed_batches": resumed,
             "input_tokens": sum(u.get("inputTokens", 0) for u in usage),
             "output_tokens": sum(u.get("outputTokens", 0) for u in usage)}
    return merge_batches(records, failures, rejected), stats


def _save(job_id, quote, stats):
    result = run_checks(quote)
    extraction = json.dumps(quote, default=str)
    checked = json.dumps({k: result[k] for k in ("findings", "questions", "vendor_message")}, default=str)
    if len(extraction) + len(checked) > RESULT_MAX_BYTES:
        raise JobFailed("result_too_large")
    if not transition(job_id, "processing", "done", remove=("batches", "reason"), extraction=extraction,
                      result=checked, processing_complete=bool(quote["processing_complete"]),
                      stats=json.dumps(stats), finished_at=now(), expires_at=now() + job_ttl_seconds()):
        raise JobFailed("state_changed")


def _fail(bucket, job_id, page_count, reason, retries_left, started):
    final = reason not in RETRYABLE_REASONS or not retries_left
    transition(job_id, "processing", "failed", remove=("batches",) if final else (), reason=reason,
               finished_at=now(), expires_at=now() + job_ttl_seconds())
    log("job_failed", job_id=job_id, status="failed", reason=reason,
        seconds=round(time.perf_counter() - started, 3))
    if final:  # no retry will read the pages or the saved batches again
        _delete_uploads(bucket, job_id, page_count)
    return "failed"


def _delete_uploads(bucket, job_id, page_count):
    keys = [page_key(job_id, n) for n in range(1, page_count + 1)] + [manifest_key(job_id)]
    s3().delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True})
