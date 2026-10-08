"""C3: price, GST, extras, discount, subsidy and net cost arithmetic.

All amounts are already-parsed INR values. Missing values are never treated as zero.
"""

from decimal import Decimal

from .common import (
    CONSISTENT,
    INCONSISTENT,
    MISSING,
    NEEDS_CONFIRMATION,
    UnusableNumber,
    computed,
    finding,
    options_finding,
    options_unresolved,
    quoted,
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

    def __init__(self):
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

    def blocked(self, check_id):
        if self.missing:
            return finding(check_id, MISSING, f"Not found: {', '.join(self.missing)}.", self.evidence)
        if self.unusable:
            return finding(
                check_id,
                NEEDS_CONFIRMATION,
                f"Please confirm these values: {', '.join(self.unusable)}.",
                self.evidence,
            )
        return None


def _compare(check_id, ops, name, expected, formula, stated, stated_name):
    ops.evidence.append(computed(name, expected, formula))
    difference = stated - expected
    if abs(difference) <= TOLERANCE_INR:
        status, verb = CONSISTENT, "matches"
    else:
        status, verb = INCONSISTENT, "differs from"
    return finding(
        check_id,
        status,
        f"Computed {name} INR {expected} ({formula}) {verb} the stated {stated_name} "
        f"INR {stated} (tolerance INR {TOLERANCE_INR}).",
        ops.evidence,
    )


def check_gross_total(quote):
    if options_unresolved(quote):
        return options_finding(GROSS_CHECK_ID, quote)

    ops = _Operands()
    gross = ops.take("gross_total", quote.get("gross_total"))
    base = ops.take("base_price", quote.get("base_price"))
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

    extras, outside, unclear = [], [], []
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
            f"Please confirm whether these charges are inside the total: {', '.join(unclear)}.",
            ops.evidence,
        )
    if value_of(complete_field) is not True:
        return finding(
            GROSS_CHECK_ID,
            NEEDS_CONFIRMATION,
            "Please confirm that the listed extra charges are all the extra charges.",
            ops.evidence,
        )

    expected = base + sum(extras, Decimal(0)) - discount
    formula = "base_price + extra_charges - discount"
    if gst_treatment == "excluded":
        expected += gst
        formula = "base_price + gst_amount + extra_charges - discount"
    result = _compare(GROSS_CHECK_ID, ops, "gross total", expected, formula, gross, "total")
    if outside:
        result["notes"].append(f"Charges listed outside the total were not added: {', '.join(outside)}.")
    return result


def check_net_cost(quote):
    if options_unresolved(quote):
        return options_finding(NET_CHECK_ID, quote)

    ops = _Operands()
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
            "The quote does not say which subsidy the net cost deducts.",
            ops.evidence,
        )

    expected = gross - sum(subsidies, Decimal(0))
    formula = " - ".join(("gross_total",) + subsidy_fields)
    result = _compare(NET_CHECK_ID, ops, "net cost", expected, formula, net, "net cost")
    if subsidy_fields:
        result["message"] += " Subsidy amounts are taken as stated and are not checked here."
    return result
