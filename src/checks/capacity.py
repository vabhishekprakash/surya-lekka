"""C1: module count x wattage against the stated DC capacity."""

from decimal import Decimal

from .common import (
    CONSISTENT,
    INCONSISTENT,
    MISSING,
    NEEDS_CONFIRMATION,
    UnusableNumber,
    computed,
    finding,
    format_number,
    options_finding,
    options_unresolved,
    quoted,
    to_decimal,
    value_of,
)

CHECK_ID = "C1_capacity"

# Engineering tolerance for rounding in a stated kWp figure. Not an official rule.
DC_KWP_TOLERANCE = Decimal("0.01")


def check_capacity(quote):
    if options_unresolved(quote):
        return options_finding(CHECK_ID, quote)

    groups = quote.get("module_groups") or []
    if not groups:
        return finding(CHECK_ID, MISSING, "Module count and wattage were not found.", [])

    evidence, missing, unusable, terms = [], [], [], []
    ranged = False
    total_w = Decimal(0)
    for i, group in enumerate(groups):
        operands = {}
        for key in ("count", "wattage_w"):
            name = f"module_groups[{i}].{key}"
            field = group.get(key)
            if field is not None:
                evidence.append(quoted(name, field))
            try:
                number = to_decimal(value_of(field))
            except UnusableNumber:
                unusable.append(name)
                ranged = ranged or isinstance(value_of(field), dict)
                continue
            if number is None:
                missing.append(name)
            elif number <= 0 or (key == "count" and number != number.to_integral_value()):
                unusable.append(name)
            else:
                operands[key] = number
        if len(operands) == 2:
            total_w += operands["count"] * operands["wattage_w"]
            terms.append(f"{operands['count']} x {operands['wattage_w']} W")

    if missing:
        return finding(CHECK_ID, MISSING, f"Not found: {', '.join(missing)}.", evidence)
    if unusable:
        return finding(
            CHECK_ID,
            NEEDS_CONFIRMATION,
            f"Please confirm these values: {', '.join(unusable)}.",
            evidence,
            question="exact_capacity" if ranged else "panel_details",
        )

    computed_kwp = total_w / 1000
    evidence.append(computed("dc_kwp", computed_kwp, f"({' + '.join(terms)}) / 1000"))

    stated_field = quote.get("stated_capacity_kw")
    basis_field = quote.get("capacity_basis")
    if stated_field is not None:
        evidence.append(quoted("stated_capacity_kw", stated_field))
    if basis_field is not None:
        evidence.append(quoted("capacity_basis", basis_field))

    try:
        stated = to_decimal(value_of(stated_field))
    except UnusableNumber:
        return finding(CHECK_ID, NEEDS_CONFIRMATION, "Please confirm the stated capacity.", evidence,
                       question="dc_capacity")
    if stated is None:
        return finding(CHECK_ID, MISSING, "Stated system capacity was not found.", evidence)

    # The stated figure as written on the quote, for questions to the vendor.
    stated_text = stated_field.get("raw") or f"{format_number(stated)} kW"
    params = {"computed_kwp": format_number(computed_kwp), "stated": stated_text}

    if value_of(basis_field) != "dc_kwp":
        return finding(
            CHECK_ID,
            NEEDS_CONFIRMATION,
            f"Computed DC capacity is {computed_kwp} kWp. The quote does not say the stated "
            f"{stated} kW is DC module capacity, so they were not compared.",
            evidence,
            question="capacity_basis",
            question_params=params,
        )

    if abs(computed_kwp - stated) <= DC_KWP_TOLERANCE:
        status, verb, question = CONSISTENT, "matches", None
    else:
        status, verb, question = INCONSISTENT, "differs from", "capacity_mismatch"
    return finding(
        CHECK_ID,
        status,
        f"Computed DC capacity {computed_kwp} kWp (module count x wattage) {verb} the stated "
        f"{stated} kWp (tolerance {DC_KWP_TOLERANCE} kWp).",
        evidence,
        question=question,
        question_params=params if question else None,
    )
