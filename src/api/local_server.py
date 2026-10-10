"""Local dev server: serves web/ and runs the API handlers on localhost with
in-memory S3 and DynamoDB (moto) and the stub extractor, so the whole journey
works offline. Uploaded pages stay in memory on this machine and nothing is
sent to AWS.

    python -m src.api.local_server [--port 8000] [--stub ok|slow|fail-once] [--reading-off]

With --api it serves only web/, with config.js pointing at a deployed API, for
a stack deployed with HostingEnabled=false (that API accepts requests from
http://127.0.0.1:8000 by default):

    python -m src.api.local_server --api <ApiUrl> --region ap-south-1 [--cross-region]

The browser's presigned POSTs are pointed at this server, which checks the
upload policy, stores the object and starts the worker when the manifest
arrives, as the S3 event does in AWS. Needs the dev requirements (moto).
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
ROOT = SRC.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import argparse  # noqa: E402
import base64  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from email.parser import BytesParser  # noqa: E402
from email.policy import HTTP  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402
from urllib.parse import parse_qsl, urlsplit  # noqa: E402

from api import common, jobs, manual, worker  # noqa: E402
from api.site_config import config_js, textract_opted_out  # noqa: E402,F401

WEB = ROOT / "web"
SAVED_READINGS = ROOT / "samples" / "cached"
RENDERED_SAMPLES = ROOT / ".build" / "samples"
REGION, BUCKET, TABLE = "ap-south-1", "surya-lekka-local", "surya-lekka-local-jobs"
UPLOAD_PATH = "/_local_s3"
UPLOAD_MAX_BYTES = 5_000_000
API_MAX_BYTES = 70_000
LAMBDA_TIMEOUT_MS = 900_000
STUB_MODES = ("ok", "slow", "fail-once")
SLOW_SECONDS = 75
ENV = {
    "AWS_ACCESS_KEY_ID": "local", "AWS_SECRET_ACCESS_KEY": "local", "AWS_SESSION_TOKEN": "local",
    "AWS_DEFAULT_REGION": REGION, "AWS_REGION": REGION, "TABLE_NAME": TABLE, "BUCKET_NAME": BUCKET,
    "UPLOADS_ENABLED": "true", "DAILY_JOB_CAP": "200", "IP_DAILY_JOB_CAP": "50", "IP_HASH_KEY": "local",
    "READING_ENGINE": "nova",  # the stub stands in for the model
}
ROUTES = [
    ("POST", re.compile(r"/jobs"), jobs.create_job),
    ("GET", re.compile(r"/jobs/(?P<id>[^/]+)"), jobs.get_job),
    ("POST", re.compile(r"/jobs/(?P<id>[^/]+)/checks"), jobs.recheck),
    ("POST", re.compile(r"/jobs/(?P<id>[^/]+)/retry"), jobs.retry_job),
    ("POST", re.compile(r"/samples/(?P<sample_id>[^/]+)"), jobs.create_sample_job),
    ("POST", re.compile(r"/checks"), manual.handler),
]
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}
LOG = logging.getLogger("surya_lekka.local")


class StubExtractor:
    """The dry-run Nova reply for any pages. mode "slow" waits before each reply;
    "fail-once" answers each job's first call with a throttling error, so the
    retry path can be tried."""

    def __init__(self, mode="ok", delay=1.5):
        self.mode, self.delay = mode, delay
        self._lock = threading.Lock()

    def converse(self, **request):
        from botocore.exceptions import ClientError
        from extract.dryrun import DryRunClient

        time.sleep(SLOW_SECONDS if self.mode == "slow" else self.delay)
        if self.mode == "fail-once" and not threading.current_thread().name.endswith("-retry"):
            raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "stub"}}, "Converse")
        with self._lock:
            return DryRunClient(REGION).converse(**request)


class LambdaContext:
    def __init__(self):
        self.started = time.monotonic()

    def get_remaining_time_in_millis(self):
        return int(LAMBDA_TIMEOUT_MS - (time.monotonic() - self.started) * 1000)


class Backend:
    """moto S3 and DynamoDB in memory, seeded with the saved sample readings."""

    def __init__(self, stub="ok", env=None):
        self.stub = StubExtractor(stub)
        self.env = {**ENV, **(env or {})}
        self._saved_env, self._mock, self._bedrock = {}, None, None

    def start(self):
        import boto3
        from moto import mock_aws

        for k, v in self.env.items():
            self._saved_env[k] = os.environ.get(k)
            os.environ[k] = v
        self._mock = mock_aws()
        self._mock.start()
        common.reset_clients()
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        boto3.client("dynamodb", region_name=REGION).create_table(
            TableName=TABLE, BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}])
        for path in sorted(SAVED_READINGS.glob("S*.json")):
            s3.put_object(Bucket=BUCKET, Key=f"samples/{path.stem}/reading.json", Body=path.read_bytes())
        for folder in sorted(RENDERED_SAMPLES.glob("S*")):  # pages for ?live=1, if rendered
            for f in folder.glob("*"):
                if f.name == "manifest.json" or f.suffix == ".jpg":
                    s3.put_object(Bucket=BUCKET, Key=f"samples/{folder.name}/{f.name}", Body=f.read_bytes())
        self._bedrock = worker.bedrock_client
        worker.bedrock_client = lambda: self.stub
        return self

    def stop(self):
        worker.bedrock_client = self._bedrock
        self._mock.stop()
        common.reset_clients()
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_worker(self, job_id, retry=False):
        """What the manifest's S3 event does in AWS: run the worker in the background."""
        event = {"Records": [{"s3": {"bucket": {"name": BUCKET},
                                     "object": {"key": common.manifest_key(job_id)}}}]}

        def run():
            try:
                worker.handler(event, LambdaContext())
            except Exception:
                LOG.warning("worker stopped with an error")  # the worker has already logged a code

        name = f"worker-{job_id}-{time.monotonic_ns()}" + ("-retry" if retry else "")
        thread = threading.Thread(target=run, name=name, daemon=True)
        thread.start()
        return thread


def check_policy(fields, size):
    """The presigned POST policy's conditions, as S3 checks them. Returns an error or None."""
    try:
        policy = json.loads(base64.b64decode(fields.get("policy", "")))
    except ValueError:
        return "bad policy"
    expires = datetime.fromisoformat(policy.get("expiration", "").replace("Z", "+00:00"))
    if expires < datetime.now(timezone.utc):
        return "policy expired"
    for condition in policy.get("conditions", []):
        if isinstance(condition, list) and condition[0] == "content-length-range":
            if not condition[1] <= size <= condition[2]:
                return "file size outside the allowed range"
        elif isinstance(condition, dict):
            for name, value in condition.items():
                if name != "bucket" and fields.get(name) != value:
                    return f"{name} does not match the policy"
    return None


def parse_form(content_type, body):
    """multipart/form-data -> {name: text} and the file's bytes."""
    message = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    fields, data = {}, None
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name == "file":
            data = part.get_payload(decode=True)
        elif name:
            fields[name] = part.get_payload(decode=True).decode("utf-8")
    return fields, data


def opt_out_from_file(path):
    """The Textract opt-out from a saved describe-effective-policy reply; False without one."""
    if not path:
        return False
    try:
        return textract_opted_out(json.loads(Path(path).read_text(encoding="utf-8-sig")))
    except (OSError, ValueError):
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "SuryaLekkaLocal"
    backend = None  # set by make_server; None serves web/ only
    config = None  # config.js to serve in place of web/config.js

    def log_message(self, fmt, *args):  # the path only, never the query (it holds job tokens)
        LOG.info("%s %s", self.command, urlsplit(self.path).path)

    def _send(self, status, body, content_type="application/json", headers=None):
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        for k, v in (headers or {}).items():
            if k.lower() not in ("content-type", "cache-control"):
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self, limit):
        length = int(self.headers.get("content-length") or 0)
        if length > limit:
            return None
        return self.rfile.read(length)

    def do_GET(self):
        (self.backend and self._dispatch("GET")) or self._static()

    def do_POST(self):
        if self.backend and urlsplit(self.path).path == UPLOAD_PATH:
            return self._upload()
        if not (self.backend and self._dispatch("POST")):
            self._body(API_MAX_BYTES)  # read the unused body first, or Windows may abort the connection
            self._send(404, b'{"error": "not_found"}')

    def _static(self):
        path = urlsplit(self.path).path
        if path == "/config.js" and self.config is not None:
            return self._send(200, self.config, TYPES[".js"])
        target = (WEB / (path.lstrip("/") + ("index.html" if path.endswith("/") else ""))).resolve()
        if WEB.resolve() not in target.parents or not target.is_file():
            return self._send(404, b"Not found", "text/plain; charset=utf-8")
        self._send(200, target.read_bytes(), TYPES.get(target.suffix, "application/octet-stream"))

    def _dispatch(self, method):
        url = urlsplit(self.path)
        for route_method, pattern, handler in ROUTES:
            match = pattern.fullmatch(url.path)
            if route_method == method and match:
                break
        else:
            return False
        body = self._body(API_MAX_BYTES)
        if body is None:
            self._send(413, b'{"error": "body_too_large"}')
            return True
        event = {"body": body.decode("utf-8", "replace") if body else None, "isBase64Encoded": False,
                 "pathParameters": match.groupdict() or None,
                 "queryStringParameters": dict(parse_qsl(url.query)) or None,
                 "requestContext": {"http": {"method": method, "path": url.path, "sourceIp": self.client_address[0]}}}
        result = handler(event, None)
        status, payload = result["statusCode"], json.loads(result["body"])
        if handler is jobs.create_job and status == 201:
            upload_url = f"http://{self.headers.get('host')}{UPLOAD_PATH}"
            for target in payload["uploads"] + [payload["manifest"]]:
                target["url"] = upload_url
        if status in (201, 202) and handler in (jobs.retry_job, jobs.create_sample_job) \
                and payload.get("mode", "nova") == "nova":
            self.backend.run_worker(payload["job_id"], retry=handler is jobs.retry_job)
        if payload.get("mode") == "nova":  # the stub read it, so the page must not say Amazon Nova did
            payload["mode"] = "stub"
        self._send(status, json.dumps(payload).encode("utf-8"), headers=result.get("headers"))
        return True

    def _upload(self):
        body = self._body(UPLOAD_MAX_BYTES)
        if body is None:
            return self._send(413, b"<Error><Code>EntityTooLarge</Code></Error>", "application/xml")
        try:
            fields, data = parse_form(self.headers.get("content-type", ""), body)
        except Exception:
            return self._send(400, b"<Error><Code>MalformedPOSTRequest</Code></Error>", "application/xml")
        problem = "no file" if data is None else check_policy(fields, len(data))
        if problem:
            LOG.info("upload refused: %s", problem)
            return self._send(403, b"<Error><Code>AccessDenied</Code></Error>", "application/xml")
        key = fields["key"]
        common.s3().put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=fields.get("Content-Type", ""))
        match = worker.MANIFEST.fullmatch(key)
        if match:
            self.backend.run_worker(match.group(1))
        self._send(204, b"")


def make_server(port=8000, stub="ok", env=None):
    """(server, backend). Call backend.stop() after server.shutdown()."""
    backend = Backend(stub, env).start()
    handler = type("BoundHandler", (Handler,), {"backend": backend})
    return ThreadingHTTPServer(("127.0.0.1", port), handler), backend


def make_site_server(port, api_base, region, cross_region=False, engine="none", ai_opt_out=False):
    """Serves web/ only, with config.js pointing at a deployed API. No local backend."""
    if not re.fullmatch(r"https://[a-z0-9.-]+(/[A-Za-z0-9._-]+)*", api_base.rstrip("/")):
        raise ValueError("--api must be the stack's https ApiUrl")
    config = config_js(api_base.rstrip("/"), region, cross_region, engine, ai_opt_out).encode("utf-8")
    handler = type("SiteHandler", (Handler,), {"backend": None, "config": config})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def serve_site(args):
    server = make_site_server(args.port, args.api, args.region, args.cross_region, args.engine,
                              opt_out_from_file(args.opt_out_reply))
    print(f"Surya Lekka web app on http://127.0.0.1:{server.server_address[1]}/ using {args.api.rstrip('/')}")
    print("Pages you upload go to that deployed stack. Ctrl+C stops this server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--stub", choices=STUB_MODES, default="ok",
                    help="ok: read every quote as the dry-run sample; slow: wait 75 s per call; "
                         "fail-once: fail each job's first reading so retry can be tried")
    ap.add_argument("--daily-cap", type=int, help="DAILY_JOB_CAP for this run (0 shows the limit message)")
    ap.add_argument("--reading-off", action="store_true",
                    help="run as a stack deployed with ReadingEngine=none: uploads are refused")
    ap.add_argument("--api", help="serve web/ only, against this deployed API (the stack's ApiUrl)")
    ap.add_argument("--region", default="ap-south-1", help="with --api: the stack's Region")
    ap.add_argument("--cross-region", action="store_true",
                    help="with --api: the stack reads with a cross-Region inference profile")
    ap.add_argument("--engine", choices=("none", "nova", "textract"), default="none",
                    help="with --api: the stack's ReadingEngine")
    ap.add_argument("--opt-out-reply", help="with --api and textract: the saved AI services opt-out policy reply")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("botocore").setLevel(logging.WARNING)
    if args.api:
        return serve_site(args)
    env = {} if args.daily_cap is None else {"DAILY_JOB_CAP": str(args.daily_cap)}
    if args.reading_off:
        env["READING_ENGINE"] = "none"
    server, backend = make_server(args.port, args.stub, env)
    print(f"Surya Lekka local server on http://127.0.0.1:{server.server_address[1]}/")
    print("In-memory storage and the stub extractor: uploaded pages stay in this process. Ctrl+C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        backend.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
