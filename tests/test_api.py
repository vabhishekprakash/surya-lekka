import base64
import json
from pathlib import Path

import pytest

boto3 = pytest.importorskip("boto3")
pytest.importorskip("moto")

from moto import mock_aws

from api import common, jobs, worker
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
           "UPLOADS_ENABLED": "true", "DAILY_JOB_CAP": "200"}
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


def call(handler, body=None, path=None, query=None):
    event = {"body": None if body is None else json.dumps(body), "isBase64Encoded": False,
             "pathParameters": path, "queryStringParameters": query}
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


def run_worker(job_id):
    event = {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": f"uploads/{job_id}/manifest.json"}}}]}
    return worker.handler(event, None)["outcomes"]


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
    assert call(jobs.create_sample_job, path={"sample_id": "S1"})[0] == 503
    assert counter() == 0


def test_daily_cap_counts_jobs_and_samples(aws, monkeypatch):
    monkeypatch.setenv("DAILY_JOB_CAP", "2")
    put_sample(aws)
    assert call(jobs.create_job, {"page_count": 2})[0] == 201
    assert call(jobs.create_sample_job, path={"sample_id": "S1"})[0] == 201
    status, body = call(jobs.create_job, {"page_count": 99})  # the cap is checked before validation
    assert status == 429 and body["error"] == "daily_limit"
    assert call(jobs.create_sample_job, path={"sample_id": "S1"})[0] == 429
    assert counter() == 2


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


def test_stale_processing_reported_as_timed_out(aws):
    job = create()
    common.transition(job["job_id"], "awaiting_upload", "processing", claimed_at=common.now() - 3600)
    assert get(job)[1] | {"job_id": None, "page_count": None} == {
        "job_id": None, "page_count": None, "status": "failed", "reason": "timed_out"}


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
        raise ValueError("Example Solar quote text")
    monkeypatch.setattr(worker, "run_checks", boom)
    job = create()
    upload(aws, job)
    with caplog.at_level("INFO", logger="surya_lekka"):
        assert run_worker(job["job_id"]) == ["failed"]
    assert get(job)[1]["reason"] == "internal_error"
    assert "Example Solar" not in caplog.text and "Example Solar" not in json.dumps(item(job["job_id"]), default=str)


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


def test_other_objects_are_ignored(aws):
    event = {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": "uploads/x/page-01.jpg"}}}]}
    assert worker.handler(event, None) == {"outcomes": []}


# --- POST /samples/{id} ----------------------------------------------------------------------------

def put_sample(s3, sample_id="S1", pages=2):
    for n in range(1, pages + 1):
        s3.put_object(Bucket=BUCKET, Key=f"samples/{sample_id}/page-{n:02d}.jpg", Body=JPEG)
    s3.put_object(Bucket=BUCKET, Key=f"samples/{sample_id}/manifest.json", Body=json.dumps({"pages": pages}))


def test_sample_route_uses_the_real_upload_path(aws):
    put_sample(aws)
    status, job = call(jobs.create_sample_job, path={"sample_id": "S1"})
    assert status == 201 and set(job) == {"job_id", "token"}
    assert sorted(uploads(aws, job["job_id"])) == [
        f"uploads/{job['job_id']}/manifest.json", f"uploads/{job['job_id']}/page-01.jpg",
        f"uploads/{job['job_id']}/page-02.jpg"]
    assert item(job["job_id"])["source"] == "sample:S1" and counter() == 1
    assert run_worker(job["job_id"]) == ["done"]  # what the manifest's S3 event starts
    assert get(job)[1]["status"] == "done" and worker.bedrock_client().calls == 1
    assert len(aws.list_objects_v2(Bucket=BUCKET, Prefix="samples/S1/")["Contents"]) == 3  # samples stay


def test_unknown_or_missing_sample(aws):
    assert call(jobs.create_sample_job, path={"sample_id": "S9"})[0] == 404
    assert counter() == 0  # an unknown name is refused before the cap
    status, body = call(jobs.create_sample_job, path={"sample_id": "S2"})
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
