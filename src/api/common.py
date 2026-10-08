"""Shared pieces for the API and worker Lambdas: settings, AWS clients, JSON
responses, job tokens, the daily cap and logging.

Logs carry job ids, timings, statuses and token counts only, never document
content, evidence text or job tokens. log() refuses any other field.
"""

import base64
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
LOG_FIELDS = {"job_id", "status", "reason", "seconds", "pages", "batches", "failed_batches", "input_tokens",
              "output_tokens", "http_status", "sample_id"}

JOB_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
MANIFEST_MAX_BYTES = 4096
UPLOAD_URL_SECONDS = 900
BODY_MAX_BYTES = 65536


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


def daily_cap():
    return int(os.environ.get("DAILY_JOB_CAP", "200"))


def job_ttl_seconds():
    return int(os.environ.get("JOB_TTL_HOURS", "24")) * 3600


def now():
    return int(time.time())


# --- AWS clients ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def s3():
    import boto3
    from botocore.config import Config

    return boto3.client("s3", config=Config(signature_version="s3v4"))


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


def check_gates():
    """Kill switch first, then the daily cap. Raises ApiError when either refuses."""
    if not uploads_enabled():
        raise ApiError(503, "uploads_disabled", "New checks are switched off for now. Please try again later.")
    if not take_daily_slot():
        raise ApiError(429, "daily_limit", "Today's limit of checks has been reached. Please try again tomorrow.")


def take_daily_slot():
    """Conditional increment of today's counter item. False once the cap is reached."""
    from botocore.exceptions import ClientError

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        table().update_item(
            Key={"job_id": f"counter#{day}"},
            UpdateExpression="ADD job_count :one SET expires_at = :expires",
            ConditionExpression="attribute_not_exists(job_count) OR job_count < :cap",
            ExpressionAttributeValues={":one": 1, ":cap": daily_cap(), ":expires": now() + 2 * 86400},
        )
    except ClientError as e:
        if error_code(e) == "ConditionalCheckFailedException":
            return False
        raise
    return True


def new_job(page_count, source):
    """Create a job awaiting upload. Returns (job_id, token)."""
    job_id = str(uuid.uuid4())
    token, digest = new_token()
    created = now()
    table().put_item(
        Item={"job_id": job_id, "status": "awaiting_upload", "token_hash": digest, "page_count": page_count,
              "source": source, "created_at": created, "expires_at": created + job_ttl_seconds()},
        ConditionExpression="attribute_not_exists(job_id)",
    )
    return job_id, token


def transition(job_id, from_status, to_status, **extra):
    """Conditional status change. False if the job is not in from_status."""
    from botocore.exceptions import ClientError

    sets = ["#status = :to"] + [f"#{k} = :{k}" for k in extra]
    names = {"#status": "status", **{f"#{k}": k for k in extra}}
    values = {":from": from_status, ":to": to_status, **{f":{k}": v for k, v in extra.items()}}
    try:
        table().update_item(Key={"job_id": job_id}, UpdateExpression="SET " + ", ".join(sets),
                            ConditionExpression="#status = :from", ExpressionAttributeNames=names,
                            ExpressionAttributeValues=values)
    except ClientError as e:
        if error_code(e) == "ConditionalCheckFailedException":
            return False
        raise
    return True


def authorised_job(event):
    """The job for /jobs/{id}?t=token, or ApiError 404 for an unknown id or a wrong token."""
    job_id, token = path_param(event, "id"), query_param(event, "t")
    not_found = ApiError(404, "not_found", "No check matches this link.")
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise not_found
    item = table().get_item(Key={"job_id": job_id}).get("Item")
    if not token_matches(token, (item or {}).get("token_hash")) or item is None:
        raise not_found
    return item
