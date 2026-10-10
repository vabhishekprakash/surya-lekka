"""Where each value sits on its page, for highlighting.

For every piece of evidence a reading quotes (a value's lines, a table row, each conflicting
candidate), find the Textract LINE blocks whose text it quotes, or the CELL blocks of the table
row it quotes, and keep only their page number and bounding box (left, top, width, height as
fractions of the page). No text is stored beyond the evidence the reading already holds, and
nothing here changes a value: the boxes sit beside the evidence and the checks never read them.
"""

ROW_SEPARATOR = " | "


def _tidy(text):
    return " ".join(str(text or "").split())


def _box(block):
    b = ((block or {}).get("Geometry") or {}).get("BoundingBox") or {}
    if not all(k in b for k in ("Left", "Top", "Width", "Height")):
        return None
    return [round(float(b[k]), 4) for k in ("Left", "Top", "Width", "Height")]


def _children(block, by_id, kind="CHILD"):
    return [by_id[i] for r in block.get("Relationships") or [] if r.get("Type") == kind
            for i in r.get("Ids") or [] if i in by_id]


def _page_index(reply):
    """(lines {text: [box]}, rows {row text: [cell boxes]}) for one page's reply."""
    blocks = (reply or {}).get("Blocks") or []
    by_id = {b.get("Id"): b for b in blocks}
    lines = {}
    for b in blocks:
        if b.get("BlockType") == "LINE" and (box := _box(b)):
            lines.setdefault(_tidy(b.get("Text")), []).append(box)
    rows = {}
    for table in (b for b in blocks if b.get("BlockType") == "TABLE"):
        cells = {}
        for cell in _children(table, by_id):
            if cell.get("BlockType") != "CELL":
                continue
            words = " ".join(w.get("Text", "") for w in _children(cell, by_id) if w.get("BlockType") == "WORD")
            cells.setdefault(cell.get("RowIndex"), []).append((cell.get("ColumnIndex") or 0, _tidy(words), _box(cell)))
        for row in cells.values():
            row.sort(key=lambda c: c[0])
            text = ROW_SEPARATOR.join(t for _, t, _ in row if t)
            boxes = [b for _, t, b in row if t and b]
            if text and boxes:
                rows.setdefault(text, []).extend(boxes)
    return lines, rows


def boxes_for(evidence, index):
    """The boxes behind one evidence text, or [] when none can be found."""
    lines, rows = index
    text = _tidy(evidence)
    if text in rows:
        return list(rows[text])
    out = []
    for part in str(evidence or "").split("\n"):
        out += lines.get(_tidy(part), [])
    return out


def _evidence(node):
    """Every dict under node that quotes evidence on a page."""
    if isinstance(node, dict):
        if node.get("evidence_text") and node.get("page"):
            yield node
        for v in node.values():
            yield from _evidence(v)
    elif isinstance(node, list):
        for v in node:
            yield from _evidence(v)


def record_boxes(record):
    """[{"page", "evidence", "boxes"}] for one read batch, from its raw reply. Saved with the
    batch so a retry keeps them; the evidence texts are those the batch's contract holds."""
    reply, pages = record.get("response"), record.get("pages") or []
    if not reply or len(pages) != 1:
        return []
    index = _page_index(reply)
    seen, out = set(), []
    for f in _evidence(record.get("contract")):
        key = (f["page"], f["evidence_text"])
        if f["page"] != pages[0] or key in seen:
            continue
        seen.add(key)
        found = boxes_for(f["evidence_text"], index)
        if found:
            out.append({"page": f["page"], "evidence": f["evidence_text"], "boxes": found})
    return out


def attach(quote, found):
    """Add "boxes": [{"page", "box"}] beside each piece of evidence in the merged quote, in place."""
    by_key = {}
    for entry in found:
        by_key.setdefault((entry["page"], entry["evidence"]), []).extend(entry["boxes"])
    for f in _evidence(quote):
        boxes = by_key.get((f["page"], f["evidence_text"]))
        if boxes:
            f["boxes"] = [{"page": f["page"], "box": b} for b in boxes]
    return quote
