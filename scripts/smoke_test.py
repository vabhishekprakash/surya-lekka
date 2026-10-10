"""Smoke test a running Surya Lekka API from the outside, as the web app uses it.

    python scripts/smoke_test.py --api <ApiUrl> [--reading-engine none|nova] [--site <SiteUrl>] [--page <jpeg>]

Steps:
  sample   POST /samples/S2 gives a done job with the saved reading, every result held until the numbers
           are confirmed, and the expected results once they are.
  manual   POST /checks with S2's numbers typed in holds every result until the numbers are confirmed,
           then gives the same capacity and price results.
  reading  With reading off: POST /jobs is refused with reading_unavailable.
  job      With reading on: one synthetic page goes through the whole job flow (one model call).
  site     With --site: the page and every file it loads come back from under the site's path,
           config.js names this API, the API's CORS preflight passes for the site's origin, and
           a real presigned S3 POST of one page (no manifest, so nothing is read) succeeds from
           that origin.

Prints PASS or FAIL for each step with status codes, counts and reason codes
only, never document text. Exits 0 when every step passes.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from checks import run_checks  # noqa: E402

SAMPLE = "S2"
SAVED_READING = ROOT / "samples" / "cached" / f"{SAMPLE}.json"
DEFAULT_PAGE = ROOT / ".build" / "samples" / SAMPLE / "page-01.jpg"
COMPARED = ("C1_capacity", "C3_gross_total", "C3_net_cost")
REQUEST_SECONDS = 30
JOB_WAIT_SECONDS = 600


class Failed(Exception):
    pass


def http(method, url, body=None, headers=None):
    """(status, body bytes, headers). Network errors raise Failed with no detail beyond the kind."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_SECONDS) as reply:
            return reply.status, reply.read(), reply.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise Failed(f"{method} {urlsplit(url).path} did not answer ({type(e).__name__})") from None


class Api:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        headers = {} if body is None else {"content-type": "application/json"}
        status, raw, _ = http(method, self.base + path, data, headers)
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {}
        return status, payload if isinstance(payload, dict) else {}

    def job(self, job, suffix=""):
        return f"/jobs/{job['job_id']}{suffix}?t={job['token']}"


def expect(ok, message):
    if not ok:
        raise Failed(message)


def refused(status, body):
    return f"HTTP {status} {body.get('error', '')}".strip()


def saved_quote():
    return json.loads(SAVED_READING.read_text(encoding="utf-8"))["quote"]


def statuses(findings):
    return {f.get("check_id"): f.get("status") for f in findings}


def tokens(findings):
    """The operand sets the checks ask the household to confirm."""
    return [f["confirm_token"] for f in findings if f.get("confirm_token")]


def expected_statuses():
    """What the checks find once the household confirms every operand set as shown."""
    held = run_checks(saved_quote())
    confirmed = run_checks(saved_quote(), {"confirmed_operands": tokens(held["findings"])})
    return {k: v for k, v in statuses(confirmed["findings"]).items() if k in COMPARED}


def value(field):
    """The text or number a quote field holds, as someone would type it."""
    if not isinstance(field, dict):
        return None
    v = field.get("value")
    return v.get("raw") if isinstance(v, dict) else v


def typed_numbers(quote):
    """The manual entry form's fields and answers for a saved reading."""
    group, inverter = quote["module_groups"][0], quote["inverters"][0]
    fields = {
        "stated_capacity": value(quote["stated_capacity"]), "panel_count": value(group["count"]),
        "panel_wattage": value(group["wattage"]), "panel_make_model": value(group["make_model"]),
        "inverter_make_model": value(inverter["make_model"]), "inverter_rating": value(inverter["rating"]),
        "vendor_registration": value(quote["vendor_registration"]),
        "dcr_declaration": value(quote["dcr_declaration"]),
    }
    for name in ("base_price", "gst_amount", "discount", "gross_total", "subsidy_central", "subsidy_state",
                 "subsidy_combined", "subsidy_unspecified", "net_cost"):
        fields[name] = value(quote.get(name))
    fields["panel_count"] = None if fields["panel_count"] is None else str(fields["panel_count"])
    fields["extra_charges"] = [{"label": value(c["label"]), "amount": value(c["amount"]),
                                "included_in_total": value(c["included_in_total"])} for c in quote["extra_charges"]]
    flags = quote["flags"]["model_proposed"]
    answers = {k: value(flags.get(k)) for k in ("capacity_basis", "gst_treatment", "extra_charges_complete",
                                                "net_cost_subsidy_basis")}
    return ({k: v for k, v in fields.items() if v not in (None, [])},
            {k: v for k, v in answers.items() if v is not None})


def wait(api, job, seconds):
    deadline = time.monotonic() + seconds
    while True:
        status, view = api.call("GET", api.job(job))
        expect(status == 200, f"GET /jobs/{{id}} returned {refused(status, view)}")
        if view.get("status") in ("done", "failed") or time.monotonic() > deadline:
            return view
        time.sleep(2)


def step_sample(api, args):
    status, job = api.call("POST", f"/samples/{SAMPLE}")
    expect(status == 201 and job.get("mode") == "saved", f"POST /samples/{SAMPLE} returned {refused(status, job)}")
    view = wait(api, job, 30)
    expect(view.get("status") == "done" and view.get("mode") == "saved", f"the sample job is {view.get('status')}")
    held = [f for f in view.get("findings") or [] if f["status"] in ("consistent", "inconsistent")]
    expect(not held, "a sample check gave a result before its numbers were confirmed")
    path = api.job(job, "/checks")
    status, checked = api.call("POST", path, {"confirmed_operands": tokens(view["findings"])})
    expect(status == 200, f"POST /jobs/{{id}}/checks returned {refused(status, checked)}")
    got = {k: v for k, v in statuses(checked.get("findings") or []).items() if k in COMPARED}
    expect(got == expected_statuses(), f"sample results {got} differ from {expected_statuses()}")
    return f"saved reading with {len(checked['findings'])} findings, held until the numbers were confirmed"


def step_manual(api, args):
    fields, answers = typed_numbers(saved_quote())
    status, result = api.call("POST", "/checks", {"fields": fields, "answers": answers})
    expect(status == 200 and result.get("mode") == "manual", f"POST /checks returned {refused(status, result)}")
    held = [f for f in result.get("findings") or [] if f["status"] in ("consistent", "inconsistent")]
    expect(not held, "a check gave a result before its numbers were confirmed")
    status, result = api.call("POST", "/checks", {"fields": fields, "answers": answers,
                                                  "challenge": result.get("challenge"),
                                                  "confirmed_operands": tokens(result["findings"])})
    expect(status == 200, f"POST /checks with confirmed numbers returned {refused(status, result)}")
    got = {k: v for k, v in statuses(result.get("findings") or []).items() if k in COMPARED}
    expect(got == expected_statuses(), f"typed-in results {got} differ from {expected_statuses()}")
    return f"{len(fields)} typed fields, {len(result['findings'])} findings"


def step_reading_off(api, args):
    status, body = api.call("POST", "/jobs", {"page_count": 1})
    expect(status == 503 and body.get("error") == "reading_unavailable",
           f"POST /jobs returned {refused(status, body)}, not 503 reading_unavailable")
    return "uploads refused with reading_unavailable"


def multipart(fields, data):
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="page"\r\n'
                 "Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def post_form(target, data, what, headers=None):
    body, content_type = multipart(target["fields"], data)
    status, _, reply_headers = http("POST", target["url"], body, {"content-type": content_type, **(headers or {})})
    expect(status in (200, 201, 204), f"uploading the {what} returned HTTP {status}")
    return reply_headers


def page_image(path):
    if path.is_file():
        return path.read_bytes()
    import runpy
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        runpy.run_path(str(ROOT / "scripts" / "render_samples.py"))["render_samples"](Path(tmp))
        return (Path(tmp) / SAMPLE / "page-01.jpg").read_bytes()


def step_job(api, args):
    data = page_image(Path(args.page))
    expect(data[:3] == b"\xff\xd8\xff", "the synthetic page is not a JPEG")
    status, job = api.call("POST", "/jobs", {"page_count": 1})
    expect(status == 201, f"POST /jobs returned {refused(status, job)}")
    post_form(job["uploads"][0], data, "page")
    post_form(job["manifest"], json.dumps(job["manifest_body"]).encode(), "manifest")
    started = time.monotonic()
    view = wait(api, job, args.job_wait)
    seconds = round(time.monotonic() - started)
    expect(view.get("status") == "done",
           f"the job is {view.get('status')} ({view.get('reason') or 'no reason'}) after {seconds} s")
    found = sum(1 for k, v in (view.get("extraction") or {}).items()
                if isinstance(v, dict) and v.get("value") is not None)
    return f"read in about {seconds} s, mode {view.get('mode')}, {found} top-level fields found"


ASSET = re.compile(r'(?:src|href)="([^"#:?]+\.(?:js|css))"')
ASSET_TYPES = {".js": "javascript", ".css": "text/css"}


def step_site(api, args):
    site = args.site.rstrip("/")
    status, page, headers = http("GET", site + "/")
    expect(status == 200 and "text/html" in headers.get("content-type", ""), f"GET {site}/ returned HTTP {status}")
    assets = sorted(set(ASSET.findall(page.decode("utf-8", "replace"))))
    expect("config.js" in assets, "the page does not load config.js")
    for name in assets:
        status, body, headers = http("GET", f"{site}/{name}")
        kind = ASSET_TYPES[Path(name).suffix]
        expect(status == 200 and kind in headers.get("content-type", ""), f"GET {name} returned HTTP {status}")
        if name == "config.js":
            expect(json.dumps(api.base) in body.decode("utf-8", "replace"), "config.js does not name this API")
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(site))
    status, _, headers = http("OPTIONS", api.base + "/checks", None, {
        "origin": origin, "access-control-request-method": "POST", "access-control-request-headers": "content-type"})
    expect(headers.get("access-control-allow-origin") == origin, f"the API does not accept requests from {origin}")
    # One page through a real presigned POST, sent as the browser would from the site. No
    # manifest follows, so nothing is read; the bucket's lifecycle rule removes the page.
    status, job = api.call("POST", "/jobs", {"page_count": 1})
    expect(status == 201, f"POST /jobs returned {refused(status, job)}")
    data = page_image(Path(args.page))
    reply = post_form(job["uploads"][0], data, "page", {"origin": origin})
    expect(reply.get("access-control-allow-origin") == origin, f"the upload bucket does not accept uploads from {origin}")
    return f"page and {len(assets)} files under {urlsplit(site).path or '/'}, CORS and a presigned upload for {origin}"


def steps(args):
    chosen = [("sample", step_sample), ("manual", step_manual)]
    chosen.append(("reading", step_reading_off) if args.reading_engine == "none" else ("job", step_job))
    if args.site:
        chosen.append(("site", step_site))
    return chosen


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api", required=True, help="the stack's ApiUrl output")
    ap.add_argument("--reading-engine", default="none", help="the stack's ReadingEngine parameter")
    ap.add_argument("--site", help="the stack's SiteUrl output, when hosting is on")
    ap.add_argument("--page", default=str(DEFAULT_PAGE), help="a synthetic page JPEG for the job step")
    ap.add_argument("--job-wait", type=int, default=JOB_WAIT_SECONDS, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    api, failed = Api(args.api), 0
    for name, step in steps(args):
        try:
            print(f"PASS {name}: {step(api, args)}")
        except Failed as e:
            failed += 1
            print(f"FAIL {name}: {e}")
    print("All steps passed." if not failed else f"{failed} step(s) failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
