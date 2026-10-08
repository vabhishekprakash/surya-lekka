"""Does a field's evidence text appear on the PDF text layer of its page?

The caller supplies page_texts: a mapping of page number (1-based) to that
page's text layer, or a list where index 0 is page 1. Use None or "" for a
page with no text layer (a scan or photo). Matching ignores case, whitespace
and dash or quote variants; nothing else is fuzzy.
"""

import re
import unicodedata

TEXT_MATCHED = "text_matched"
NOT_MACHINE_VERIFIED = "not_machine_verified"
MISMATCH = "mismatch"
EVIDENCE_STATUSES = (TEXT_MATCHED, NOT_MACHINE_VERIFIED, MISMATCH)

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {**dict.fromkeys(map(ord, "‘’‚′"), "'"),
           **dict.fromkeys(map(ord, "“”„″"), '"')}
_INVISIBLE = re.compile("[­​-‍⁠﻿]")


def normalise_text(text):
    t = unicodedata.normalize("NFKC", str(text))
    t = _INVISIBLE.sub("", t).translate(_DASHES).translate(_QUOTES)
    return "".join(t.casefold().split())


def _page_text(page_texts, page):
    try:
        page = int(page)
    except (TypeError, ValueError):
        return None
    if isinstance(page_texts, dict):
        return page_texts.get(page, page_texts.get(str(page)))
    if isinstance(page_texts, (list, tuple)) and 1 <= page <= len(page_texts):
        return page_texts[page - 1]
    return None


def verify_evidence(field, page_texts):
    """text_matched, not_machine_verified (nothing to check against, or no
    text layer on that page) or mismatch (text layer present, evidence absent)."""
    if not field:
        return NOT_MACHINE_VERIFIED
    evidence = field.get("evidence_text")
    if not evidence or not normalise_text(evidence):
        return NOT_MACHINE_VERIFIED
    page_text = _page_text(page_texts, field.get("page"))
    if page_text is None or not normalise_text(page_text):
        return NOT_MACHINE_VERIFIED
    return TEXT_MATCHED if normalise_text(evidence) in normalise_text(page_text) else MISMATCH
