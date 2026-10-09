"""Handmade Textract AnalyzeDocument replies for the mapping tests: LINE, QUERY,
QUERY_RESULT, TABLE, CELL, MERGED_CELL and WORD blocks with only the fields the
mapping reads."""


def box(left, top, width, height=0.02):
    return {"BoundingBox": {"Left": left, "Top": top, "Width": width, "Height": height}}


class Page:
    def __init__(self):
        self.blocks = [{"BlockType": "PAGE", "Id": "page", "Page": 1}]
        self.count = 0

    def _id(self):
        self.count += 1
        return f"b{self.count}"

    def _add(self, block):
        block.setdefault("Id", self._id())
        block.setdefault("Page", 1)
        self.blocks.append(block)
        return block["Id"]

    def line(self, text, top, left=0.05, width=0.8):
        return self._add({"BlockType": "LINE", "Text": text, "Confidence": 99.0, "Geometry": box(left, top, width)})

    def answer(self, alias, text, confidence=95.0, top=None, left=0.5, width=0.2, question="question"):
        """A query and its answer. With top=None the answer has no box."""
        result = {"BlockType": "QUERY_RESULT", "Text": text, "Confidence": confidence}
        if top is not None:
            result["Geometry"] = box(left, top, width)
        rid = self._add(result)
        return self._add({"BlockType": "QUERY", "Query": {"Text": question, "Alias": alias},
                          "Relationships": [{"Type": "ANSWER", "Ids": [rid]}]})

    def answers(self, alias, *results):
        """A query with several answers: (text, confidence, top) each."""
        ids = [self._add({"BlockType": "QUERY_RESULT", "Text": t, "Confidence": c, "Geometry": box(0.5, top, 0.2)})
               for t, c, top in results]
        return self._add({"BlockType": "QUERY", "Query": {"Text": "question", "Alias": alias},
                          "Relationships": [{"Type": "ANSWER", "Ids": ids}]})

    def unanswered(self, alias):
        return self._add({"BlockType": "QUERY", "Query": {"Text": "question", "Alias": alias}})

    def table(self, rows, header_rows=1, merged=(), confidence=None):
        """rows: lists of cell texts. merged: (row, col, row_span, col_span) groups,
        1-based; the merged text is the covered cells' texts in order. confidence:
        {(row, col): value} for cells read with less than the usual 90."""
        cell_ids, cells = [], {}
        for r, row in enumerate(rows, 1):
            for c, text in enumerate(row, 1):
                words = [self._add({"BlockType": "WORD", "Text": w, "Confidence": 99.0}) for w in str(text).split()]
                block = {"BlockType": "CELL", "RowIndex": r, "ColumnIndex": c, "RowSpan": 1, "ColumnSpan": 1,
                         "Confidence": (confidence or {}).get((r, c), 90.0)}
                if words:
                    block["Relationships"] = [{"Type": "CHILD", "Ids": words}]
                if r <= header_rows:
                    block["EntityTypes"] = ["COLUMN_HEADER"]
                cells[(r, c)] = self._add(block)
                cell_ids.append(cells[(r, c)])
        merged_ids = []
        for r, c, rs, cs in merged:
            children = [cells[(rr, cc)] for rr in range(r, r + rs) for cc in range(c, c + cs)]
            merged_ids.append(self._add({"BlockType": "MERGED_CELL", "RowIndex": r, "ColumnIndex": c,
                                         "RowSpan": rs, "ColumnSpan": cs,
                                         "Relationships": [{"Type": "CHILD", "Ids": children}]}))
        relationships = [{"Type": "CHILD", "Ids": cell_ids}]
        if merged_ids:
            relationships.append({"Type": "MERGED_CELL", "Ids": merged_ids})
        return self._add({"BlockType": "TABLE", "Relationships": relationships})

    def form(self, key, value, top, confidence=95.0):
        """A FORMS key-value pair: KEY and VALUE blocks with their words. Add the page's
        own LINE for it separately, as Textract does."""
        def words(text):
            return [self._add({"BlockType": "WORD", "Text": w, "Confidence": 99.0}) for w in text.split()]
        value_id = self._add({"BlockType": "KEY_VALUE_SET", "EntityTypes": ["VALUE"], "Confidence": confidence,
                              "Geometry": box(0.5, top, 0.2), "Relationships": [{"Type": "CHILD", "Ids": words(value)}]})
        return self._add({"BlockType": "KEY_VALUE_SET", "EntityTypes": ["KEY"], "Confidence": confidence,
                          "Geometry": box(0.05, top, 0.4),
                          "Relationships": [{"Type": "VALUE", "Ids": [value_id]}, {"Type": "CHILD", "Ids": words(key)}]})

    def reply(self):
        return {"DocumentMetadata": {"Pages": 1}, "Blocks": self.blocks}


def expense(summary=(), items=(), confidence=95.0):
    """A handmade AnalyzeExpense reply for one page. summary: (type, label, value, top);
    items: rows of (type, value, top)."""
    def detection(text, top):
        return {"Text": text, "Confidence": confidence, "Geometry": box(0.5, top, 0.2)}
    fields = [{"Type": {"Text": t, "Confidence": confidence}, "PageNumber": 1,
               **({"LabelDetection": {"Text": label, "Confidence": confidence}} if label else {}),
               "ValueDetection": detection(value, top)} for t, label, value, top in summary]
    groups = [{"LineItems": [{"LineItemExpenseFields": [
        {"Type": {"Text": t, "Confidence": confidence}, "PageNumber": 1, "ValueDetection": detection(v, top)}
        for t, v, top in row]} for row in items]}] if items else []
    return {"DocumentMetadata": {"Pages": 1},
            "ExpenseDocuments": [{"ExpenseIndex": 1, "SummaryFields": fields, "LineItemGroups": groups}]}
