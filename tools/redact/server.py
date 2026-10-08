"""Local review UI. Binds to 127.0.0.1 only and serves no external assets."""

import json
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pymupdf

from . import detect
from . import export as exporter
from . import state as review_state
from .config import DPI, HELD_OUT, ORDER, check_doc_id

HOST = "127.0.0.1"
STATIC = Path(__file__).parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

ROUTE_DOC = re.compile(r"^/api/doc/(Q\d{2})$")
ROUTE_PAGE = re.compile(r"^/api/doc/(Q\d{2})/page/(\d+)$")
ROUTE_IMG = re.compile(r"^/api/doc/(Q\d{2})/(original|masked|exported)/(\d+)$")
ROUTE_EXPORT = re.compile(r"^/api/doc/(Q\d{2})/export$")

_lock = threading.Lock()


def doc_summary(paths, doc_id):
    st = review_state.load(paths, doc_id)
    done, total = review_state.progress(st) if st else (0, None)
    return {
        "doc_id": doc_id,
        "held_out": doc_id in HELD_OUT,
        "original_found": paths.original(doc_id) is not None,
        "denylist_found": paths.terms(doc_id).exists(),
        "approved": done,
        "pages": total,
        "exported": paths.output(doc_id).exists(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "redact-review"
    sys_version = ""

    # --- helpers -------------------------------------------------------
    @property
    def paths(self):
        return self.server.paths

    def _origins(self):
        port = self.server.server_address[1]
        return {f"{HOST}:{port}", f"localhost:{port}"}

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status=HTTPStatus.OK):
        self._send(status, json.dumps(data).encode(), "application/json")

    def _error(self, status, msg):
        self._json({"error": msg}, status)

    def _host_ok(self):
        # Reject other Host names (DNS rebinding).
        return self.headers.get("Host") in self._origins()

    def _post_ok(self):
        origin = self.headers.get("Origin", "")
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip()
        return origin in {f"http://{o}" for o in self._origins()} and ctype == "application/json"

    def log_message(self, fmt, *args):
        pass

    # --- routes --------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        path = self.path.split("?")[0]
        if path in STATIC_FILES:
            name, ctype = STATIC_FILES[path]
            return self._send(HTTPStatus.OK, (STATIC / name).read_bytes(), ctype)
        if path == "/api/docs":
            return self._json({"dpi": DPI, "docs": [doc_summary(self.paths, d) for d in ORDER]})
        if m := ROUTE_DOC.match(path):
            return self._with_doc(m.group(1), self._get_doc)
        if m := ROUTE_IMG.match(path):
            return self._with_doc(m.group(1), self._get_image, m.group(2), int(m.group(3)))
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self):
        if not self._host_ok() or not self._post_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad origin")
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(min(length, 1 << 20)) or b"{}")
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "bad json")
        if m := ROUTE_PAGE.match(path):
            return self._with_doc(m.group(1), self._post_page, int(m.group(2)), body)
        if m := ROUTE_EXPORT.match(path):
            return self._with_doc(m.group(1), self._post_export)
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _with_doc(self, doc_id, fn, *args):
        try:
            check_doc_id(doc_id)
        except KeyError:
            return self._error(HTTPStatus.NOT_FOUND, "unknown doc")
        if self.paths.original(doc_id) is None:
            return self._error(HTTPStatus.NOT_FOUND, "original not found")
        with _lock:
            try:
                return fn(doc_id, *args)
            except Exception as e:  # keep tracebacks (and any document detail) out of the console
                return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, type(e).__name__)

    def _get_doc(self, doc_id):
        st = review_state.get_or_build(self.paths, doc_id, use_ocr=self.server.use_ocr)
        self._json(st)

    def _get_image(self, doc_id, which, index):
        st = review_state.load(self.paths, doc_id)
        if st is None or not 0 <= index < len(st["pages"]):
            return self._error(HTTPStatus.NOT_FOUND, "no such page")
        ps = st["pages"][index]
        if which == "exported":
            out = self.paths.output(doc_id)
            if not out.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "not exported yet")
            with pymupdf.open(stream=out.read_bytes(), filetype="pdf") as doc:
                if index >= len(doc):
                    return self._error(HTTPStatus.NOT_FOUND, "no such page")
                pix = doc[index].get_pixmap(dpi=DPI, alpha=False)
                return self._send(HTTPStatus.OK, pix.tobytes("png"), "image/png")
        src, digest = detect.open_source(self.paths.original(doc_id))
        with src:
            if digest != st["source_sha256"] or len(src) != len(st["pages"]):
                return self._error(HTTPStatus.CONFLICT, "original changed; reload")
            if which == "masked":
                pix = exporter.masked_pixmap(src[index], ps)
            else:
                pix, _ = detect.render(src[index], ps["zoom"])
            png = pix.tobytes("png")
        self._send(HTTPStatus.OK, png, "image/png")

    def _post_page(self, doc_id, index, body):
        st = review_state.load(self.paths, doc_id)
        if st is None or not 0 <= index < len(st["pages"]):
            return self._error(HTTPStatus.NOT_FOUND, "no such page")
        if body.get("source_sha256") != st["source_sha256"]:
            return self._error(HTTPStatus.CONFLICT, "review state changed; reload")
        try:
            page = review_state.update_page(st, index, body.get("boxes", []), body.get("approved"))
        except review_state.Rejected as e:
            return self._error(HTTPStatus.CONFLICT, str(e))
        except (KeyError, TypeError, ValueError):
            return self._error(HTTPStatus.BAD_REQUEST, "bad boxes")
        review_state.save(self.paths, doc_id, st)
        done, total = review_state.progress(st)
        self._json({"page": page, "approved": done, "pages": total})

    def _post_export(self, doc_id):
        try:
            entry = exporter.export(self.paths, doc_id)
        except (exporter.NotReady, exporter.CheckFailed) as e:
            return self._error(HTTPStatus.CONFLICT, str(e))
        self._json(entry)


def make_server(paths, port=8770, use_ocr=True):
    srv = ThreadingHTTPServer((HOST, port), Handler)
    srv.paths = paths
    srv.use_ocr = use_ocr
    return srv
