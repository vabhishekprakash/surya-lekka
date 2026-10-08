"""PDF (or image) pages to JPEG images for the model, with PyMuPDF.

Local use only: the deployed app renders pages in the browser. Page numbers
are the original 1-based page numbers, so evidence can point back to them.
"""

from dataclasses import dataclass

MAX_IMAGE_BYTES = 3_750_000
MAX_SIDE_PX = 8000
MAX_PAGES = 8
DEFAULT_DPI = 150
QUALITIES = (85, 75, 65, 55)
MIN_DPI = 72


@dataclass(frozen=True)
class PageImage:
    page: int  # original 1-based page number
    jpeg: bytes
    width: int
    height: int
    dpi: int
    quality: int


def _render_page(page, dpi, max_bytes, max_side):
    import pymupdf

    while dpi >= MIN_DPI:
        zoom = dpi / 72
        longest = max(page.rect.width, page.rect.height) * zoom
        if longest > max_side:
            zoom *= max_side / longest
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, colorspace=pymupdf.csRGB)
        while pix.width > max_side or pix.height > max_side:  # rounding can add a pixel
            zoom *= 0.999
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, colorspace=pymupdf.csRGB)
        for quality in QUALITIES:
            data = pix.tobytes(output="jpeg", jpg_quality=quality)
            if len(data) <= max_bytes:
                return PageImage(page.number + 1, data, pix.width, pix.height, round(zoom * 72), quality)
        dpi = int(dpi * 0.8)
    return None


def render_document(path, dpi=DEFAULT_DPI, max_pages=MAX_PAGES, max_bytes=MAX_IMAGE_BYTES,
                    max_side=MAX_SIDE_PX):
    """(pages, skipped_page_numbers). A page is skipped when it is past max_pages or
    cannot be brought under max_bytes even at low resolution."""
    import pymupdf

    pages, skipped = [], []
    with pymupdf.open(path) as doc:
        for index in range(doc.page_count):
            if index >= max_pages:
                skipped.append(index + 1)
                continue
            image = _render_page(doc[index], dpi, max_bytes, max_side)
            if image is None:
                skipped.append(index + 1)
            else:
                pages.append(image)
    return pages, skipped
