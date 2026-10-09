"""HTTP API handlers: create a job, read it, re-run the checks, retry it, and
start a sample.

The browser renders the quote's pages to JPEG and uploads them with the
presigned POSTs from POST /jobs, then uploads manifest.json, which starts the
worker. Every job read or change needs the job's secret token.
"""

import json
import os

from checks import run_checks
from extract.render import MAX_IMAGE_BYTES, MAX_PAGES

from .common import (
    MANIFEST_MAX_BYTES,
    MAX_RETRIES,
    RETRYABLE_REASONS,
    STALE_SECONDS,
    UPLOAD_URL_SECONDS,
    ApiError,
    authorised_job,
    body_json,
    bucket_name,
    check_kill_switch,
    check_reading_available,
    conditional_update,
    error_response,
    guarded,
    log,
    manifest_key,
    new_job,
    now,
    page_key,
    path_param,
    query_param,
    reading_engine,
    response,
    s3,
    table,
    take_slots,
)

RESULT_MAX_BYTES = 350_000
# The attribute that changes whenever a job enters each state, used to make a retry conditional.
STATE_MARK = {"processing": "claimed_at", "uploaded": "retry_at", "failed": "finished_at"}


def _presigned_post(key, content_type, max_bytes):
    post = s3().generate_presigned_post(
        Bucket=bucket_name(), Key=key, Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, max_bytes]],
        ExpiresIn=UPLOAD_URL_SECONDS)
    return {"url": post["url"], "fields": post["fields"]}


@guarded("create")
def create_job(event, context):
    """POST /jobs {"page_count": n}"""
    try:
        check_reading_available()
        check_kill_switch()
        pages = body_json(event).get("page_count")
        if isinstance(pages, bool) or not isinstance(pages, int) or not 1 <= pages <= MAX_PAGES:
            raise ApiError(400, "bad_page_count", f"page_count must be a whole number from 1 to {MAX_PAGES}.")
        take_slots(event)
        job_id, token = new_job(pages, "upload", mode=reading_engine())
        body = {
            "job_id": job_id,
            "token": token,
            "uploads": [{"page": n, **_presigned_post(page_key(job_id, n), "image/jpeg", MAX_IMAGE_BYTES)}
                        for n in range(1, pages + 1)],
            "manifest": _presigned_post(manifest_key(job_id), "application/json", MANIFEST_MAX_BYTES),
            "manifest_body": {"pages": pages},
            "expires_in": UPLOAD_URL_SECONDS,
        }
    except ApiError as e:
        log("create_refused", reason=e.code, http_status=e.status)
        return error_response(e)
    log("job_created", job_id=job_id, pages=pages, http_status=201)
    return response(201, body)


def _stuck(item):
    """A worker claim, or a queued retry, older than the worker's time limit."""
    mark = {"processing": "claimed_at", "uploaded": "retry_at"}.get(item["status"])
    return mark is not None and mark in item and now() - int(item[mark]) > STALE_SECONDS


def _view(item):
    status, reason = item["status"], item.get("reason")
    if _stuck(item):
        status, reason = "failed", "timed_out"
    view = {"job_id": item["job_id"], "status": status, "reason": reason, "page_count": int(item["page_count"]),
            "mode": item.get("mode", "nova")}
    if status == "failed":
        view["retryable"] = reason in RETRYABLE_REASONS and int(item.get("retries", 0)) < MAX_RETRIES
    if item.get("page_text"):
        view["page_text"] = json.loads(item["page_text"])
    if status == "done":
        result = json.loads(item.get("checked") or item["result"])
        view.update({
            "extraction": json.loads(item["extraction"]),
            "processing_complete": bool(item.get("processing_complete")),
            "findings": result["findings"],
            "questions": result["questions"],
            "vendor_message": result["vendor_message"],
            "corrections": json.loads(item["corrections"]) if item.get("corrections") else None,
        })
    return view


@guarded("read")
def get_job(event, context):
    """GET /jobs/{id}?t=token"""
    try:
        item = authorised_job(event)
    except ApiError as e:
        log("read_refused", reason=e.code, http_status=e.status)
        return error_response(e)
    view = _view(item)
    log("job_read", job_id=item["job_id"], status=view["status"], http_status=200)
    return response(200, view)


@guarded("recheck")
def recheck(event, context):
    """POST /jobs/{id}/checks?t=token {"corrections": {...}, "answers": {...}}

    Runs the checks on the original extraction with the user's corrections and
    answers (consumer type, state, portal date, first system, prior subsidy, Give It
    Up, selected option). Corrections are stored with their provenance.
    """
    try:
        item = authorised_job(event)
        if item["status"] != "done":
            raise ApiError(409, "not_ready", "The quote has not finished processing.")
        body = body_json(event)
        corrections = body.get("corrections") or {}
        answers = body.get("answers", body.get("confirmations")) or {}
        if not isinstance(corrections, dict) or not isinstance(answers, dict):
            raise ApiError(400, "bad_inputs", "corrections and answers must be JSON objects.")
        user_inputs = {"corrections": corrections, "confirmations": answers}
        try:
            result = run_checks(json.loads(item["extraction"]), user_inputs)
        except KeyError as e:
            raise ApiError(400, "unknown_field", str(e).strip("'\"")) from None
        except (TypeError, ValueError, AttributeError):
            raise ApiError(400, "bad_inputs", "A correction or answer has the wrong type.") from None
        checked = {k: result[k] for k in ("findings", "questions", "vendor_message")}
        stored = {"user_inputs": user_inputs,
                  "corrected_fields": [{**c, "provenance": "user_corrected"} for c in result["corrected_fields"]]}
        checked_text, stored_text = json.dumps(checked, default=str), json.dumps(stored, default=str)
        if len(checked_text) + len(stored_text) > RESULT_MAX_BYTES:
            raise ApiError(413, "too_large", "Too many corrections to store.")
        table().update_item(
            Key={"job_id": item["job_id"]},
            UpdateExpression="SET #checked = :checked, #corrections = :corrections, #checked_at = :at",
            ExpressionAttributeNames={"#checked": "checked", "#corrections": "corrections", "#checked_at": "checked_at"},
            ExpressionAttributeValues={":checked": checked_text, ":corrections": stored_text, ":at": now()})
    except ApiError as e:
        log("recheck_refused", reason=e.code, http_status=e.status)
        return error_response(e)
    log("rechecked", job_id=item["job_id"], http_status=200)
    return response(200, {**checked, "corrected_fields": stored["corrected_fields"]})


@guarded("retry")
def retry_job(event, context):
    """POST /jobs/{id}/retry?t=token: run a failed or stuck job again. The manifest
    is uploaded again, which starts the worker; batches the earlier run saved are
    reused, so only the rest are sent to the model."""
    try:
        item = authorised_job(event)
        check_reading_available()
        check_kill_switch()
        view = _view(item)
        if view["status"] != "failed" or not view.get("retryable"):
            raise ApiError(409, "not_retryable", "This check can't be run again. Please start a new one.")
        mark = STATE_MARK[item["status"]]
        seen = "attribute_not_exists(#mark)" if mark not in item else "#mark = :mark"
        values = {":uploaded": "uploaded", ":t": now(), ":zero": 0, ":one": 1, ":status": item["status"]}
        if mark in item:
            values[":mark"] = item[mark]
        if not conditional_update(
                item["job_id"],
                "SET #status = :uploaded, #retry_at = :t, #retries = if_not_exists(#retries, :zero) + :one "
                "REMOVE #reason",
                f"#status = :status AND {seen}",
                {"#status": "status", "#mark": mark, "#retry_at": "retry_at", "#retries": "retries",
                 "#reason": "reason"},
                values):
            raise ApiError(409, "changed", "This check changed while you were looking. Please refresh.")
        pages = int(item["page_count"])
        s3().put_object(Bucket=bucket_name(), Key=manifest_key(item["job_id"]),
                        Body=json.dumps({"pages": pages}).encode(), ContentType="application/json")
    except ApiError as e:
        log("retry_refused", reason=e.code, http_status=e.status)
        return error_response(e)
    log("job_retried", job_id=item["job_id"], http_status=202)
    return response(202, {"job_id": item["job_id"], "status": "uploaded"})


def sample_ids():
    return [s.strip() for s in os.environ.get("SAMPLE_IDS", "S1,S2,S3").split(",") if s.strip()]


@guarded("sample")
def create_sample_job(event, context):
    """POST /samples/{sample_id}: a done job holding the sample's saved reading
    (samples/<id>/reading.json in the bucket). No model call, so no cap applies.

    POST /samples/{sample_id}?live=1 copies the synthetic sample pages under
    samples/ in the bucket into a new upload, which runs the same worker and
    model call as an upload and counts against the caps. It is refused while
    reading is switched off."""
    sample_id = path_param(event, "sample_id")
    live = query_param(event, "live") == "1"
    try:
        if sample_id not in sample_ids():
            raise ApiError(404, "no_such_sample", "There is no sample with that name.")
        job_id, token, pages = (_live_sample if live else _saved_sample)(event, sample_id)
    except ApiError as e:
        log("sample_refused", reason=e.code, http_status=e.status)
        return error_response(e)
    log("sample_job_created", job_id=job_id, sample_id=sample_id, pages=pages, http_status=201)
    return response(201, {"job_id": job_id, "token": token, "mode": reading_engine() if live else "saved"})


SAMPLE_MISSING = ("sample_missing", "The sample is not available.")
READING_MAX_BYTES = 200_000


def _sample_object(sample_id, name, limit):
    from botocore.exceptions import ClientError

    try:
        body = s3().get_object(Bucket=bucket_name(), Key=f"samples/{sample_id}/{name}")["Body"].read(limit + 1)
        data = json.loads(body) if len(body) <= limit else None
    except (ClientError, ValueError):
        data = None
    if not isinstance(data, dict):
        raise ApiError(404, *SAMPLE_MISSING)
    return data


def _saved_sample(event, sample_id):
    reading = _sample_object(sample_id, "reading.json", READING_MAX_BYTES)
    quote, pages = reading.get("quote"), reading.get("pages")
    if reading.get("reading") != "saved" or not isinstance(quote, dict) or not isinstance(pages, dict):
        raise ApiError(404, *SAMPLE_MISSING)
    result = run_checks(quote)
    checked = {k: result[k] for k in ("findings", "questions", "vendor_message")}
    job_id, token = new_job(
        len(pages), f"sample:{sample_id}", mode="saved", status="done",
        extraction=json.dumps(quote, default=str), result=json.dumps(checked, default=str),
        processing_complete=quote.get("processing_complete") is True, page_text=json.dumps(pages),
        finished_at=now())
    return job_id, token, len(pages)


def _live_sample(event, sample_id):
    check_reading_available()
    check_kill_switch()
    pages = _sample_object(sample_id, "manifest.json", MANIFEST_MAX_BYTES).get("pages")
    if isinstance(pages, bool) or not isinstance(pages, int) or not 1 <= pages <= MAX_PAGES:
        raise ApiError(404, *SAMPLE_MISSING)
    take_slots(event)
    job_id, token = new_job(pages, f"sample:{sample_id}", mode=reading_engine())
    bucket = bucket_name()
    for n in range(1, pages + 1):
        s3().copy_object(Bucket=bucket, Key=page_key(job_id, n),
                         CopySource={"Bucket": bucket, "Key": f"samples/{sample_id}/page-{n:02d}.jpg"})
    s3().put_object(Bucket=bucket, Key=manifest_key(job_id), Body=json.dumps({"pages": pages}).encode(),
                    ContentType="application/json")
    return job_id, token, pages
