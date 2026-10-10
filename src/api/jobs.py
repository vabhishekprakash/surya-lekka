"""HTTP API handlers: create a job, read it, re-run the checks, retry it, and
start a sample.

The browser renders the quote's pages to JPEG and uploads them with the
presigned POSTs from POST /jobs, then uploads manifest.json, which starts the
worker. Every job read or change needs the job's secret token.
"""

import json
import os
import uuid

from checks import run_checks
from extract.render import MAX_IMAGE_BYTES, MAX_PAGES

from .common import (
    MANIFEST_MAX_BYTES,
    MAX_RETRIES,
    RETRYABLE_REASONS,
    STALE_SECONDS,
    UPLOAD_URL_SECONDS,
    ApiError,
    check_new_job,
    authorised_job,
    body_json,
    bucket_name,
    check_kill_switch,
    check_reading_available,
    check_item_size,
    conditional_update,
    job_binding,
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


MAX_TOTAL_PAGES = 2000
OMISSION_REASONS = {"over_limit", "too_large", "unreadable"}


def _page_plan(body, pages):
    """(page numbers, total pages, omitted pages) from POST /jobs. Each uploaded image keeps its
    page number in the original quote; every other page is listed as omitted with its reason
    (over the page limit, too large to send, or unreadable), so the reading is marked incomplete."""
    numbers = body.get("page_numbers", list(range(1, pages + 1)))
    total = body.get("total_pages", pages)
    omitted = body.get("omitted", [])

    def whole(n, low, high):
        return not isinstance(n, bool) and isinstance(n, int) and low <= n <= high
    bad = ApiError(400, "bad_page_plan", "page_numbers, total_pages and omitted must list every page once.")
    if not whole(total, pages, MAX_TOTAL_PAGES) or not isinstance(numbers, list) or not isinstance(omitted, list):
        raise bad
    if len(numbers) != pages or not all(whole(n, 1, total) for n in numbers) or numbers != sorted(set(numbers)):
        raise bad
    if not all(isinstance(o, dict) and set(o) == {"page", "reason"} and whole(o["page"], 1, total)
               and o["reason"] in OMISSION_REASONS for o in omitted):
        raise bad
    left_out = [o["page"] for o in omitted]
    if len(set(left_out)) != len(left_out) or set(left_out) & set(numbers) or len(left_out) + pages != total:
        raise bad
    return numbers, total, sorted(omitted, key=lambda o: o["page"])


@guarded("create")
def create_job(event, context):
    """POST /jobs {"page_count": n, "page_numbers": [...], "total_pages": t, "omitted": [{"page", "reason"}]}

    page_count is the number of page images to upload. The other fields are optional: without
    them the images are pages 1 to n of an n-page quote."""
    try:
        check_reading_available()
        check_kill_switch()
        body = body_json(event)
        pages = body.get("page_count")
        if isinstance(pages, bool) or not isinstance(pages, int) or not 1 <= pages <= MAX_PAGES:
            raise ApiError(400, "bad_page_count", f"page_count must be a whole number from 1 to {MAX_PAGES}.")
        numbers, total, omitted = _page_plan(body, pages)
        take_slots(event, "upload", pages)
        job_id, token = new_job(pages, "upload", mode=reading_engine(), page_numbers=numbers, total_pages=total,
                                omitted_pages=omitted)
        reply = {
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
    return response(201, reply)


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
    source = str(item.get("source") or "")
    if source.startswith("sample:") and view["mode"] == "saved":
        view["page_images"] = _sample_page_urls(source.split(":", 1)[1], view["page_count"])
    if status == "done":
        result = json.loads(item.get("checked") or item["result"])
        view.update({
            "extraction": json.loads(item["extraction"]),
            "processing_complete": bool(item.get("processing_complete")),
            "findings": result["findings"],
            "questions": result["questions"],
            "vendor_message": result["vendor_message"],
            "vendor_message_lines": result.get("vendor_message_lines", []),
            "check_this": result.get("check_this", []),
            "entry_checks": result.get("entry_checks", []),
            "corrections": json.loads(item["corrections"]) if item.get("corrections") else None,
        })
    return view


SAMPLE_IMAGE_SECONDS = 900


def _sample_page_urls(sample_id, pages):
    """Short-lived links to a synthetic sample's page images, so its values can be highlighted
    on the page. Samples only: a household's own pages never leave their device this way."""
    if sample_id not in sample_ids():
        return []
    return [s3().generate_presigned_url("get_object", ExpiresIn=SAMPLE_IMAGE_SECONDS,
                                        Params={"Bucket": bucket_name(), "Key": f"samples/{sample_id}/page-{n:02d}.jpg"})
            for n in range(1, pages + 1)]


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
    """POST /jobs/{id}/checks?t=token {"corrections": {...}, "answers": {...}, "verified": [token],
    "confirmed_operands": [token]}

    Runs the checks on the original extraction with the user's corrections and
    answers (consumer type, state, portal date, first system, prior subsidy, Give It
    Up, selected option), the values the household checked one by one and the operand sets
    it confirmed (tokens bound to option, field and value). Corrections are stored with
    their provenance.
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
        tokens = {}
        for key in ("verified", "confirmed_operands"):  # tokens from an earlier result
            tokens[key] = body.get(key) or []
            if not isinstance(tokens[key], list) or not all(isinstance(t, str) for t in tokens[key]):
                raise ApiError(400, "bad_inputs", f"{key} must be a list of tokens.")
        user_inputs = {"corrections": corrections, "confirmations": answers, **tokens}
        # Any change of corrections or answers (an option switch included) starts a new review
        # revision, so tokens issued before it no longer confirm anything, even after a revert.
        # Nothing is written until the request is fully checked, and then corrections, result and
        # revision are written together, only if the revision is still the one read here.
        revision = int(item.get("review_revision", 0))
        before = json.loads(item["corrections"])["user_inputs"] if item.get("corrections") else {}
        changed = (corrections, answers) != (before.get("corrections") or {}, before.get("confirmations") or {})
        target = revision + 1 if changed else revision
        try:
            result = run_checks(json.loads(item["extraction"]), user_inputs,
                                binding=job_binding(item["job_id"], target))
        except KeyError as e:
            raise ApiError(400, "unknown_field", str(e).strip("'\"")) from None
        except (TypeError, ValueError, AttributeError):
            raise ApiError(400, "bad_inputs", "A correction or answer has the wrong type.") from None
        checked = {k: result[k] for k in ("findings", "questions", "vendor_message", "vendor_message_lines", "check_this", "entry_checks")}
        stored = {"user_inputs": user_inputs,
                  "corrected_fields": [{**c, "provenance": "user_corrected"} for c in result["corrected_fields"]]}
        checked_text, stored_text = json.dumps(checked, default=str), json.dumps(stored, default=str)
        attributes = {"checked": checked_text, "corrections": stored_text, "checked_at": now(),
                      "review_revision": target, "checked_revision": target}
        check_item_size({**item, **attributes})
        first = "attribute_not_exists(#review_revision) OR " if revision == 0 else ""
        names = {f"#{k}": k for k in attributes}
        if not conditional_update(
                item["job_id"], "SET " + ", ".join(f"#{k} = :{k}" for k in attributes),
                f"({first}#review_revision = :expected) AND #status = :done", {**names, "#status": "status"},
                {**{f":{k}": v for k, v in attributes.items()}, ":expected": revision, ":done": "done"}):
            raise ApiError(409, "review_changed", "The review changed in another window. Please open the quote again.")
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
    # S4 is a hidden tester sample, reached only by a direct link.
    return [s.strip() for s in os.environ.get("SAMPLE_IDS", "S1,S2,S3,S4").split(",") if s.strip()]


@guarded("sample")
def create_sample_job(event, context):
    """POST /samples/{sample_id}: a done job holding the sample's saved reading
    (samples/<id>/reading.json in the bucket). No model call; saved samples have their own daily
    quotas (per address and overall), and the kill switch pauses them too.

    POST /samples/{sample_id}?live=1 copies the synthetic sample pages under
    samples/ in the bucket into a new upload, which runs the same worker and
    model call as an upload and counts against the caps. It is refused while
    reading is switched off."""
    sample_id = path_param(event, "sample_id")
    live = query_param(event, "live") == "1"
    try:
        check_kill_switch()
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
    job_id = str(uuid.uuid4())  # the findings' tokens are signed for this job, revision 0
    result = run_checks(quote, binding=job_binding(job_id, 0))
    checked = {k: result[k] for k in ("findings", "questions", "vendor_message", "vendor_message_lines", "check_this", "entry_checks")}
    job = dict(page_count=len(pages), source=f"sample:{sample_id}", mode="saved", status="done", job_id=job_id,
               extraction=json.dumps(quote, default=str), result=json.dumps(checked, default=str),
               processing_complete=quote.get("processing_complete") is True, page_text=json.dumps(pages),
               finished_at=now())
    check_new_job(**job)  # an oversized saved reading is refused before it spends a sample slot
    take_slots(event, "sample")
    job_id, token = new_job(**job)
    return job_id, token, len(pages)


def _live_sample(event, sample_id):
    check_reading_available()
    check_kill_switch()
    pages = _sample_object(sample_id, "manifest.json", MANIFEST_MAX_BYTES).get("pages")
    if isinstance(pages, bool) or not isinstance(pages, int) or not 1 <= pages <= MAX_PAGES:
        raise ApiError(404, *SAMPLE_MISSING)
    take_slots(event, "upload", pages)
    job_id, token = new_job(pages, f"sample:{sample_id}", mode=reading_engine())
    bucket = bucket_name()
    for n in range(1, pages + 1):
        s3().copy_object(Bucket=bucket, Key=page_key(job_id, n),
                         CopySource={"Bucket": bucket, "Key": f"samples/{sample_id}/page-{n:02d}.jpg"})
    s3().put_object(Bucket=bucket, Key=manifest_key(job_id), Body=json.dumps({"pages": pages}).encode(),
                    ContentType="application/json")
    return job_id, token, pages
