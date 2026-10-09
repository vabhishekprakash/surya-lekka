import json
import runpy
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("moto")

from test_deploy_scripts import run_ps, windows_powershell

from api import local_server

ROOT = Path(__file__).resolve().parent.parent
smoke = runpy.run_path(str(ROOT / "scripts" / "smoke_test.py"))
JPEG = b"\xff\xd8\xff\xe0" + bytes(200)
SAMPLE_TEXT = ("Example PV", "Example Inverters", "EX-VR-0002", "1,50,000")  # S2's made-up quote text


@contextmanager
def serving(server, backend=None):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        if backend:
            backend.stop()


def local_api(env=None):
    server, backend = local_server.make_server(port=0, env=env)
    backend.stub.delay = 0
    return serving(server, backend)


def lines(capsys):
    return capsys.readouterr().out.splitlines()


def test_reading_off_stack_passes(capsys):
    with local_api({"READING_ENGINE": "none"}) as api:
        assert smoke["main"](["--api", api, "--reading-engine", "none"]) == 0
    out = lines(capsys)
    assert [line.split(":")[0] for line in out] == ["PASS sample", "PASS manual", "PASS reading", "All steps passed."]


def test_reading_on_stack_reads_one_page(tmp_path, capsys):
    page = tmp_path / "page.jpg"
    page.write_bytes(JPEG)
    with local_api() as api:
        assert smoke["main"](["--api", api, "--reading-engine", "nova", "--page", str(page), "--job-wait", "30"]) == 0
    out = lines(capsys)
    assert [line.split(":")[0] for line in out] == ["PASS sample", "PASS manual", "PASS job", "All steps passed."]


def test_failures_are_reported_without_document_text(capsys):
    with local_api() as api:  # reading is on, so the reading-off check must fail
        assert smoke["main"](["--api", api, "--reading-engine", "none"]) == 1
    out = "\n".join(lines(capsys))
    assert "FAIL reading: POST /jobs returned HTTP 201" in out and "1 step(s) failed." in out
    assert not any(text in out for text in SAMPLE_TEXT)


def test_typed_numbers_come_from_the_saved_reading():
    fields, answers = smoke["typed_numbers"](smoke["saved_quote"]())
    assert fields["panel_count"] == "5" and fields["panel_wattage"] == "500 W" and fields["dcr_declaration"] is True
    assert fields["extra_charges"] == [{"label": "Net meter charges", "amount": "Rs. 2,500", "included_in_total": "yes"}]
    assert answers["capacity_basis"] == "dc_kwp" and answers["extra_charges_complete"] is True
    assert smoke["expected_statuses"]()["C1_capacity"] == "inconsistent"


def fake_site(allow_origin):
    """A site and API in one: the page, a config.js naming this server, and CORS preflights."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            body, kind = ((b"<!doctype html><html></html>", "text/html") if self.path == "/" else
                          (local_server.config_js(base, "ap-south-1", False).encode(), "text/javascript"))
            self.send_response(200)
            self.send_header("content-type", kind)
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("access-control-allow-origin", allow_origin(self.server.server_address[1]))
            self.end_headers()

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


@pytest.mark.parametrize("allowed,result", [(lambda port: f"http://127.0.0.1:{port}", "PASS"),
                                            (lambda port: "https://elsewhere.example", "FAIL")])
def test_site_step_checks_the_page_config_and_cors(allowed, result):
    with serving(fake_site(allowed)) as url:
        args = type("Args", (), {"site": url + "/"})
        try:
            outcome = "PASS" if smoke["step_site"](smoke["Api"](url), args) else "FAIL"
        except smoke["Failed"] as e:
            outcome = "FAIL"
            assert "does not accept requests from" in str(e)
    assert outcome == result


def test_local_site_mode_serves_web_against_a_deployed_api():
    api = "https://abc123.execute-api.ap-south-1.amazonaws.com"
    with serving(local_server.make_site_server(0, api, "ap-south-1", True)) as url:
        port = int(url.rsplit(":", 1)[1])
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/config.js")
        config = conn.getresponse().read().decode()
        conn.request("GET", "/")
        page = conn.getresponse()
        assert page.status == 200 and b"<html" in page.read()
        conn.request("POST", "/jobs", body=json.dumps({"page_count": 1}))
        assert conn.getresponse().status == 404  # no local API in this mode
        conn.close()
    assert config == local_server.config_js(api, "ap-south-1", True)
    with pytest.raises(ValueError):
        local_server.make_site_server(0, "http://abc.example", "ap-south-1")


@windows_powershell
def test_smoke_script_reads_the_stack_and_runs_the_checks():
    with local_api({"READING_ENGINE": "none"}) as api:
        stack = {"Outputs": [{"OutputKey": "ApiUrl", "OutputValue": api}],
                 "Parameters": [{"ParameterKey": "ReadingEngine", "ParameterValue": "none"}]}
        run, calls = run_ps("smoke_test.ps1", "-Profile default -Region ap-south-1", {"FAKE_STACK": json.dumps(stack)})
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.index("AWS account: 656446902316") < run.stdout.index("PASS sample")
    assert "PASS reading" in run.stdout and "All steps passed." in run.stdout
    assert calls[1] == ("aws cloudformation describe-stacks --stack-name surya-lekka --profile default "
                        "--region ap-south-1 --query Stacks[0] --output json")
