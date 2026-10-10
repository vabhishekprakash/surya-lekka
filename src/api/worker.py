"""Worker: runs when uploads/{job_id}/manifest.json lands in the bucket.

It claims the job with conditional updates (awaiting_upload -> uploaded ->
processing), so a duplicate or racing S3 event finds nothing to do. It then
reads the pages in batches with the reading engine (Amazon Nova, up to five
pages a call, or Amazon Textract, one page a call), saving each batch's
reading on the job as soon as it arrives, merges them, runs the initial
checks, saves the extraction and findings, and deletes the uploaded objects.
Only the mapped facts are kept: no raw reply is logged or stored.

A call starts only when there is time left for it to time out (with its
retries) and for the job to be saved; otherwise the job stops as failed with reason timed_out.
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
from extract import textract_client as textract
from extract.batching import DEFAULT_REQUEST_LIMIT_BYTES, MAX_IMAGES_PER_CALL, plan_batches
from extract.merge import merge_batches
from extract.nova_client import ExtractionFailure, build_request, extract_batch, make_client, request_size
from extract.render import MAX_IMAGE_BYTES, PageImage

from . import boxes
from .common import (
    MANIFEST_MAX_BYTES,
    MAX_RETRIES,
    RETRYABLE_REASONS,
    claim,
    conditional_update,
    ITEM_MAX_BYTES,
    error_code,
    item_size,
    job_binding,
    job_ttl_seconds,
    log,
    manifest_key,
    now,
    page_key,
    reading_engine,
    s3,
    table,
    transition,
)

MANIFEST = re.compile(r"uploads/([0-9a-f-]{36})/manifest\.json")
# Paid reads a job may make per batch, counted in its record before each one: the first run,
# Lambda's one retry and the two retries the page offers. A reading that is saved is never
# read again, so the budget only matters when saving keeps failing.
READS_PER_BATCH = 1 + 1 + MAX_RETRIES
DEFAULT_MODEL = "global.amazon.nova-2-lite-v1:0"
RESULT_MAX_BYTES = 350_000
BATCH_SAVE_MAX_BYTES = 100_000
READ_TIMEOUT_SECONDS = 120
CONNECT_TIMEOUT_SECONDS = 10
SAVE_RESERVE_SECONDS = 30
# A call starts only with time left for it to time out and for the job to be saved.
CALL_BUDGET_MS = (READ_TIMEOUT_SECONDS + CONNECT_TIMEOUT_SECONDS + SAVE_RESERVE_SECONDS) * 1000
SAVED_KEYS = ("batch", "pages", "model_id", "contract", "usage", "boxes")
DELETE_ATTEMPTS = 3
DELETE_BACKOFF_SECONDS = 0.5
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


class WorkerError(Exception):
    """Raised in place of an unexpected error outside a job's own error handling,
    so Lambda retries the event (and then sends it to the failure queue) without
    the original message, which could quote the document."""


def model_id():
    return os.environ.get("MODEL_ID") or DEFAULT_MODEL


@lru_cache(maxsize=None)
def bedrock_client():
    region = os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION")
    return make_client(region, retries=0, read_timeout=READ_TIMEOUT_SECONDS)


@lru_cache(maxsize=None)
def textract_client():
    return textract.make_client(os.environ.get("AWS_REGION"))


class NovaReader:
    """Amazon Nova through the Bedrock Converse API. MODEL_ID is an inference
    profile ID or a model ID in the stack's own Region."""

    max_pages, request_limit, call_budget_ms = MAX_IMAGES_PER_CALL, DEFAULT_REQUEST_LIMIT_BYTES, CALL_BUDGET_MS

    def __init__(self, model):
        self.model, self.client = model, bedrock_client()

    def request_size(self, batch):
        return request_size(build_request(self.model, batch))

    def read(self, batch, number):
        return extract_batch(self.client, self.model, batch, number)


class TextractReader(textract.TextractEngine):
    """Amazon Textract AnalyzeDocument in the stack's Region, one page per call,
    sent as bytes. Saved pages are marked with model_id "textract"."""

    call_budget_ms = (textract.MAX_ATTEMPTS * (textract.READ_TIMEOUT_SECONDS
                      + textract.CONNECT_TIMEOUT_SECONDS) + SAVE_RESERVE_SECONDS) * 1000

    def __init__(self, model):
        super().__init__(textract_client())


# Readers by READING_ENGINE name (common.READING_ENGINES). A new engine needs a
# reader here, its permissions in template.yaml and a mode label in web/app.js.
READERS = {"nova": NovaReader, "textract": TextractReader}


def reader():
    engine = READERS.get(reading_engine())
    if engine is None:  # reading was switched off after the pages were uploaded
        raise JobFailed("reading_unavailable")
    return engine(model_id())


def handler(event, context):
    outcomes = []
    for record in event.get("Records") or []:
        key = unquote_plus(record.get("s3", {}).get("object", {}).get("key", ""))
        match = MANIFEST.fullmatch(key)
        if not match:
            log("ignored_object")
            continue
        try:
            outcomes.append(process_job(record["s3"]["bucket"]["name"], match.group(1), context))
        except Exception:
            log("worker_error", job_id=match.group(1), reason="internal_error")
            raise WorkerError("internal_error") from None
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
        pages = _load_pages(bucket, job_id, page_count, [int(n) for n in item.get("page_numbers") or []])
        omitted = [int(o["page"]) for o in item.get("omitted_pages") or []]
        quote, stats = _extract(job_id, pages, item, omitted, context)
        _save(job_id, item, quote, {**stats, "pages": page_count, "seconds": round(time.perf_counter() - started, 3)})
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


def _load_pages(bucket, job_id, page_count, numbers=()):
    """The uploaded page images, each with its page number in the original quote."""
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
        pages.append(PageImage(numbers[n - 1] if len(numbers) == page_count else n, data, 0, 0, 0, 0))
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


def _save_batch(job_id, record, item):
    """Keep one batch's reading on the job so a retry need not pay for it again. A batch that
    can't be saved, because it or the whole item would be too large, only costs a repeat call."""
    from botocore.exceptions import ClientError

    text = json.dumps(record, default=str)
    batches = {**(item.get("batches") or {}), str(record["batch"]): text}
    if len(text) > BATCH_SAVE_MAX_BYTES or item_size({**item, "batches": batches}) > ITEM_MAX_BYTES:
        log("batch_not_saved", job_id=job_id, batches=record["batch"], reason="too_large")
        return
    item["batches"] = batches
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


def _extract(job_id, pages, item, omitted, context):
    saved = item.get("batches") or {}
    engine = reader()
    model = engine.model
    batches, rejected = plan_batches(pages, engine.request_size, engine.request_limit, engine.max_pages)
    records, failures, resumed = [], [], 0
    for n, batch in enumerate(batches, 1):
        numbers = [p.page for p in batch]
        record = _saved_record(saved.get(str(n)), n, numbers, model)
        if record is not None:
            records.append(record)
            resumed += 1
            continue
        left = _time_left_ms(context)
        if left is not None and left < engine.call_budget_ms:
            log("out_of_time", job_id=job_id, batches=n)
            raise OutOfTime()
        _take_read(job_id, len(batches))
        try:
            result = engine.read(batch, n)
        except ExtractionFailure as f:
            if f.stops_run:
                raise JobFailed(REASONS.get(f.code, "model_unavailable")) from None
            failures.append({"batch": n, "pages": numbers, "kind": f.kind, "code": f.code})
            log("batch_failed", reason=f.kind, batches=n)
            continue
        record = {k: result.get(k) for k in SAVED_KEYS if k != "boxes"}
        record["boxes"] = boxes.record_boxes(result)  # page coordinates for highlighting, no text
        record["model_id"] = model
        records.append(record)
        _save_batch(job_id, record, item)
    if not records:
        raise JobFailed("extraction_failed")
    usage = [r.get("usage") or {} for r in records]
    stats = {"batches": len(batches), "failed_batches": len(failures), "resumed_batches": resumed}
    if model == textract.MODEL_ID:  # Textract bills by the page
        stats["pages_read"] = sum(u.get("pages", 0) for u in usage)
        stats["estimated_usd"] = textract.estimated_cost(stats["pages_read"])
    else:
        stats.update(input_tokens=sum(u.get("inputTokens", 0) for u in usage),
                     output_tokens=sum(u.get("outputTokens", 0) for u in usage))
    quote = merge_batches(records, failures, sorted(set(rejected) | set(omitted)))
    boxes.attach(quote, [b for r in records for b in r.get("boxes") or []])
    return quote, stats


def _take_read(job_id, batch_count):
    """Counts one paid read in the job's record, or stops the job once its budget is spent."""
    if not conditional_update(job_id, "ADD #r :one", "attribute_not_exists(#r) OR #r < :max", {"#r": "paid_reads"},
                              {":one": 1, ":max": READS_PER_BATCH * batch_count}):
        raise JobFailed("read_limit")


def _save(job_id, item, quote, stats):
    result = run_checks(quote, binding=job_binding(job_id, 0))
    extraction = json.dumps(quote, default=str)
    checked = json.dumps({k: result[k] for k in ("findings", "questions", "vendor_message", "vendor_message_lines", "check_this", "entry_checks")}, default=str)
    final = {**{k: v for k, v in item.items() if k not in ("batches", "reason")}, "extraction": extraction,
             "result": checked, "stats": json.dumps(stats), "processing_complete": True, "finished_at": now(),
             "expires_at": now()}
    if len(extraction) + len(checked) > RESULT_MAX_BYTES or item_size(final) > ITEM_MAX_BYTES:
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
    """Delete the job's uploads, trying again for any object S3 reports as not
    deleted. Logs counts only. The bucket's lifecycle rule removes anything left."""
    from botocore.exceptions import BotoCoreError, ClientError

    keys = [page_key(job_id, n) for n in range(1, page_count + 1)] + [manifest_key(job_id)]
    remaining = keys
    for attempt in range(DELETE_ATTEMPTS):
        if attempt:
            time.sleep(DELETE_BACKOFF_SECONDS * attempt)
        try:
            reply = s3().delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in remaining],
                                                               "Quiet": True})
        except (BotoCoreError, ClientError):
            continue
        failed = {e.get("Key") for e in reply.get("Errors") or []}
        remaining = [k for k in remaining if k in failed]
        if not remaining:
            break
    log("uploads_deleted", job_id=job_id, deleted=len(keys) - len(remaining), delete_errors=len(remaining))
