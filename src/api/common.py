"""Shared pieces for the API and worker Lambdas: settings, AWS clients, JSON
responses, job tokens, the daily cap and logging.

Logs carry job ids, timings, statuses and token counts only, never document
content, evidence text or job tokens. log() refuses any other field.
"""

import base64
import functools
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache

LOGGER = logging.getLogger("surya_lekka")
LOGGER.setLevel(logging.INFO)
LOG_FIELDS = {"job_id", "status", "reason", "seconds", "pages", "batches", "failed_batches", "resumed_batches",
              "input_tokens", "output_tokens", "pages_read", "estimated_usd", "http_status", "sample_id",
              "deleted", "delete_errors"}

JOB_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
MANIFEST_MAX_BYTES = 4096
UPLOAD_URL_SECONDS = 900
BODY_MAX_BYTES = 65536
# The worker's Lambda timeout. Lambda stops a worker by then, so a claim older
# than this belongs to a worker that is no longer running.
STALE_SECONDS = 900
# Failures worth running again. Batches already read are kept, so a retry only
# pays for the rest.
RETRYABLE_REASONS = {"timed_out", "model_busy", "model_unavailable", "storage_error", "internal_error",
                     "extraction_failed"}
MAX_RETRIES = 2
# Engines the worker can read quotes with (READING_ENGINE). Any other value,
# "none" included, switches reading off: uploads and live samples are refused,
# while saved sample readings and typed-in numbers keep working.
READING_ENGINES = ("nova", "textract")
READING_UNAVAILABLE = "AI reading isn't available yet. Please type the numbers instead."


def log(event, **fields):
    unknown = set(fields) - LOG_FIELDS
    if unknown:
        raise ValueError(f"not a loggable field: {', '.join(sorted(unknown))}")
    LOGGER.info(json.dumps({"event": event, **fields}, sort_keys=True, default=str))


# --- settings ----------------------------------------------------------------------------

def table_name():
    return os.environ["TABLE_NAME"]


def bucket_name():
    return os.environ["BUCKET_NAME"]


def uploads_enabled():
    return os.environ.get("UPLOADS_ENABLED", "true").strip().lower() == "true"


def reading_engine():
    name = os.environ.get("READING_ENGINE", "none").strip()
    return name if name in READING_ENGINES else "none"


def _cap(name, default):
    """A cap from the environment. Anything that is not a whole number reads as 0,
    which refuses every new job."""
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return 0


def daily_cap():
    return _cap("DAILY_JOB_CAP", "200")


def daily_page_cap():
    return _cap("DAILY_PAGE_CAP", "300")


def ip_daily_cap():
    return _cap("IP_DAILY_JOB_CAP", "10")


def job_ttl_seconds():
    return int(os.environ.get("JOB_TTL_HOURS", "24")) * 3600


def now():
    return int(time.time())


# --- AWS clients ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def s3():
    import boto3
    from botocore.config import Config

    # Presigned POSTs name the Region's own endpoint: for up to a day after a bucket
    # is made outside us-east-1, the global endpoint answers with a 307 redirect.
    region = boto3.session.Session().region_name
    return boto3.client("s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com",
                        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))


@lru_cache(maxsize=None)
def table():
    import boto3

    return boto3.resource("dynamodb").Table(table_name())


def reset_clients():
    s3.cache_clear()
    table.cache_clear()


def error_code(exc):
    return getattr(exc, "response", {}).get("Error", {}).get("Code")


# --- HTTP -----------------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


def response(status, body):
    return {"statusCode": status, "body": json.dumps(body, default=str),
            "headers": {"content-type": "application/json", "cache-control": "no-store"}}


def error_response(e):
    return response(e.status, {"error": e.code, "message": e.message})


def guarded(action):
    """Turn any unexpected error in a handler into a plain 500. Only the action
    and a fixed reason are logged: exception text could quote the document."""
    def wrap(handler):
        @functools.wraps(handler)
        def guarded_handler(event, context):
            try:
                return handler(event, context)
            except Exception:
                log(f"{action}_error", reason="internal_error", http_status=500)
                return response(500, {"error": "internal_error",
                                      "message": "Something went wrong on our side. Please try again."})
        return guarded_handler
    return wrap


def body_json(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", "replace")
    if len(raw.encode("utf-8")) > BODY_MAX_BYTES:
        raise ApiError(413, "body_too_large", "The request body is too large.")
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        raise ApiError(400, "bad_json", "The request body is not valid JSON.") from None
    if not isinstance(data, dict):
        raise ApiError(400, "bad_json", "The request body must be a JSON object.")
    return data


def path_param(event, name):
    return (event.get("pathParameters") or {}).get(name)


def query_param(event, name):
    return (event.get("queryStringParameters") or {}).get(name)


# --- tokens ---------------------------------------------------------------------------------

def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token():
    """(token for the browser, hash to store). Only the hash is ever stored."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_matches(token, stored_hash):
    """Constant-time comparison of the token's hash with the stored hash."""
    given = token_hash(token) if isinstance(token, str) and token else ""
    stored = stored_hash if isinstance(stored_hash, str) else ""
    return hmac.compare_digest(given, stored) and bool(given)


# --- jobs ---------------------------------------------------------------------------------

def page_key(job_id, page):
    return f"uploads/{job_id}/page-{page:02d}.jpg"


def manifest_key(job_id):
    return f"uploads/{job_id}/manifest.json"


def check_kill_switch():
    if not uploads_enabled():
        raise ApiError(503, "uploads_disabled", "New checks are switched off for now. Please try again later.")


def check_reading_available():
    if reading_engine() == "none":
        raise ApiError(503, "reading_unavailable", READING_UNAVAILABLE)


def source_ip(event):
    return ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp") or "unknown"


def _ip_counter_key(day, ip):
    """The address is kept only as a keyed hash, and only for the day's counter."""
    key = os.environ.get("IP_HASH_KEY", "").encode("utf-8")
    digest = hmac.new(key, f"{day}|{ip}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"ipcount#{day}#{digest}"


def take_slots(event, pages):
    """One of the source address's daily slots, then one of the day's jobs together
    with the job's declared pages from the day's page allowance. Call only once the
    request is valid. A cap of 0 refuses before anything is written."""
    ip_limit = ApiError(429, "ip_limit", "You have reached today's limit of checks. Please try again tomorrow.")
    day_limit = ApiError(429, "daily_limit", "Today's limit of checks has been reached. Please try again tomorrow.")
    ip_cap, cap, page_cap = ip_daily_cap(), daily_cap(), daily_page_cap()
    if ip_cap <= 0:
        raise ip_limit
    if cap <= 0 or page_cap < pages:
        raise day_limit
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not _take_slot(_ip_counter_key(day, source_ip(event)), ip_cap):
        raise ip_limit
    if not _take_day_slots(day, cap, pages, page_cap):
        raise day_limit


def _take_slot(key, cap):
    """Conditional increment of a counter item. False once it reaches cap."""
    from botocore.exceptions import ClientError

    try:
        table().update_item(
            Key={"job_id": key},
            UpdateExpression="ADD job_count :one SET expires_at = :expires",
            ConditionExpression="attribute_not_exists(job_count) OR job_count < :cap",
            ExpressionAttributeValues={":one": 1, ":cap": cap, ":expires": now() + 2 * 86400},
        )
    except ClientError as e:
        if error_code(e) == "ConditionalCheckFailedException":
            return False
        raise
    return True


def _take_day_slots(day, cap, pages, page_cap):
    """The day's job slot and the job's pages in one transaction: both or neither.
    False once either counter would pass its cap. (The table resource's client
    converts plain values to DynamoDB types.)"""
    from botocore.exceptions import ClientError

    expires = now() + 2 * 86400

    def update(key, attribute, amount, limit):
        return {"Update": {
            "TableName": table_name(), "Key": {"job_id": key},
            "UpdateExpression": "ADD #count :amount SET expires_at = :expires",
            "ConditionExpression": "attribute_not_exists(#count) OR #count <= :limit",
            "ExpressionAttributeNames": {"#count": attribute},
            "ExpressionAttributeValues": {":amount": amount, ":limit": limit, ":expires": expires}}}
    try:
        table().meta.client.transact_write_items(TransactItems=[
            update(f"counter#{day}", "job_count", 1, cap - 1),
            update(f"pagecount#{day}", "pages_taken", pages, page_cap - pages)])
    except ClientError as e:
        if error_code(e) == "TransactionCanceledException":
            return False
        raise
    return True


def new_job(page_count, source, mode="nova", status="awaiting_upload", **fields):
    """Create a job, awaiting upload unless told otherwise. Returns (job_id, token).
    mode says who read the quote: a reading engine, or "saved" for a sample's saved reading."""
    job_id = str(uuid.uuid4())
    token, digest = new_token()
    created = now()
    table().put_item(
        Item={"job_id": job_id, "status": status, "token_hash": digest, "page_count": page_count,
              "source": source, "mode": mode, "created_at": created, "expires_at": created + job_ttl_seconds(),
              **fields},
        ConditionExpression="attribute_not_exists(job_id)",
    )
    return job_id, token


def conditional_update(job_id, update, condition, names, values):
    """update_item that returns False when the condition fails."""
    from botocore.exceptions import ClientError

    try:
        table().update_item(Key={"job_id": job_id}, UpdateExpression=update, ConditionExpression=condition,
                            ExpressionAttributeNames=names, ExpressionAttributeValues=values)
    except ClientError as e:
        if error_code(e) == "ConditionalCheckFailedException":
            return False
        raise
    return True


def transition(job_id, from_status, to_status, remove=(), **extra):
    """Conditional status change. False if the job is not in from_status."""
    sets = ["#status = :to"] + [f"#{k} = :{k}" for k in extra]
    names = {"#status": "status", **{f"#{k}": k for k in (*extra, *remove)}}
    values = {":from": from_status, ":to": to_status, **{f":{k}": v for k, v in extra.items()}}
    update = "SET " + ", ".join(sets) + (" REMOVE " + ", ".join(f"#{k}" for k in remove) if remove else "")
    return conditional_update(job_id, update, "#status = :from", names, values)


def claim(job_id):
    """uploaded -> processing, or take over a processing claim older than
    STALE_SECONDS. False if neither applies, for example a duplicate event."""
    t = now()
    return conditional_update(
        job_id, "SET #status = :processing, #claimed = :t, #batches = if_not_exists(#batches, :empty)",
        "#status = :uploaded OR (#status = :processing AND #claimed < :stale)",
        {"#status": "status", "#claimed": "claimed_at", "#batches": "batches"},
        {":processing": "processing", ":uploaded": "uploaded", ":t": t, ":stale": t - STALE_SECONDS, ":empty": {}})


def authorised_job(event):
    """The job for /jobs/{id}?t=token, or ApiError 404 for an unknown id, a wrong token
    or an expired job (DynamoDB's TTL deletion can lag by days)."""
    job_id, token = path_param(event, "id"), query_param(event, "t")
    not_found = ApiError(404, "not_found", "No check matches this link.")
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise not_found
    item = table().get_item(Key={"job_id": job_id}).get("Item")
    if not token_matches(token, (item or {}).get("token_hash")) or item is None:
        raise not_found
    if int(item.get("expires_at", 0)) <= now():
        raise not_found
    return item
