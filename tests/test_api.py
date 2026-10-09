import base64
import json
from pathlib import Path

import pytest

boto3 = pytest.importorskip("boto3")
pytest.importorskip("moto")

from moto import mock_aws

from api import common, jobs, worker
from checks import run_checks
from extract.dryrun import DryRunClient, load_dry_run_wire, remap_pages, stubbed_client, tool_response

ROOT = Path(__file__).resolve().parent.parent
REGION, BUCKET, TABLE = "ap-south-1", "surya-lekka-test", "jobs-test"
JPEG = b"\xff\xd8\xff\xe0" + bytes(200)
S1_ANSWERS = json.loads((ROOT / "samples" / "expected" / "S1.json").read_text(encoding="utf-8"))[
    "user_inputs"]["confirmations"]


class CountingClient:
    """Answers like the dry run and counts the Nova calls."""

    def __init__(self, replies=None):
        self.calls, self.replies = 0, list(replies or [])

    def converse(self, **request):
        self.calls += 1
        if self.replies:
            client, stubber = stubbed_client("bedrock-runtime", REGION)
            reply = self.replies.pop(0)
            if isinstance(reply, str):
                stubber.add_client_error("converse", service_error_code=reply, service_message="synthetic",
                                         http_status_code=403)
            else:
                stubber.add_response("converse", reply)
            return client.converse(**request)
        return DryRunClient(REGION).converse(**request)


@pytest.fixture
def aws(monkeypatch):
    env = {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_SESSION_TOKEN": "testing",
           "AWS_DEFAULT_REGION": REGION, "AWS_REGION": REGION, "TABLE_NAME": TABLE, "BUCKET_NAME": BUCKET,
           "UPLOADS_ENABLED": "true", "DAILY_JOB_CAP": "200", "READING_ENGINE": "nova"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        common.reset_clients()
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        boto3.client("dynamodb", region_name=REGION).create_table(
            TableName=TABLE, BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}])
        client = CountingClient()
        monkeypatch.setattr(worker, "bedrock_client", lambda: client)
        yield s3
    common.reset_clients()


IP = "198.51.100.7"
LIVE = {"live": "1"}


def call(handler, body=None, path=None, query=None, ip=IP, raw=None):
    event = {"body": raw if raw is not None else None if body is None else json.dumps(body),
             "isBase64Encoded": False, "pathParameters": path, "queryStringParameters": query,
             "requestContext": {"http": {"sourceIp": ip}}}
    r = handler(event, None)
    assert r["headers"]["cache-control"] == "no-store"
    return r["statusCode"], json.loads(r["body"])


def create(pages=2):
    status, body = call(jobs.create_job, {"page_count": pages})
    assert status == 201, body
    return body


def upload(s3, job, pages=2, manifest=None, page_bytes=JPEG):
    for n in range(1, pages + 1):
        s3.put_object(Bucket=BUCKET, Key=f"uploads/{job['job_id']}/page-{n:02d}.jpg", Body=page_bytes)
    body = manifest if manifest is not None else json.dumps({"pages": pages})
    s3.put_object(Bucket=BUCKET, Key=f"uploads/{job['job_id']}/manifest.json", Body=body)


def run_worker(job_id, context=None):
    event = {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": f"uploads/{job_id}/manifest.json"}}}]}
    return worker.handler(event, context)["outcomes"]


class LambdaClock:
    """A Lambda context whose remaining time is read from a list, one value per call."""

    def __init__(self, *remaining_ms):
        self.remaining = list(remaining_ms)

    def get_remaining_time_in_millis(self):
        return self.remaining.pop(0) if len(self.remaining) > 1 else self.remaining[0]


def retry(job, token=None):
    return call(jobs.retry_job, path={"id": job["job_id"]}, query={"t": job["token"] if token is None else token})


def get(job, token=None):
    return call(jobs.get_job, path={"id": job["job_id"]}, query={"t": job["token"] if token is None else token})


def item(job_id):
    return common.table().get_item(Key={"job_id": job_id})["Item"]


def counter():
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    found = common.table().get_item(Key={"job_id": f"counter#{day}"}).get("Item")
    return int(found["job_count"]) if found else 0


def uploads(s3, job_id):
    return [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET, Prefix=f"uploads/{job_id}/").get("Contents", [])]


def done_job(s3, pages=2):
    job = create(pages)
    upload(s3, job, pages)
    assert run_worker(job["job_id"]) == ["done"]
    return job


# --- POST /jobs ---------------------------------------------------------------------------

def test_create_job_returns_presigned_posts(aws):
    job = create(3)
    assert [u["page"] for u in job["uploads"]] == [1, 2, 3] and job["manifest_body"] == {"pages": 3}
    first = job["uploads"][0]["fields"]
    assert first["key"] == f"uploads/{job['job_id']}/page-01.jpg" and first["Content-Type"] == "image/jpeg"
    conditions = json.loads(base64.b64decode(first["policy"]))["conditions"]
    assert ["content-length-range", 1, 3_750_000] in conditions and {"Content-Type": "image/jpeg"} in conditions
    assert job["manifest"]["fields"]["key"] == f"uploads/{job['job_id']}/manifest.json"
    stored = item(job["job_id"])
    assert stored["status"] == "awaiting_upload" and int(stored["page_count"]) == 3
    assert stored["token_hash"] == common.token_hash(job["token"])
    assert job["token"] not in json.dumps(stored, default=str)  # only the hash is kept
    assert int(stored["expires_at"]) > int(stored["created_at"])


@pytest.mark.parametrize("pages", [0, 21, "3", True, None, 2.5])
def test_page_count_validated(aws, pages):
    status, body = call(jobs.create_job, {"page_count": pages} if pages is not None else {})
    assert status == 400 and body["error"] == "bad_page_count"
    assert create(20)["uploads"][-1]["page"] == 20


def test_kill_switch(aws, monkeypatch):
    monkeypatch.setenv("UPLOADS_ENABLED", "false")
    monkeypatch.setenv("DAILY_JOB_CAP", "0")  # the kill switch is checked first
    assert call(jobs.create_job, {"page_count": 2})[0] == 503
    assert call(jobs.create_sample_job, path={"sample_id": "S1"}, query=LIVE)[0] == 503
    assert counter() == 0


def test_reading_off_refuses_uploads_and_live_samples(aws, monkeypatch):
    monkeypatch.setenv("READING_ENGINE", "none")
    put_sample(aws)
    for status, body in (call(jobs.create_job, {"page_count": 2}),
                         call(jobs.create_sample_job, path={"sample_id": "S1"}, query=LIVE)):
        assert status == 503 and body == {"error": "reading_unavailable", "message": common.READING_UNAVAILABLE}
    assert common.READING_UNAVAILABLE == "AI reading isn't available yet. Please type the numbers instead."
    assert scan() == []  # no job and no slot taken
    status, job = call(jobs.create_sample_job, path={"sample_id": "S1"})  # saved readings still work
    assert status == 201 and get(job)[1]["status"] == "done"


@pytest.mark.parametrize("value", [None, "", "none", "bedrock", "NONE"])
def test_reading_is_off_unless_a_known_engine_is_named(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("READING_ENGINE", raising=False)
    else:
        monkeypatch.setenv("READING_ENGINE", value)
    assert common.reading_engine() == "none"


def test_every_reading_engine_has_a_reader():
    assert set(worker.READERS) == set(common.READING_ENGINES) and "none" not in worker.READERS


def test_switching_reading_off_stops_retries_and_queued_jobs(aws, monkeypatch):
    stopped = create()
    upload(aws, stopped)
    assert run_worker(stopped["job_id"], LambdaClock(0)) == ["interrupted"]
    queued = create()
    upload(aws, queued)
    monkeypatch.setenv("READING_ENGINE", "none")
    status, body = retry(stopped)
    assert status == 503 and body["error"] == "reading_unavailable"
    assert run_worker(queued["job_id"]) == ["failed"]
    view = get(queued)[1]
    assert view["reason"] == "reading_unavailable" and view["retryable"] is False
    assert uploads(aws, queued["job_id"]) == [] and worker.bedrock_client().calls == 0


def test_daily_cap_counts_jobs_and_live_samples(aws, monkeypatch):
    monkeypatch.setenv("DAILY_JOB_CAP", "2")
    put_sample(aws)
    assert call(jobs.create_job, {"page_count": 2})[0] == 201
    assert call(jobs.create_sample_job, path={"sample_id": "S1"}, query=LIVE)[0] == 201
    status, body = call(jobs.create_job, {"page_count": 2})
    assert status == 429 and body["error"] == "daily_limit"
    assert call(jobs.create_sample_job, path={"sample_id": "S1"}, query=LIVE)[0] == 429
    assert counter() == 2


def page_counter():
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    found = common.table().get_item(Key={"job_id": f"pagecount#{day}"}).get("Item")
    return int(found["pages_taken"]) if found else 0


def test_daily_page_cap_counts_declared_pages_of_jobs_and_live_samples(aws, monkeypatch):
    monkeypatch.setenv("DAILY_PAGE_CAP", "5")
    put_sample(aws)  # two pages
    assert call(jobs.create_job, {"page_count": 2})[0] == 201
    assert call(jobs.create_sample_job, path={"sample_id": "S1"}, query=LIVE)[0] == 201
    status, body = call(jobs.create_job, {"page_count": 2})
    assert status == 429 and body == {
        "error": "daily_limit", "message": "Today's limit of checks has been reached. Please try again tomorrow."}
    assert call(jobs.create_sample_job, path={"sample_id": "S1"}, query=LIVE)[0] == 429
    assert (counter(), page_counter()) == (2, 4)  # a refused check takes neither a job slot nor pages
    assert call(jobs.create_job, {"page_count": 1})[0] == 201
    assert (counter(), page_counter()) == (3, 5)
    assert call(jobs.create_job, {"page_count": 1})[0] == 429


def test_a_check_with_more_pages_than_the_cap_is_refused_without_writing(aws, monkeypatch):
    monkeypatch.setenv("DAILY_PAGE_CAP", "3")
    assert call(jobs.create_job, {"page_count": 4})[1]["error"] == "daily_limit"
    assert (counter(), page_counter()) == (0, 0)


def test_daily_page_cap_defaults_to_300(monkeypatch):
    monkeypatch.delenv("DAILY_PAGE_CAP", raising=False)
    assert common.daily_page_cap() == 300


def scan():
    return common.table().scan()["Items"]


@pytest.mark.parametrize("request_kwargs", [
    {"body": {"page_count": 99}}, {"body": {"page_count": "2"}}, {"body": {}}, {"raw": "not json"},
    {"raw": json.dumps({"page_count": 2, "pad": "x" * 70000})},
])
def test_invalid_requests_take_no_slot(aws, request_kwargs):
    status, body = call(jobs.create_job, **request_kwargs)
    assert status in (400, 413) and body["error"] in ("bad_page_count", "bad_json", "body_too_large")
    assert scan() == []


def test_missing_live_sample_takes_no_slot(aws):
    status, body = call(jobs.create_sample_job, path={"sample_id": "S2"}, query=LIVE)
    assert status == 404 and body["error"] == "sample_missing"
    assert scan() == []


@pytest.mark.parametrize("name,code", [("DAILY_JOB_CAP", "daily_limit"), ("IP_DAILY_JOB_CAP", "ip_limit"),
                                       ("DAILY_PAGE_CAP", "daily_limit")])
@pytest.mark.parametrize("value", ["0", "-1", "lots", ""])
def test_cap_of_zero_or_unreadable_refuses_without_writing(aws, monkeypatch, name, code, value):
    monkeypatch.setenv(name, value)
    put_sample(aws)
    status, body = call(jobs.create_job, {"page_count": 2})
    assert status == 429 and body["error"] == code
    assert call(jobs.create_sample_job, path={"sample_id": "S1"}, query=LIVE)[1]["error"] == code
    assert scan() == []


def test_per_ip_daily_limit(aws, monkeypatch):
    monkeypatch.setenv("IP_DAILY_JOB_CAP", "2")
    put_sample(aws)
    assert call(jobs.create_job, {"page_count": 1}, ip="203.0.113.9")[0] == 201
    assert call(jobs.create_sample_job, path={"sample_id": "S1"}, query=LIVE, ip="203.0.113.9")[0] == 201
    status, body = call(jobs.create_job, {"page_count": 1}, ip="203.0.113.9")
    assert status == 429 and body["error"] == "ip_limit"
    assert call(jobs.create_sample_job, path={"sample_id": "S1"}, query=LIVE, ip="203.0.113.9")[0] == 429
    assert call(jobs.create_job, {"page_count": 1}, ip="203.0.113.10")[0] == 201
    assert counter() == 3  # refused requests take no slot for the day
    assert "203.0.113.9" not in json.dumps(scan(), default=str)  # the address is stored only as a keyed hash


def test_per_ip_limit_defaults_to_ten(monkeypatch):
    monkeypatch.delenv("IP_DAILY_JOB_CAP", raising=False)
    assert common.ip_daily_cap() == 10


# --- GET /jobs/{id} --------------------------------------------------------------------------

def test_token_check(aws, monkeypatch):
    job = create()
    compared = []
    real = common.hmac.compare_digest
    monkeypatch.setattr(common.hmac, "compare_digest", lambda a, b: compared.append(1) or real(a, b))
    assert get(job)[0] == 200 and get(job)[1]["status"] == "awaiting_upload"
    assert get(job, token="wrong")[0] == 404
    assert get(job, token="")[0] == 404
    assert call(jobs.get_job, path={"id": job["job_id"]}, query=None)[0] == 404
    assert call(jobs.get_job, path={"id": "counter#2026-10-08"}, query={"t": job["token"]})[0] == 404
    other = create()
    assert get({"job_id": other["job_id"], "token": job["token"]})[0] == 404
    assert len(compared) >= 4  # every check goes through the constant-time comparison


def test_stuck_processing_reported_as_failed_with_a_retry(aws):
    job = create()
    upload(aws, job)
    common.transition(job["job_id"], "awaiting_upload", "processing", claimed_at=common.now() - 901)
    assert get(job)[1] | {"job_id": None, "page_count": None} == {
        "job_id": None, "page_count": None, "status": "failed", "reason": "timed_out", "mode": "nova",
        "retryable": True}
    assert retry(job)[0] == 202 and get(job)[1]["status"] == "uploaded"
    assert run_worker(job["job_id"]) == ["done"] and get(job)[1]["status"] == "done"


def test_processing_under_fifteen_minutes_is_still_processing(aws):
    job = create()
    common.transition(job["job_id"], "awaiting_upload", "processing", claimed_at=common.now() - 880)
    assert get(job)[1]["status"] == "processing" and "retryable" not in get(job)[1]
    assert retry(job)[0] == 409


# --- worker ------------------------------------------------------------------------------------

def test_worker_extracts_saves_and_deletes_uploads(aws):
    job = done_job(aws)
    status, body = get(job)
    assert status == 200 and body["status"] == "done" and body["processing_complete"] is True
    assert [f["check_id"] for f in body["findings"]][:4] == [
        "C1_capacity", "C2_central_subsidy", "C3_gross_total", "C3_net_cost"]
    assert body["extraction"]["gross_total"]["value"]["raw"] == "Rs. 1,97,000/-"
    assert uploads(aws, job["job_id"]) == []
    stats = json.loads(item(job["job_id"])["stats"])
    assert stats["batches"] == 1 and stats["input_tokens"] == 1500 and stats["pages"] == 2


def test_duplicate_events_are_harmless(aws):
    job = done_job(aws)
    client = worker.bedrock_client()
    before = item(job["job_id"])
    assert run_worker(job["job_id"]) == ["duplicate"]
    assert client.calls == 1 and item(job["job_id"]) == before


def test_claim_race_has_one_winner(aws):
    job = create()
    upload(aws, job)
    assert common.transition(job["job_id"], "awaiting_upload", "uploaded")
    assert common.transition(job["job_id"], "uploaded", "processing") is True
    assert common.transition(job["job_id"], "uploaded", "processing") is False
    assert run_worker(job["job_id"]) == ["duplicate"]  # the other worker holds the claim
    assert worker.bedrock_client().calls == 0 and uploads(aws, job["job_id"])


def test_two_records_for_one_manifest_process_once(aws):
    job = create()
    upload(aws, job)
    record = {"s3": {"bucket": {"name": BUCKET}, "object": {"key": f"uploads/{job['job_id']}/manifest.json"}}}
    assert worker.handler({"Records": [record, record]}, None)["outcomes"] == ["done", "duplicate"]


@pytest.mark.parametrize("reply,reason", [
    ("AccessDeniedException", "model_access_denied"),
    ("ThrottlingException", "model_busy"),
    ("ValidationException", "model_rejected_request"),
])
def test_model_errors_fail_the_job_with_a_safe_reason(aws, monkeypatch, reply, reason):
    monkeypatch.setattr(worker, "bedrock_client", lambda client=CountingClient([reply]): client)
    job = create()
    upload(aws, job)
    assert run_worker(job["job_id"]) == ["failed"]
    status, body = get(job)
    assert body["status"] == "failed" and body["reason"] == reason
    assert "synthetic" not in json.dumps(item(job["job_id"]), default=str)


@pytest.mark.parametrize("manifest,page_bytes,reason", [
    ("not json", JPEG, "bad_manifest"),
    (json.dumps({"pages": 3}), JPEG, "bad_manifest"),
    (None, b"%PDF-1.7", "not_a_jpeg"),
])
def test_bad_uploads_fail_the_job(aws, manifest, page_bytes, reason):
    job = create()
    upload(aws, job, manifest=manifest, page_bytes=page_bytes)
    assert run_worker(job["job_id"]) == ["failed"]
    assert get(job)[1]["reason"] == reason


def test_missing_page_fails_the_job(aws):
    job = create(3)
    upload(aws, job, pages=2, manifest=json.dumps({"pages": 3}))
    assert run_worker(job["job_id"]) == ["failed"] and get(job)[1]["reason"] == "missing_upload"


def test_unexpected_error_is_stored_without_its_text(aws, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise ValueError("Sentinel quote text 7f3a")
    monkeypatch.setattr(worker, "run_checks", boom)
    job = create()
    upload(aws, job)
    with caplog.at_level("INFO", logger="surya_lekka"):
        assert run_worker(job["job_id"]) == ["failed"]
    assert get(job)[1]["reason"] == "internal_error"
    assert "Sentinel" not in caplog.text and "Sentinel" not in json.dumps(item(job["job_id"]), default=str)
    assert set(item(job["job_id"])["batches"]) == {"1"}  # kept for a retry


def test_saved_batches_go_when_a_job_cannot_be_retried(aws, monkeypatch):
    monkeypatch.setattr(worker, "run_checks", lambda quote: {})  # fails as internal_error after reading
    job = create()
    upload(aws, job)
    common.table().update_item(Key={"job_id": job["job_id"]}, UpdateExpression="SET retries = :n",
                               ExpressionAttributeValues={":n": common.MAX_RETRIES})
    assert run_worker(job["job_id"]) == ["failed"]
    assert "batches" not in item(job["job_id"]) and uploads(aws, job["job_id"]) == []


def test_failed_batch_leaves_processing_incomplete(aws, monkeypatch):
    cut_short = tool_response(remap_pages(load_dry_run_wire(), [6]), stop_reason="max_tokens")
    first = tool_response(remap_pages(load_dry_run_wire(), [1, 2, 3, 4, 5]))
    monkeypatch.setattr(worker, "bedrock_client", lambda client=CountingClient([first, cut_short]): client)
    job = create(6)
    upload(aws, job, 6)
    assert run_worker(job["job_id"]) == ["done"]
    body = get(job)[1]
    assert body["processing_complete"] is False
    assert body["extraction"]["pages_skipped"] == [6]
    c2 = next(f for f in body["findings"] if f["check_id"] == "C2_central_subsidy")
    assert c2["status"] == "needs_confirmation" and "processed" in c2["message"]


def test_worker_stops_before_the_time_limit_and_a_retry_resumes(aws):
    job = create(6)
    upload(aws, job, 6)
    client = worker.bedrock_client()
    # Time for one model call: the second batch would not finish before Lambda's limit.
    assert run_worker(job["job_id"], LambdaClock(900_000, worker.CALL_BUDGET_MS - 1)) == ["interrupted"]
    assert client.calls == 1
    body = get(job)[1]
    assert (body["status"], body["reason"], body["retryable"]) == ("failed", "timed_out", True)
    assert len(uploads(aws, job["job_id"])) == 7  # the pages stay for the retry
    aws.delete_object(Bucket=BUCKET, Key=f"uploads/{job['job_id']}/manifest.json")
    status, body = retry(job)
    assert status == 202 and body["status"] == "uploaded"
    assert f"uploads/{job['job_id']}/manifest.json" in uploads(aws, job["job_id"])  # its event starts the worker
    assert run_worker(job["job_id"], LambdaClock(900_000)) == ["done"]
    assert client.calls == 2  # the saved first batch is not paid for again
    body = get(job)[1]
    assert body["status"] == "done" and body["processing_complete"] is True
    assert body["extraction"]["pages_processed"] == [1, 2, 3, 4, 5, 6]
    assert "batches" not in item(job["job_id"]) and uploads(aws, job["job_id"]) == []
    assert json.loads(item(job["job_id"])["stats"])["resumed_batches"] == 1


class HardStop(BaseException):
    """Stands in for Lambda stopping the function mid-call."""


def test_lambda_retry_after_a_hard_stop_resumes_from_saved_batches(aws, monkeypatch):
    class StopsOnSecondCall(CountingClient):
        def converse(self, **request):
            if self.calls == 1:
                self.calls += 1
                raise HardStop()
            return super().converse(**request)

    monkeypatch.setattr(worker, "bedrock_client", lambda client=StopsOnSecondCall(): client)
    job = create(6)
    upload(aws, job, 6)
    with pytest.raises(HardStop):
        run_worker(job["job_id"])
    assert item(job["job_id"])["status"] == "processing" and set(item(job["job_id"])["batches"]) == {"1"}
    fresh = CountingClient()
    monkeypatch.setattr(worker, "bedrock_client", lambda: fresh)
    assert run_worker(job["job_id"]) == ["duplicate"]  # a live claim is never taken over
    common.table().update_item(Key={"job_id": job["job_id"]}, UpdateExpression="SET claimed_at = :t",
                               ExpressionAttributeValues={":t": common.now() - common.STALE_SECONDS - 1})
    assert run_worker(job["job_id"]) == ["done"]  # Lambda's own retry, after the timeout
    assert fresh.calls == 1 and get(job)[1]["extraction"]["pages_processed"] == [1, 2, 3, 4, 5, 6]


def test_saved_batch_from_another_model_is_not_reused(aws, monkeypatch):
    job = create(6)
    upload(aws, job, 6)
    assert run_worker(job["job_id"], LambdaClock(900_000, 0)) == ["interrupted"]
    monkeypatch.setenv("MODEL_ID", "apac.amazon.nova-pro-v1:0")
    assert retry(job)[0] == 202 and run_worker(job["job_id"]) == ["done"]
    assert worker.bedrock_client().calls == 3


def test_worker_model_calls_time_out_at_120_seconds(monkeypatch):
    made = {}
    monkeypatch.setattr(worker, "make_client", lambda region, **kw: made.update(kw) or object())
    worker.bedrock_client.cache_clear()
    try:
        worker.bedrock_client()
    finally:
        worker.bedrock_client.cache_clear()
    assert made == {"retries": 0, "read_timeout": 120}
    assert worker.CALL_BUDGET_MS >= (120 + 10) * 1000  # read timeout plus connect timeout, then saving


def test_retry_refusals(aws, monkeypatch):
    pending = create()
    assert retry(pending)[0] == 409
    job = done_job(aws)
    assert retry(job)[0] == 409 and retry(job, token="wrong")[0] == 404
    bad = create()
    upload(aws, bad, manifest="not json")
    assert run_worker(bad["job_id"]) == ["failed"] and get(bad)[1]["retryable"] is False
    status, body = retry(bad)
    assert status == 409 and body["error"] == "not_retryable"
    stuck = create()
    upload(aws, stuck)
    common.transition(stuck["job_id"], "awaiting_upload", "processing", claimed_at=common.now() - 3600)
    monkeypatch.setenv("UPLOADS_ENABLED", "false")
    assert retry(stuck)[0] == 503
    monkeypatch.setenv("UPLOADS_ENABLED", "true")
    assert retry(stuck)[0] == 202
    assert retry(stuck)[0] == 409  # already queued


def test_retries_are_limited(aws):
    job = create(6)
    upload(aws, job, 6)
    for attempt in range(common.MAX_RETRIES):
        assert run_worker(job["job_id"], LambdaClock(0)) == ["interrupted"]
        assert get(job)[1]["retryable"] is True and retry(job)[0] == 202
    assert run_worker(job["job_id"], LambdaClock(0)) == ["interrupted"]
    assert get(job)[1]["retryable"] is False and retry(job)[0] == 409
    assert uploads(aws, job["job_id"]) == []  # nothing left to retry, so the pages go


def test_unretryable_failure_deletes_the_uploads(aws):
    job = create()
    upload(aws, job, page_bytes=b"%PDF-1.7")
    assert run_worker(job["job_id"]) == ["failed"] and uploads(aws, job["job_id"]) == []


def flaky_deletes(monkeypatch, failing_calls, raise_error=False):
    """S3 reports the first key of each of the first failing_calls delete calls as not deleted."""
    from botocore.exceptions import ClientError

    real = common.s3()
    delete, calls = real.delete_objects, []

    def delete_objects(**kwargs):
        keys = [o["Key"] for o in kwargs["Delete"]["Objects"]]
        calls.append(keys)
        if len(calls) > failing_calls:
            return delete(**kwargs)
        if raise_error:
            raise ClientError({"Error": {"Code": "InternalError", "Message": "Sentinel delete text"}}, "DeleteObjects")
        delete(Bucket=kwargs["Bucket"], Delete={"Objects": [{"Key": k} for k in keys[1:]], "Quiet": True})
        return {"Errors": [{"Key": keys[0], "Code": "InternalError", "Message": "Sentinel delete text"}]}

    monkeypatch.setattr(real, "delete_objects", delete_objects)
    monkeypatch.setattr(worker, "DELETE_BACKOFF_SECONDS", 0)
    return calls


def deletion_logs(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records
            if r.name == "surya_lekka" and json.loads(r.getMessage())["event"] == "uploads_deleted"]


@pytest.mark.parametrize("raise_error", [False, True])
def test_delete_errors_are_retried(aws, monkeypatch, caplog, raise_error):
    calls = flaky_deletes(monkeypatch, failing_calls=1, raise_error=raise_error)
    job = create()
    upload(aws, job)
    with caplog.at_level("INFO", logger="surya_lekka"):
        assert run_worker(job["job_id"]) == ["done"]
    assert len(calls) == 2 and calls[1] == (calls[0] if raise_error else calls[0][:1])
    assert uploads(aws, job["job_id"]) == []
    (event,) = deletion_logs(caplog)
    assert (event["deleted"], event["delete_errors"]) == (3, 0)
    assert "Sentinel" not in caplog.text and "page-01" not in caplog.text


def test_deletes_that_keep_failing_are_counted_not_described(aws, monkeypatch, caplog):
    calls = flaky_deletes(monkeypatch, failing_calls=99)
    job = create()
    upload(aws, job)
    with caplog.at_level("INFO", logger="surya_lekka"):
        assert run_worker(job["job_id"]) == ["done"]  # the lifecycle rule is the backstop
    assert len(calls) == worker.DELETE_ATTEMPTS and get(job)[1]["status"] == "done"
    (event,) = deletion_logs(caplog)
    assert (event["deleted"], event["delete_errors"]) == (2, 1)
    assert "Sentinel" not in caplog.text and "page-01" not in caplog.text and "InternalError" not in caplog.text


def test_other_objects_are_ignored(aws):
    event = {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": "uploads/x/page-01.jpg"}}}]}
    assert worker.handler(event, None) == {"outcomes": []}


# --- POST /samples/{id} ----------------------------------------------------------------------------

def saved_reading(sample_id):
    return (ROOT / "samples" / "cached" / f"{sample_id}.json").read_bytes()


def put_sample(s3, sample_id="S1", pages=2):
    for n in range(1, pages + 1):
        s3.put_object(Bucket=BUCKET, Key=f"samples/{sample_id}/page-{n:02d}.jpg", Body=JPEG)
    s3.put_object(Bucket=BUCKET, Key=f"samples/{sample_id}/manifest.json", Body=json.dumps({"pages": pages}))
    s3.put_object(Bucket=BUCKET, Key=f"samples/{sample_id}/reading.json", Body=saved_reading(sample_id))


def test_sample_route_serves_the_saved_reading_by_default(aws, monkeypatch):
    monkeypatch.setenv("UPLOADS_ENABLED", "false")  # no model call, so the kill switch and caps don't apply
    monkeypatch.setenv("DAILY_JOB_CAP", "0")
    put_sample(aws)
    status, job = call(jobs.create_sample_job, path={"sample_id": "S1"})
    assert status == 201 and set(job) == {"job_id", "token", "mode"} and job["mode"] == "saved"
    assert uploads(aws, job["job_id"]) == [] and worker.bedrock_client().calls == 0
    assert [i["job_id"] for i in scan()] == [job["job_id"]]  # no counters touched
    assert item(job["job_id"])["source"] == "sample:S1"
    reading = json.loads(saved_reading("S1"))
    body = get(job)[1]
    assert body["status"] == "done" and body["mode"] == "saved" and body["processing_complete"] is True
    assert body["extraction"] == reading["quote"] and body["page_text"] == reading["pages"]
    assert body["findings"] == json.loads(json.dumps(run_checks(reading["quote"])["findings"], default=str))
    status, checked = call(jobs.recheck, {"answers": S1_ANSWERS}, path={"id": job["job_id"]},
                           query={"t": job["token"]})
    assert status == 200 and {f["status"] for f in checked["findings"]} == {"consistent"}


def test_sample_route_uses_the_real_upload_path_when_live(aws):
    put_sample(aws)
    status, job = call(jobs.create_sample_job, path={"sample_id": "S1"}, query=LIVE)
    assert status == 201 and set(job) == {"job_id", "token", "mode"} and job["mode"] == "nova"
    assert sorted(uploads(aws, job["job_id"])) == [
        f"uploads/{job['job_id']}/manifest.json", f"uploads/{job['job_id']}/page-01.jpg",
        f"uploads/{job['job_id']}/page-02.jpg"]
    assert item(job["job_id"])["source"] == "sample:S1" and counter() == 1
    assert run_worker(job["job_id"]) == ["done"]  # what the manifest's S3 event starts
    assert get(job)[1]["status"] == "done" and worker.bedrock_client().calls == 1
    assert get(job)[1]["mode"] == "nova" and "page_text" not in get(job)[1]
    assert len(aws.list_objects_v2(Bucket=BUCKET, Prefix="samples/S1/")["Contents"]) == 4  # samples stay


@pytest.mark.parametrize("query", [None, LIVE])
def test_unknown_or_missing_sample(aws, query):
    assert call(jobs.create_sample_job, path={"sample_id": "S9"}, query=query)[0] == 404
    status, body = call(jobs.create_sample_job, path={"sample_id": "S2"}, query=query)
    assert status == 404 and body["error"] == "sample_missing"
    assert scan() == []


def test_bad_saved_reading_is_missing(aws):
    aws.put_object(Bucket=BUCKET, Key="samples/S1/reading.json", Body=b"not json")
    status, body = call(jobs.create_sample_job, path={"sample_id": "S1"})
    assert status == 404 and body["error"] == "sample_missing"


# --- POST /jobs/{id}/checks -----------------------------------------------------------------------

def test_recheck_with_answers_and_corrections(aws):
    job = done_job(aws)
    before = {f["check_id"]: f["status"] for f in get(job)[1]["findings"] if f["item"] is None}
    assert before["C2_central_subsidy"] == "needs_confirmation"
    status, body = call(jobs.recheck, {"answers": S1_ANSWERS, "corrections": {"base_price": "Rs. 1,80,000"}},
                        path={"id": job["job_id"]}, query={"t": job["token"]})
    assert status == 200
    after = {f["check_id"]: f["status"] for f in body["findings"] if f["item"] is None}
    assert after["C2_central_subsidy"] == "consistent"
    assert body["corrected_fields"][0]["path"] == "base_price"
    assert body["corrected_fields"][0]["provenance"] == "user_corrected"
    stored = get(job)[1]
    assert stored["corrections"]["user_inputs"]["confirmations"] == S1_ANSWERS
    assert {f["check_id"]: f["status"] for f in stored["findings"] if f["item"] is None} == after


def test_recheck_refusals(aws):
    pending = create()
    assert call(jobs.recheck, {}, path={"id": pending["job_id"]}, query={"t": pending["token"]})[0] == 409
    job = done_job(aws)
    where = {"path": {"id": job["job_id"]}, "query": {"t": job["token"]}}
    assert call(jobs.recheck, {}, path=where["path"], query={"t": "wrong"})[0] == 404
    status, body = call(jobs.recheck, {"corrections": {"evidence_text": "x"}}, **where)
    assert status == 400 and body["error"] == "unknown_field"
    assert call(jobs.recheck, {"answers": ["state"]}, **where)[0] == 400
    assert call(jobs.recheck, {"answers": {"favourite_colour": "red"}}, **where)[0] == 400


# --- logs ----------------------------------------------------------------------------------------------

def test_logs_hold_no_tokens_or_document_text(aws, caplog):
    put_sample(aws)
    with caplog.at_level("INFO", logger="surya_lekka"):
        job = done_job(aws)
        get(job)
        call(jobs.recheck, {"answers": S1_ANSWERS}, path={"id": job["job_id"]}, query={"t": job["token"]})
        sample = call(jobs.create_sample_job, path={"sample_id": "S1"})[1]
    text = caplog.text
    for secret in (job["token"], sample["token"], "Example Solar", "1,80,000", "EX-VR-0001", "Example PV"):
        assert secret not in text
    events = [json.loads(r.getMessage()) for r in caplog.records if r.name == "surya_lekka"]
    assert events and all(set(e) <= common.LOG_FIELDS | {"event"} for e in events)
    assert any(e["event"] == "job_done" and e["input_tokens"] == 1500 and "seconds" in e for e in events)


def test_log_refuses_other_fields():
    with pytest.raises(ValueError):
        common.log("job_done", evidence_text="anything")


# --- Amazon Textract as the reader -----------------------------------------------------------

SECRET_LINE = "Zebra-quartz-7731 internal note"


class FakeTextract:
    """Answers AnalyzeDocument with a handmade page: one total, one line nothing points at."""

    def __init__(self, errors=()):
        self.calls, self.errors = [], list(errors)

    def analyze_document(self, **request):
        from botocore.exceptions import ClientError
        from textract_pages import Page

        self.calls.append(request)
        if self.errors:
            code = self.errors.pop(0)
            if code:
                raise ClientError({"Error": {"Code": code, "Message": "synthetic"}}, "AnalyzeDocument")
        page = Page()
        page.line(SECRET_LINE, 0.1)
        page.line("Total amount payable Rs 1,90,000", 0.5)
        page.answer("TOTAL_PAYABLE", "Rs 1,90,000", top=0.5)
        return page.reply()


@pytest.fixture
def textract(aws, monkeypatch):
    monkeypatch.setenv("READING_ENGINE", "textract")
    monkeypatch.setenv("MODEL_ID", "global.amazon.nova-2-lite-v1:0")  # ignored by Textract
    client = FakeTextract()
    monkeypatch.setattr(worker, "textract_client", lambda: client)
    return client


def test_textract_reads_each_page_with_its_own_call(aws, textract, caplog):
    caplog.set_level("INFO")
    job = create(3)
    assert job.get("job_id")
    upload(aws, job, 3)
    assert run_worker(job["job_id"]) == ["done"]
    assert [c["Document"]["Bytes"] for c in textract.calls] == [JPEG] * 3
    assert all(set(c) == {"Document", "FeatureTypes", "QueriesConfig"} for c in textract.calls)
    view = get(job)[1]
    assert view["mode"] == "textract" and view["processing_complete"] is True
    total = view["extraction"]["gross_total"]
    assert total["value"]["parsed"] == "190000" and [c["page"] for c in total["candidates"]] == [1, 2, 3]
    stats = json.loads(item(job["job_id"])["stats"])
    assert stats == {**stats, "batches": 3, "pages_read": 3, "estimated_usd": 0.06, "pages": 3}
    assert '"pages_read": 3' in caplog.text and '"estimated_usd": 0.06' in caplog.text
    assert "input_tokens" not in stats
    assert worker.bedrock_client().calls == 0
    # Only mapped facts are kept: no raw reply, and nothing the mapping didn't use.
    stored = json.dumps(item(job["job_id"]), default=str)
    assert SECRET_LINE not in stored and "QUERY_RESULT" not in stored and "Blocks" not in stored
    assert SECRET_LINE not in caplog.text and "Rs 1,90,000" not in caplog.text
    assert uploads(aws, job["job_id"]) == []


def test_textract_saves_each_page_and_a_retry_reads_only_the_rest(aws, textract):
    job = create(3)
    upload(aws, job, 3)
    clock = LambdaClock(10 ** 6, 10 ** 6, 1000)  # time runs out before page 3
    assert run_worker(job["job_id"], clock) == ["interrupted"]
    saved = item(job["job_id"])["batches"]
    assert sorted(saved) == ["1", "2"]
    assert all(json.loads(v)["model_id"] == "textract" and "response" not in json.loads(v) for v in saved.values())
    assert retry(job)[0] == 202
    assert run_worker(job["job_id"]) == ["done"]
    assert len(textract.calls) == 3
    assert json.loads(item(job["job_id"])["stats"])["resumed_batches"] == 2


def test_textract_bad_page_leaves_processing_incomplete(aws, textract):
    textract.errors = [None, "BadDocumentException"]
    job = create(3)
    upload(aws, job, 3)
    assert run_worker(job["job_id"]) == ["done"]
    view = get(job)[1]
    assert view["processing_complete"] is False and view["extraction"]["pages_skipped"] == [2]


@pytest.mark.parametrize("code,reason", [("ProvisionedThroughputExceededException", "model_busy"),
                                         ("ThrottlingException", "model_busy"),
                                         ("AccessDeniedException", "model_access_denied"),
                                         ("ExpiredTokenException", "model_unavailable")])
def test_textract_throttling_and_access_errors_stop_the_job(aws, textract, code, reason):
    textract.errors = [code]
    job = create(2)
    upload(aws, job, 2)
    assert run_worker(job["job_id"]) == ["failed"]
    assert get(job)[1]["reason"] == reason and len(textract.calls) == 1


def test_textract_client_is_made_for_the_stack_region(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    worker.textract_client.cache_clear()
    client = worker.textract_client()
    assert client.meta.region_name == "ap-south-1" and client.meta.service_model.service_name == "textract"
    worker.textract_client.cache_clear()
