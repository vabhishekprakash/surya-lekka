"""Split rendered pages into model calls.

At most MAX_IMAGES_PER_CALL images per call, and every request must fit the
size limit once serialised, with image bytes counted as base64.
"""

MAX_IMAGES_PER_CALL = 5
# Our own ceiling for one serialised request. Kept below the 25 MB payload limit
# we understand Nova to have; that limit could not be confirmed offline.
DEFAULT_REQUEST_LIMIT_BYTES = 20_000_000


def plan_batches(pages, size_of, limit=DEFAULT_REQUEST_LIMIT_BYTES, max_images=MAX_IMAGES_PER_CALL):
    """(batches, rejected_page_numbers). size_of(list_of_pages) gives the serialised
    request size. Pages keep their order; a page too large to send alone is rejected."""
    batches, rejected, current = [], [], []
    for page in pages:
        candidate = current + [page]
        if len(candidate) <= max_images and size_of(candidate) <= limit:
            current = candidate
            continue
        if current:
            batches.append(current)
        if size_of([page]) <= limit:
            current = [page]
        else:
            rejected.append(page.page)
            current = []
    if current:
        batches.append(current)
    return batches, rejected
