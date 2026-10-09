"""C3: price, GST, extras, discount, subsidy and net cost arithmetic.

All amounts are already-parsed INR values. A missing required amount is never
treated as zero. A discount is optional: when the quote states none, it is
left out of the relation rather than shown as a computed zero.
"""

from decimal import Decimal

from .common import (
    CONSISTENT,
    INCONSISTENT,
    MISSING,
    NEEDS_CONFIRMATION,
    UnusableNumber,
    computed,
    field_words,
    finding,
    format_inr,
    join_words,
    options_finding,
    options_unresolved,
    quoted,
    rupees,
    to_decimal,
    value_of,
)

GROSS_CHECK_ID = "C3_gross_total"
NET_CHECK_ID = "C3_net_cost"

# Allowed rounding difference between computed and stated amounts.
TOLERANCE_INR = Decimal("1")

# Which stated subsidy fields the stated net cost deducts, per net_cost_subsidy_basis.
SUBSIDY_FIELDS = {
    "none": (),
    "central": ("subsidy_central",),
    "state": ("subsidy_state",),
    "central_and_state": ("subsidy_central", "subsidy_state"),
    "combined": ("subsidy_combined",),
}


class _Operands:
    """Collects amounts and their evidence; records missing and unusable fields."""

    def __init__(self, quote):
        self.quote = quote
        self.evidence, self.missing, self.unusable = [], [], []

    def take(self, name, field):
        if field is not None:
            self.evidence.append(quoted(name, field))
        try:
            amount = to_decimal(value_of(field))
        except UnusableNumber:
            self.unusable.append(name)
            return None
        if amount is None:
            self.missing.append(name)
        elif amount < 0:
            self.unusable.append(name)
            return None
        return amount

    def words(self, names):
        return join_words(field_words(n, self.quote) for n in names)

    def blocked(self, check_id):
        if self.missing:
            return finding(check_id, MISSING, f"Not found on the quote: {self.words(self.missing)}.", self.evidence)
        if self.unusable:
            return finding(
                check_id,
                NEEDS_CONFIRMATION,
                f"Please check {self.words(self.unusable)}: it can't be used as an amount.",
                self.evidence,
            )
        return None


def _sum_text(quote, terms):
    """ "Base price ₹1,50,000 + GST ₹13,350" from [(field name, amount, sign)]."""
    parts = []
    for i, (name, amount, sign) in enumerate(terms):
        words = field_words(name, quote, quoted_label=False)
        words = words[:1].upper() + words[1:] if i == 0 else words
        parts.append(("" if i == 0 else " + " if sign > 0 else " minus ") + f"{words} {rupees(amount)}")
    return "".join(parts)


def _compare(check_id, ops, name, expected, terms, stated, stated_words, question, more_hint, less_hint):
    """Compares a worked-out amount with the quote's own. Differences of up to ₹1 count as
    rounding."""
    ops.evidence.append(computed(name, expected, " ".join(
        ("" if i == 0 else "+ " if sign > 0 else "minus ") + field_words(n, ops.quote, quoted_label=False)
        for i, (n, _, sign) in enumerate(terms))))
    difference = stated - expected
    sums = f"{_sum_text(ops.quote, terms)} = {rupees(expected)}"
    if abs(difference) <= TOLERANCE_INR:
        status, question = CONSISTENT, None
        same = "the same as" if difference == 0 else f"within ₹1 of the {rupees(stated)} on"
        message = f"{sums}, {same} the quote's {stated_words}."
    else:
        status = INCONSISTENT
        direction, hint = ("more", more_hint) if difference > 0 else ("less", less_hint)
        message = (f"{sums}. The quote's {stated_words} is {rupees(stated)}, which is {rupees(abs(difference))} "
                   f"{direction}. {hint}")
    return finding(
        check_id,
        status,
        message,
        ops.evidence,
        question=question,
        question_params={"computed": format_inr(expected), "stated": format_inr(stated)} if question else None,
    )


def check_gross_total(quote):
    if options_unresolved(quote):
        return options_finding(GROSS_CHECK_ID, quote)

    ops = _Operands(quote)
    gross = ops.take("gross_total", quote.get("gross_total"))
    base = ops.take("base_price", quote.get("base_price"))
    discount = None
    if value_of(quote.get("discount")) is not None:
        discount = ops.take("discount", quote.get("discount"))

    gst_field = quote.get("gst_treatment")
    if gst_field is not None:
        ops.evidence.append(quoted("gst_treatment", gst_field))
    gst_treatment = value_of(gst_field)
    gst = None
    if gst_treatment == "excluded":
        gst = ops.take("gst_amount", quote.get("gst_amount"))
    elif quote.get("gst_amount") is not None:
        ops.evidence.append(quoted("gst_amount", quote["gst_amount"]))

    extras, extra_names, outside, unclear = [], [], [], []
    for i, extra in enumerate(quote.get("extra_charges") or []):
        name = f"extra_charges[{i}]"
        # Contract v1 says whether each charge is inside the stated total. A
        # charge without that key (older input) is taken as listed in the total.
        if "included_in_total" in extra:
            inc_field = extra["included_in_total"]
            if inc_field is not None:
                ops.evidence.append(quoted(f"{name}.included_in_total", inc_field))
            included = value_of(inc_field)
            if included != "yes":
                (outside if included == "no" else unclear).append(name)
                if extra.get("amount") is not None:
                    ops.evidence.append(quoted(f"{name}.amount", extra["amount"]))
                continue
        amount = ops.take(f"{name}.amount", extra.get("amount"))
        if amount is not None:
            extras.append(amount)
            extra_names.append(f"{name}.amount")
    complete_field = quote.get("extra_charges_complete")
    if complete_field is not None:
        ops.evidence.append(quoted("extra_charges_complete", complete_field))

    blocked = ops.blocked(GROSS_CHECK_ID)
    if blocked:
        return blocked
    if gst_treatment not in ("included", "excluded"):
        return finding(
            GROSS_CHECK_ID,
            NEEDS_CONFIRMATION,
            "The quote does not say whether the base price includes GST.",
            ops.evidence,
        )
    if unclear:
        return finding(
            GROSS_CHECK_ID,
            NEEDS_CONFIRMATION,
            f"Is {ops.words(unclear)} inside the total? Please answer on the review screen.",
            ops.evidence,
        )
    if value_of(complete_field) is not True:
        return finding(
            GROSS_CHECK_ID,
            NEEDS_CONFIRMATION,
            "Is every charge on your quote listed? Answer yes on the review screen once it is, and the total "
            "will be checked.",
            ops.evidence,
        )

    # Only amounts the quote states appear in the relation.
    terms = [("base_price", base, 1)]
    if gst_treatment == "excluded":
        terms.append(("gst_amount", gst, 1))
    terms += [(name.removesuffix(".amount") + ".amount", amount, 1) for name, amount in zip(extra_names, extras)]
    if discount is not None:
        terms.append(("discount", discount, -1))
    expected = sum((amount * sign for _, amount, sign in terms), Decimal(0))
    result = _compare(GROSS_CHECK_ID, ops, "gross total", expected, terms, gross, "total", "total_mismatch",
                      "Is a charge missing from the list?", "Please check the amounts with the vendor.")
    if outside:
        result["notes"].append(f"Charges listed outside the total were not added: {ops.words(outside)}.")
    return result


def check_net_cost(quote):
    if options_unresolved(quote):
        return options_finding(NET_CHECK_ID, quote)

    ops = _Operands(quote)
    gross = ops.take("gross_total", quote.get("gross_total"))
    net = ops.take("net_cost", quote.get("net_cost"))

    basis_field = quote.get("net_cost_subsidy_basis")
    if basis_field is not None:
        ops.evidence.append(quoted("net_cost_subsidy_basis", basis_field))
    subsidy_fields = SUBSIDY_FIELDS.get(value_of(basis_field))

    subsidies = []
    for name in subsidy_fields or ():
        amount = ops.take(name, quote.get(name))
        if amount is not None:
            subsidies.append(amount)

    blocked = ops.blocked(NET_CHECK_ID)
    if blocked:
        return blocked
    if subsidy_fields is None:
        return finding(
            NET_CHECK_ID,
            NEEDS_CONFIRMATION,
            "The quote doesn't say which subsidy the net cost takes off.",
            ops.evidence,
        )

    expected = gross - sum(subsidies, Decimal(0))
    terms = [("gross_total", gross, 1)] + [(name, amount, -1) for name, amount in zip(subsidy_fields, subsidies)]
    result = _compare(NET_CHECK_ID, ops, "net cost", expected, terms, net, "net cost", "net_cost_mismatch",
                      "Please check the amounts with the vendor.", "Please check the amounts with the vendor.")
    if subsidy_fields:
        result["message"] += " Subsidy amounts are taken as stated and are not checked here."
    return result
