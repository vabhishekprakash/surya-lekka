import json
import threading
import time
import uuid
from http.client import HTTPConnection
from pathlib import Path

import pytest

pytest.importorskip("moto")

from api import local_server, worker

ROOT = Path(__file__).resolve().parent.parent
JPEG = b"\xff\xd8\xff\xe0" + bytes(200)


@pytest.fixture
def local(caplog):
    server, backend = local_server.make_server(port=0)
    backend.stub.delay = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with caplog.at_level("INFO"):
        yield server.server_address[1], backend
    server.shutdown()
    server.server_close()
    backend.stop()


def request(port, method, path, body=None, headers=None, raw=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    data = raw if raw is not None else None if body is None else json.dumps(body).encode()
    conn.request(method, path, body=data, headers={"content-type": "application/json", **(headers or {})})
    r = conn.getresponse()
    payload = r.read()
    conn.close()
    try:
        return r.status, json.loads(payload)
    except ValueError:
        return r.status, payload


def multipart(fields, data):
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="blob"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def post_form(port, target, data):
    assert target["url"] == f"http://127.0.0.1:{port}{local_server.UPLOAD_PATH}"
    body, content_type = multipart(target["fields"], data)
    return request(port, "POST", local_server.UPLOAD_PATH, raw=body, headers={"content-type": content_type})[0]


def wait_for(port, job, until=("done", "failed")):
    for _ in range(200):
        status, body = request(port, "GET", f"/jobs/{job['job_id']}?t={job['token']}")
        if body["status"] in until:
            return body
        time.sleep(0.05)
    raise AssertionError(f"job stuck in {body['status']}")


def upload_job(port, pages=2):
    status, job = request(port, "POST", "/jobs", {"page_count": pages})
    assert status == 201
    for u in job["uploads"]:
        assert post_form(port, u, JPEG) == 204
    assert post_form(port, job["manifest"], json.dumps(job["manifest_body"]).encode()) == 204
    return job


def test_serves_the_web_app_and_nothing_outside_it(local):
    port, _ = local
    status, page = request(port, "GET", "/")
    assert status == 200 and b"<html" in page
    assert request(port, "GET", "/config.js")[0] == 200
    for path in ("/../pyproject.toml", "/..%2fpyproject.toml", "/nothing-here.js"):
        assert request(port, "GET", path)[0] == 404


def test_upload_runs_the_worker_with_the_stub_extractor(local):
    port, _ = local
    job = upload_job(port)
    body = wait_for(port, job)
    assert body["status"] == "done" and body["mode"] == "stub"  # never labelled as read by Nova
    assert body["extraction"]["base_price"]["value"]["raw"]
    status, checked = request(port, "POST", f"/jobs/{job['job_id']}/checks?t={job['token']}",
                              {"corrections": {"base_price": "1,80,000"}, "answers": {"state": "Telangana"}})
    assert status == 200 and checked["corrected_fields"][0]["path"] == "base_price"


def test_upload_policy_is_enforced(local):
    port, _ = local
    status, job = request(port, "POST", "/jobs", {"page_count": 1})
    page = job["uploads"][0]
    assert post_form(port, page, b"") == 403  # below the content-length range
    wrong_key = {**page, "fields": {**page["fields"], "key": "uploads/other/page-01.jpg"}}
    assert post_form(port, wrong_key, JPEG) == 403
    wrong_type = {**page, "fields": {**page["fields"], "Content-Type": "text/html"}}
    assert post_form(port, wrong_type, JPEG) == 403
    assert post_form(port, page, JPEG) == 204


def test_saved_sample_and_manual_check(local):
    port, _ = local
    status, job = request(port, "POST", "/samples/S2")
    assert status == 201 and job["mode"] == "saved"
    body = wait_for(port, job)
    assert body["mode"] == "saved" and body["page_text"]["1"]
    status, result = request(port, "POST", "/checks", {"fields": {"panel_count": "6"}, "answers": {}})
    assert status == 200 and result["mode"] == "manual"


def test_failed_reading_can_be_retried(local):
    port, backend = local
    backend.stub.mode = "fail-once"
    job = upload_job(port)
    body = wait_for(port, job)
    assert (body["status"], body["reason"], body["retryable"]) == ("failed", "model_busy", True)
    assert request(port, "POST", f"/jobs/{job['job_id']}/retry?t={job['token']}")[0] == 202
    assert wait_for(port, job, until=("done",))["status"] == "done"


def test_request_log_never_shows_tokens(local, caplog):
    port, _ = local
    job = upload_job(port)
    wait_for(port, job)
    assert job["token"] not in caplog.text and f"/jobs/{job['job_id']}" in caplog.text


def test_reading_off_refuses_uploads_but_not_samples_or_typed_numbers():
    server, backend = local_server.make_server(port=0, env={"READING_ENGINE": "none"})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        status, body = request(port, "POST", "/jobs", {"page_count": 1})
        assert status == 503 and body["error"] == "reading_unavailable"
        status, job = request(port, "POST", "/samples/S1")
        assert status == 201 and wait_for(port, job)["mode"] == "saved"
        assert request(port, "POST", "/checks", {"fields": {"panel_count": "6"}, "answers": {}})[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        backend.stop()


def test_stopping_restores_the_real_worker_client():
    server, backend = local_server.make_server(port=0)
    assert worker.bedrock_client() is backend.stub
    server.server_close()
    backend.stop()
    assert worker.bedrock_client is not None and getattr(worker.bedrock_client, "cache_clear", None)
