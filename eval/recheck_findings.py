"""Recompute labelled findings from their operands, sharing no code with the app.

    python eval/recheck_findings.py ADJUDICATION.csv [--set dev|held-out] [--show-values] [--text-dir DIR ...]

Reads an adjudication CSV (one row per operand of a finding) and works each finding out again
from the rules as written below, then reports agree / disagree counts per check. Values are
printed only with --show-values (development quotes). With --text-dir, it also reports whether
each operand's number appears in the quote's own text: the PDF text layer of DIR/<quote>.pdf
and the LINE blocks of any saved Textract responses under the other directories. It never
prints document text.

Rules (MNRE CFA guidelines, general category state such as Telangana):
  capacity  sum of panel count x panel watts / 1000 over panel groups, against the stated DC kWp,
            matching within 0.01 kWp
  subsidy   Rs 30,000 per kWp up to 2 kWp, then Rs 18,000 per kWp from 2 to 3 kWp, at most
            Rs 78,000 (special category: 33,000 / 19,800 / 85,800), matching within Rs 1
  net cost  total minus the subsidy the quote says it deducts, against the stated net cost,
            within Rs 1
  total     base + GST when excluded + charges inside the total - discount, against the stated
            total, within Rs 1
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

RATES = {"general": (Decimal(30000), Decimal(18000), Decimal(78000)),
         "special": (Decimal(33000), Decimal(19800), Decimal(85800))}
KWP_TOLERANCE = Decimal("0.01")
RUPEE_TOLERANCE = Decimal(1)
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def number(raw):
    """A Decimal from an operand cell, or None (a cell may hold {'raw': ..., 'parse_status': ...})."""
    text = raw.strip()
    if text.startswith("{"):
        match = re.search(r"'raw': '([^']*)'", text)
        text = match.group(1) if match else ""
    text = text.replace(",", "").replace("₹", "").strip()
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def subsidy(kwp, category="general"):
    first, second, cap = RATES[category]
    amount = first * min(kwp, Decimal(2)) + second * min(max(kwp - 2, Decimal(0)), Decimal(1))
    return min(amount, cap)


def panels_kwp(ops):
    groups = sorted({int(m.group(1)) for k in ops for m in [re.match(r"module_groups\[(\d+)\]\.", k)] if m})
    total = Decimal(0)
    for g in groups:
        count, watts = ops.get(f"module_groups[{g}].count"), ops.get(f"module_groups[{g}].wattage_w")
        if count is None or watts is None:
            return None
        total += count * watts / 1000
    return total if groups else None


def recompute(check, ops, stated_basis):
    """'consistent' or 'inconsistent' from the rules as written, with the numbers used."""
    if check == "C1_capacity":
        kwp, stated = panels_kwp(ops), ops["stated_capacity_kw"]
        return ("consistent" if abs(kwp - stated) <= KWP_TOLERANCE else "inconsistent"), {"panels_kwp": kwp,
                                                                                        "stated_kwp": stated}
    if check == "C2_central_subsidy":
        kwp = ops["stated_capacity_kw"] if stated_basis else panels_kwp(ops)
        rule = subsidy(kwp)
        return ("consistent" if abs(rule - ops["subsidy_central"]) <= RUPEE_TOLERANCE else "inconsistent"), {
            "kwp": kwp, "rule": rule, "stated": ops["subsidy_central"]}
    if check == "C3_net_cost":
        net = ops["gross_total"] - ops["subsidy_central"]
        return ("consistent" if abs(net - ops["net_cost"]) <= RUPEE_TOLERANCE else "inconsistent"), {
            "computed": net, "stated": ops["net_cost"]}
    if check == "C3_gross_total":
        gst = ops.get("gst_amount", Decimal(0)) if ops.get("gst_excluded", Decimal(1)) else Decimal(0)
        total = ops["base_price"] + gst + ops.get("charges_inside", Decimal(0)) - ops.get("discount", Decimal(0))
        return ("consistent" if abs(total - ops["gross_total"]) <= RUPEE_TOLERANCE else "inconsistent"), {
            "computed": total, "stated": ops["gross_total"]}
    raise ValueError(check)


def findings(path, only_set=None):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    grouped = defaultdict(lambda: {"ops": {}, "raw": {}, "status": None, "stated_basis": False})
    for row in rows:
        if only_set and row.get("set", "dev") != only_set:
            continue
        key = (row.get("set", "dev"), row["doc"], row["check_id"])
        f = grouped[key]
        f["status"] = row["status"]
        if row["operand"].startswith("computed:"):
            f["stated_basis"] |= "stated DC capacity" in row["operand"]
            continue
        f["ops"][row["operand"]] = number(row["value"])
        f["raw"][row["operand"]] = row["value"]
    return grouped


def quote_numbers(doc, text_dirs):
    """Every number in a quote's PDF text layer and saved Textract lines (never printed)."""
    texts = []
    for d in text_dirs:
        pdf = Path(d) / f"{doc}.pdf"
        if pdf.is_file():
            import pymupdf  # local tool only
            with pymupdf.open(pdf) as document:
                texts += [page.get_text() for page in document]
        for batch in Path(d).glob(f"**/{doc}/batch-*.json"):
            blocks = json.loads(batch.read_text(encoding="utf-8"))["response"]["Blocks"]
            texts += [b.get("Text", "") for b in blocks if b.get("BlockType") == "LINE"]
    found = set()
    for text in texts:
        for token in NUMBER.findall(text):
            try:
                found.add(Decimal(token.replace(",", "")))
            except InvalidOperation:
                pass
    return found


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("csv")
    parser.add_argument("--set", dest="only_set")
    parser.add_argument("--show-values", action="store_true")
    parser.add_argument("--text-dir", action="append", default=[])
    args = parser.parse_args(argv)
    grouped = findings(args.csv, args.only_set)
    tally = defaultdict(Counter)
    disagreements = []
    for (part, doc, check), f in sorted(grouped.items()):
        status, used = recompute(check, f["ops"], f["stated_basis"])
        agree = status == f["status"]
        tally[(part, check)]["agree" if agree else "disagree"] += 1
        if not agree:
            disagreements.append((part, doc, check, status, f["status"], used))
        if args.show_values and check == "C1_capacity" and status == "inconsistent":
            kwp, stated = used["panels_kwp"], used["stated_kwp"]
            print(f"{doc} C1 doesn't match: panels {kwp} kWp, stated {stated} kWp, difference {abs(kwp - stated)} kWp, "
                  f"{'panels' if kwp > stated else 'stated'} larger; central subsidy (Telangana) "
                  f"Rs {subsidy(kwp)} at {kwp} kWp, Rs {subsidy(stated)} at {stated} kWp, "
                  f"difference Rs {abs(subsidy(kwp) - subsidy(stated))}")
    print(f"{args.csv}: {len(grouped)} findings")
    for (part, check), c in sorted(tally.items()):
        print(f"  {part} {check}: agree {c['agree']}, disagree {c['disagree']}")
    for part, doc, check, mine, theirs, used in disagreements:
        detail = f" recomputed {mine}, file {theirs}, numbers {used}" if args.show_values else ""
        print(f"  DISAGREE {part} {doc} {check}{detail}")
    if args.text_dir:
        counts = Counter()
        missing = []
        cache = {}
        for (part, doc, check), f in sorted(grouped.items()):
            numbers = cache.setdefault(doc, quote_numbers(doc, args.text_dir))
            for operand, value in f["ops"].items():
                if value is None:
                    counts["not a number"] += 1
                    missing.append((doc, check, operand, "not a number"))
                elif value in numbers:
                    counts["found"] += 1
                else:
                    counts["not found"] += 1
                    missing.append((doc, check, operand, "not found"))
        print(f"  operands in the quote's own text: {dict(counts)}")
        for doc, check, operand, why in missing:
            print(f"    {why}: {doc} {check} {operand}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
