import pytest

pymupdf = pytest.importorskip("pymupdf")

from extract.batching import DEFAULT_REQUEST_LIMIT_BYTES, plan_batches
from extract.nova_client import build_request, request_size
from extract.render import MAX_IMAGE_BYTES, MAX_PAGES, MAX_SIDE_PX, render_document


def make_pdf(path, sizes):
    doc = pymupdf.open()
    for n, (w, h) in enumerate(sizes, 1):
        page = doc.new_page(width=w, height=h)
        for row in range(40):
            page.insert_text((20, 20 + row * 14), f"Synthetic page {n} line {row} 0123456789 " * 3, fontsize=9)
    doc.save(path)
    doc.close()
    return path


def test_pages_numbered_from_one_and_within_limits(tmp_path):
    path = make_pdf(tmp_path / "s.pdf", [(595, 842), (595, 842), (6000, 4000)])
    pages, skipped, count = render_document(path)
    assert [p.page for p in pages] == [1, 2, 3] and skipped == [] and count == 3
    for p in pages:
        assert p.jpeg[:2] == b"\xff\xd8" and len(p.jpeg) <= MAX_IMAGE_BYTES
        assert max(p.width, p.height) <= MAX_SIDE_PX
    assert pages[0].dpi == 150 and pages[0].width == round(595 * 150 / 72)
    assert pages[2].dpi < 150  # 6000 pt wide at 150 DPI would be 12500 px


def test_pages_past_the_limit_are_skipped(tmp_path):
    path = make_pdf(tmp_path / "s.pdf", [(595, 842)] * 4)
    pages, skipped, count = render_document(path, max_pages=2)
    assert [p.page for p in pages] == [1, 2] and skipped == [3, 4] and count == 4


def test_thirteen_pages_all_rendered_in_three_calls(tmp_path):
    assert MAX_PAGES == 20 and DEFAULT_REQUEST_LIMIT_BYTES == 20_000_000
    path = make_pdf(tmp_path / "s.pdf", [(595, 842)] * 13)
    pages, skipped, count = render_document(path)
    assert [p.page for p in pages] == list(range(1, 14)) and skipped == [] and count == 13

    def size_of(batch):
        return request_size(build_request("global.amazon.nova-2-lite-v1:0", batch))
    batches, rejected = plan_batches(pages, size_of)
    assert [[p.page for p in b] for b in batches] == [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10], [11, 12, 13]]
    assert rejected == [] and all(size_of(b) <= 20_000_000 for b in batches)


def test_pages_past_twenty_are_skipped(tmp_path):
    path = make_pdf(tmp_path / "s.pdf", [(300, 200)] * 22)
    pages, skipped, count = render_document(path)
    assert len(pages) == 20 and skipped == [21, 22] and count == 22


def test_large_pages_are_shrunk_or_skipped(tmp_path):
    path = make_pdf(tmp_path / "s.pdf", [(595, 842)])
    (full,), _, _ = render_document(path)
    (small,), _, _ = render_document(path, max_bytes=len(full.jpeg) - 1)
    assert len(small.jpeg) < len(full.jpeg) and (small.quality < full.quality or small.dpi < full.dpi)
    pages, skipped, count = render_document(path, max_bytes=10)
    assert pages == [] and skipped == [1] and count == 1


def test_image_input(tmp_path):
    pdf = make_pdf(tmp_path / "s.pdf", [(300, 200)])
    with pymupdf.open(pdf) as doc:
        doc[0].get_pixmap().save(tmp_path / "s.png")
    pages, skipped, count = render_document(tmp_path / "s.png")
    assert [p.page for p in pages] == [1] and skipped == [] and count == 1
