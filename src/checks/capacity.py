"""C1: module count x wattage against the stated DC capacity."""

from decimal import Decimal

from .common import (
    CONSISTENT,
    INCONSISTENT,
    MISSING,
    NEEDS_CONFIRMATION,
    UnusableNumber,
    computed,
    without_unit,
    field_words,
    finding,
    format_number,
    join_words,
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
        return finding(CHECK_ID, MISSING, "The number of panels and their wattage weren't found on the quote.", [])

    evidence, missing, unusable, terms, lines = [], [], [], [], []
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
            count, watts = format_number(operands["count"]), format_number(operands["wattage_w"])
            lines.append(f"{count} panel{'' if count == '1' else 's'} of {watts} W")

    if missing:
        return finding(CHECK_ID, MISSING,
                       f"Not found on the quote: {join_words(field_words(n, quote) for n in missing)}.", evidence)
    if unusable:
        return finding(
            CHECK_ID,
            NEEDS_CONFIRMATION,
            f"Please check {join_words(field_words(n, quote) for n in unusable)}: "
            "a single number is needed.",
            evidence,
            question="exact_capacity" if ranged else "panel_details",
        )

    computed_kwp = total_w / 1000
    kwp = format_number(computed_kwp)
    panels = f"{join_words(lines)} make {kwp} kWp"
    evidence.append(computed("dc_kwp", computed_kwp, f"({' + '.join(terms)}) / 1000"))

    stated_field = quote.get("stated_capacity_kw")
    basis_field = quote.get("capacity_basis")
    if stated_field is not None:
        evidence.append(quoted("stated_capacity_kw", stated_field))
    if basis_field is not None:
        evidence.append(quoted("capacity_basis", basis_field))

    if quote.get("stated_capacity_unit") == "kVA":
        raw = stated_field.get("raw")
        return finding(CHECK_ID, NEEDS_CONFIRMATION,
                       f"The quote gives the system size as {raw}. kVA measures the inverter's output, not the "
                       f"panels, so it wasn't compared with the {kwp} kWp the panels make.", evidence,
                       question="capacity_basis",
                       question_params={"computed_kwp": format_number(computed_kwp), "stated": raw})
    try:
        stated = to_decimal(value_of(stated_field))
    except UnusableNumber:
        if without_unit(stated_field):
            return finding(CHECK_ID, NEEDS_CONFIRMATION,
                           "Please add the unit to the system size, for example 3 kWp.", evidence,
                           question="dc_capacity")
        return finding(CHECK_ID, NEEDS_CONFIRMATION, "Please check the system size on the quote.", evidence,
                       question="dc_capacity")
    if stated is None:
        return finding(CHECK_ID, MISSING, "The system size wasn't found on the quote.", evidence)

    # The stated figure as written on the quote, for questions to the vendor.
    stated_text = stated_field.get("raw") or f"{format_number(stated)} kW"
    params = {"computed_kwp": format_number(computed_kwp), "stated": stated_text}

    if value_of(basis_field) != "dc_kwp":
        return finding(
            CHECK_ID,
            NEEDS_CONFIRMATION,
            f"{panels}. The quote doesn't say whether its {format_number(stated)} kW system size is the panels' "
            "(DC) capacity, so the two weren't compared.",
            evidence,
            question="capacity_basis",
            question_params=params,
        )

    if abs(computed_kwp - stated) <= DC_KWP_TOLERANCE:
        status, question = CONSISTENT, None
        same = ("the same as the quote's system size" if computed_kwp == stated
                else f"within 0.01 kWp of the quote's {format_number(stated)} kWp")
        message = f"{panels}, {same}."
    else:
        status, question = INCONSISTENT, "capacity_mismatch"
        message = f"{panels}, but the quote's system size is {format_number(stated)} kWp."
    return finding(
        CHECK_ID,
        status,
        message,
        evidence,
        question=question,
        question_params=params if question else None,
    )
