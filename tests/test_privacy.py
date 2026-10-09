"""Exception paths never put document text, evidence or job tokens in the logs
or in the error a handler returns or raises."""

import json
import traceback

import pytest

pytest.importorskip("moto")

from botocore.exceptions import ClientError, ReadTimeoutError
from test_api import (  # noqa: F401  (aws is a fixture)
    BUCKET,
    LIVE,
    S1_ANSWERS,
    aws,
    call,
    create,
    done_job,
    get,
    put_sample,
    retry,
    run_worker,
    upload,
)

from api import common, jobs, worker
from extract.dryrun import load_dry_run_wire, remap_pages, tool_response

SENTINEL = "Sentinel quote text 7f3a"
EVIDENCE = ("Example Solar", "1,80,000", "EX-VR-0001", "Example PV", "Base price")


def client_error(code="InternalError", operation="Operation"):
    return ClientError({"Error": {"Code": code, "Message": f"{SENTINEL} Base price Rs. 1,80,000"}}, operation)


class RaisingClient:
    def __init__(self, exc):
        self.exc = exc

    def converse(self, **request):
        raise self.exc


class ReplyClient:
    def __init__(self, reply):
        self.reply = reply

    def converse(self, **request):
        return self.reply


@pytest.fixture
def logs(caplog, capsys):
    """Everything logged at INFO and above, plus anything printed."""
    def text():
        out = capsys.readouterr()
        return caplog.text + out.out + out.err
    with caplog.at_level("INFO"):
        yield text


def assert_clean(text, *secrets):
    for secret in (SENTINEL, *EVIDENCE, *secrets):
        assert secret not in text, secret


def invalid_tool_input():
    data = remap_pages(load_dry_run_wire(), [1, 2])
    data["prices"][0]["evidence_text"] = {"nested": SENTINEL}  # fails validation
    return tool_response(data)


def failing_update(monkeypatch, when):
    real = common.table()
    update = real.update_item

    def update_item(**kwargs):
        if when in kwargs["UpdateExpression"]:
            raise client_error("ValidationException", "UpdateItem")
        return update(**kwargs)
    monkeypatch.setattr(real, "update_item", update_item)


def failing_get_object(monkeypatch):
    real = common.s3()
    monkeypatch.setattr(real, "get_object", lambda **kwargs: (_ for _ in ()).throw(client_error()))


def raiser(*args, **kwargs):
    raise ValueError(f"{SENTINEL} Example Solar Pvt Ltd")


WORKER_BREAKS = {
    "model_error_that_stops_the_job": (
        lambda mp: mp.setattr(worker, "bedrock_client", lambda: RaisingClient(client_error("ValidationException"))),
        "model_rejected_request"),
    "model_error_for_one_batch": (
        lambda mp: mp.setattr(worker, "bedrock_client", lambda: RaisingClient(client_error("ModelErrorException"))),
        "extraction_failed"),
    "read_timeout": (
        lambda mp: mp.setattr(worker, "bedrock_client",
                              lambda: RaisingClient(ReadTimeoutError(endpoint_url=SENTINEL))),
        "extraction_failed"),
    "invalid_tool_input": (
        lambda mp: mp.setattr(worker, "bedrock_client", lambda: ReplyClient(invalid_tool_input())),
        "extraction_failed"),
    "merge_raises": (lambda mp: mp.setattr(worker, "merge_batches", raiser), "internal_error"),
    "checks_raise": (lambda mp: mp.setattr(worker, "run_checks", raiser), "internal_error"),
    "storage_read_fails": (failing_get_object, "storage_error"),
}


@pytest.mark.parametrize("name", WORKER_BREAKS)
def test_worker_failures_log_only_codes(aws, monkeypatch, logs, name):
    breaks, reason = WORKER_BREAKS[name]
    job = create()
    upload(aws, job)
    breaks(monkeypatch)
    assert run_worker(job["job_id"]) == ["failed"]
    assert get(job)[1]["reason"] == reason
    stored = json.dumps(common.table().get_item(Key={"job_id": job["job_id"]})["Item"], default=str)
    assert SENTINEL not in stored
    assert_clean(logs(), job["token"])


def test_batch_that_cannot_be_saved_logs_only_its_number(aws, monkeypatch, logs):
    failing_update(monkeypatch, "#batches.#n")
    job = create()
    upload(aws, job)
    assert run_worker(job["job_id"]) == ["done"]
    text = logs()
    assert '"event": "batch_not_saved"' in text and "ValidationException" in text
    assert_clean(text, job["token"])


def test_worker_error_outside_the_job_is_raised_without_its_text(aws, monkeypatch, logs):
    job = create()
    upload(aws, job)
    monkeypatch.setattr(worker, "claim", lambda job_id: raiser())
    with pytest.raises(worker.WorkerError) as raised:
        run_worker(job["job_id"])
    shown = "".join(traceback.format_exception(raised.value)) + str(raised.value) + repr(raised.value)
    assert_clean(logs() + shown, job["token"])


def test_job_read_error_is_a_plain_500(aws, monkeypatch, logs):
    job = create()
    monkeypatch.setattr(common.table(), "get_item", lambda **kwargs: (_ for _ in ()).throw(client_error()))
    status, body = get(job)
    assert status == 500 and body["error"] == "internal_error"
    assert_clean(logs() + json.dumps(body), job["token"])


def test_recheck_error_is_a_plain_500(aws, monkeypatch, logs):
    job = done_job(aws)
    monkeypatch.setattr(jobs, "run_checks", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(SENTINEL)))
    status, body = call(jobs.recheck, {"answers": S1_ANSWERS}, path={"id": job["job_id"]},
                        query={"t": job["token"]})
    assert status == 500 and body["error"] == "internal_error"
    assert_clean(logs() + json.dumps(body), job["token"])


def test_recheck_refusal_logs_only_the_code(aws, logs):
    job = done_job(aws)
    status, body = call(jobs.recheck, {"corrections": {SENTINEL: "Rs. 1,80,000"}}, path={"id": job["job_id"]},
                        query={"t": job["token"]})
    assert status == 400 and body["error"] == "unknown_field"
    assert_clean(logs(), job["token"])


def test_create_error_is_a_plain_500(aws, monkeypatch, logs):
    monkeypatch.setattr(common.s3(), "generate_presigned_post",
                        lambda **kwargs: (_ for _ in ()).throw(client_error()))
    status, body = call(jobs.create_job, {"page_count": 2})
    assert status == 500 and body["error"] == "internal_error"
    assert_clean(logs() + json.dumps(body))


@pytest.mark.parametrize("query", [None, LIVE])
def test_sample_error_is_a_plain_500(aws, monkeypatch, logs, query):
    put_sample(aws)
    monkeypatch.setattr(jobs, "run_checks", raiser)
    monkeypatch.setattr(common.s3(), "copy_object", raiser)
    status, body = call(jobs.create_sample_job, path={"sample_id": "S1"}, query=query)
    assert status == 500 and body["error"] == "internal_error"
    assert_clean(logs() + json.dumps(body))


def test_retry_error_is_a_plain_500(aws, monkeypatch, logs):
    job = create()
    upload(aws, job)
    common.transition(job["job_id"], "awaiting_upload", "processing", claimed_at=common.now() - 3600)
    monkeypatch.setattr(common.s3(), "put_object", lambda **kwargs: (_ for _ in ()).throw(client_error()))
    status, body = retry(job)
    assert status == 500 and body["error"] == "internal_error"
    assert_clean(logs() + json.dumps(body), job["token"])
