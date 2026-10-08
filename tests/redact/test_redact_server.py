import http.client
import json
import threading

import pytest

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("cv2")

from redact_synth import NAME, QUOTE_ITEMS, paths, pdf_bytes, root  # noqa: E402,F401
from tools.redact.server import HOST, STATIC, make_server  # noqa: E402


@pytest.fixture
def server(paths):
    (paths.originals / "Q10.pdf").write_bytes(pdf_bytes(QUOTE_ITEMS))
    paths.terms("Q10").write_text(NAME + "\n", encoding="utf-8")
    srv = make_server(paths, 0, use_ocr=False)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=None, host=None, origin=True):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection(HOST, port, timeout=60)
    headers = {"Host": host or f"{HOST}:{port}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = f"http://{HOST}:{port}"
    conn.request(method, path, body=data, headers=headers)
    res = conn.getresponse()
    return res.status, res.getheader("Content-Type"), res.read()


def test_binds_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"


def test_static_assets_are_local():
    for f in STATIC.iterdir():
        text = f.read_text(encoding="utf-8")
        assert "http://" not in text and "https://" not in text and "//cdn" not in text


def test_rejects_foreign_host_and_origin(server):
    assert call(server, "GET", "/api/docs", host="evil.example:80")[0] == 403
    assert call(server, "POST", "/api/doc/Q10/page/0", {"boxes": []}, origin=False)[0] == 403


def test_review_round_trip(server, paths):
    status, _, body = call(server, "GET", "/api/docs")
    docs = {d["doc_id"]: d for d in json.loads(body)["docs"]}
    assert status == 200 and docs["Q10"]["original_found"] and not docs["Q11"]["original_found"]

    status, _, body = call(server, "GET", "/api/doc/Q10")
    st = json.loads(body)
    assert status == 200 and st["pages"][0]["boxes"]

    for which in ("original", "masked"):
        status, ctype, img = call(server, "GET", f"/api/doc/Q10/{which}/0")
        assert status == 200 and ctype == "image/png" and img[:4] == b"\x89PNG"
    assert call(server, "GET", "/api/doc/Q10/exported/0")[0] == 404

    page = st["pages"][0]
    status, _, body = call(server, "POST", "/api/doc/Q10/page/0",
                           {"source_sha256": st["source_sha256"], "boxes": page["boxes"], "approved": True})
    assert status == 200 and json.loads(body)["approved"] == 1
    # Saved to the review folder straight away.
    assert json.loads(paths.state("Q10").read_text(encoding="utf-8"))["pages"][0]["approved"]

    status, _, body = call(server, "POST", "/api/doc/Q10/export", {})
    assert status == 200 and json.loads(body)["image_only"] is True
    assert call(server, "GET", "/api/doc/Q10/exported/0")[0] == 200


def test_unknown_or_skipped_doc(server):
    assert call(server, "GET", "/api/doc/Q12")[0] == 404
    assert call(server, "GET", "/api/doc/Q99")[0] == 404
