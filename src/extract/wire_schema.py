"""The tool schema Nova fills in, its local validator, and the normaliser into
quote contract v1 fields.

The schema is shallow: the root has only type, properties and required, and
every value is a verbatim string or an enum. Parsing happens here, in code,
never in the model. There are no customer fields.
"""

import re

from checks.common import jsonable
from checks.parse import parse_amount, parse_capacity

TOOL_NAME = "extract_solar_quote"

AMOUNT_FIELDS = (
    "base_price", "gst_amount", "discount", "gross_total",
    "subsidy_central", "subsidy_state", "subsidy_combined", "subsidy_unspecified", "net_cost",
)
# Wire enum value -> contract value. None means "not stated".
FLAG_VALUES = {
    "multiple_options": {"yes": True, "no": False, "not_stated": None},
    "capacity_basis": {"dc_kwp": "dc_kwp", "ac_kw": "ac_kw", "unspecified": "unspecified"},
    "gst_treatment": {"included": "included", "excluded": "excluded", "unclear": "unclear"},
    "extra_charges_complete": {"stated": True, "not_stated": None},
    "net_cost_subsidy_basis": {v: v for v in ("none", "central", "state", "central_and_state", "combined",
                                              "unspecified")},
    "give_it_up": {"mentioned": True, "not_mentioned": None},
}
DCR_VALUES = {"dcr": True, "non_dcr": False}
INCLUDED_VALUES = ("yes", "no", "unclear")


def _text(description):
    return {"type": "string", "description": description}


_PAGE = {"type": "integer", "description": "The page number written before the page image."}
_EVIDENCE = _text("The exact text on the page that shows this value.")


def _field(description, value_description):
    return {"type": "object", "description": description,
            "properties": {"value": _text(value_description), "evidence": _EVIDENCE, "page": _PAGE},
            "required": ["value", "evidence", "page"]}


def _flag(description, values):
    return {"type": "object", "description": description,
            "properties": {"value": {"type": "string", "enum": list(values)}, "evidence": _EVIDENCE,
                           "page": _PAGE},
            "required": ["value"]}


_AMOUNT = "The amount exactly as printed, including Rs, commas and /-."
_OPTION_ID = _text("The option label as printed, such as 'Option A'. Leave out when the quote has one option.")
_ALTERNATIVES = {"type": "array", "items": {"type": "string"},
                 "description": "Other makes or models offered for the same item, exactly as printed."}

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "multiple_options": _flag("Does the quote offer more than one option (for example Option A and "
                                  "Option B with different prices)?", FLAG_VALUES["multiple_options"]),
        "options": {"type": "array", "description": "Each option the quote offers.", "items": {
            "type": "object",
            "properties": {"option_id": _OPTION_ID, "label": _text("The option heading as printed."),
                           "page": _PAGE},
            "required": ["option_id", "page"]}},
        "module_groups": {"type": "array", "description": "Each line of solar panels (modules).", "items": {
            "type": "object",
            "properties": {
                "option_id": _OPTION_ID,
                "count": _text("Number of panels exactly as written."),
                "count_evidence": _EVIDENCE,
                "wattage": _text("Wattage of one panel exactly as written, with its unit. Copy a range as "
                                 "written."),
                "wattage_evidence": _EVIDENCE,
                "make_model": _text("Panel make and model exactly as written."),
                "make_model_evidence": _EVIDENCE,
                "make_model_alternatives": _ALTERNATIVES,
                "page": _PAGE,
            },
            "required": ["page"]}},
        "inverters": {"type": "array", "description": "Each inverter.", "items": {
            "type": "object",
            "properties": {
                "option_id": _OPTION_ID,
                "make_model": _text("Inverter make and model exactly as written."),
                "make_model_evidence": _EVIDENCE,
                "rating": _text("Inverter rating exactly as written, with its unit."),
                "rating_evidence": _EVIDENCE,
                "make_model_alternatives": _ALTERNATIVES,
                "page": _PAGE,
            },
            "required": ["page"]}},
        "stated_capacity": _field("The total system capacity the quote states.",
                                  "The capacity exactly as written, with its unit."),
        "capacity_basis": _flag("Does the quote say the stated capacity is the DC panel capacity (kWp, DC) "
                                "or the AC/inverter capacity?", FLAG_VALUES["capacity_basis"]),
        "dcr_declaration": _flag("What the quote says about DCR (domestic content) panels and cells.",
                                 DCR_VALUES),
        "vendor_registration": _field("The vendor's registration or empanelment number.",
                                      "The number exactly as printed."),
        "base_price": _field("The system price before GST and extra charges.", _AMOUNT),
        "gst_amount": _field("The GST amount.", _AMOUNT),
        "discount": _field("A discount, if one is shown.", _AMOUNT),
        "gross_total": _field("The total the customer pays before any subsidy.", _AMOUNT),
        "subsidy_central": _field("The central (MNRE / PM Surya Ghar) subsidy amount.", _AMOUNT),
        "subsidy_state": _field("A state subsidy amount.", _AMOUNT),
        "subsidy_combined": _field("A single subsidy figure described as central plus state together.",
                                   _AMOUNT),
        "subsidy_unspecified": _field("A subsidy figure that does not say whether it is central or state.",
                                      _AMOUNT),
        "net_cost": _field("The cost after subsidy, if the quote shows one.", _AMOUNT),
        "extra_charges": {"type": "array", "description": "Each charge listed apart from the base price and "
                          "GST, such as net meter, structure or installation charges.", "items": {
            "type": "object",
            "properties": {
                "option_id": _OPTION_ID,
                "label": _text("The charge name exactly as printed."),
                "amount": _text(_AMOUNT),
                "included_in_total": {"type": "string", "enum": list(INCLUDED_VALUES),
                                      "description": "Does the quote say this charge is inside the total?"},
                "total_label": _text("The name of the total it is or is not part of, as printed."),
                "evidence": _EVIDENCE,
                "page": _PAGE,
            },
            "required": ["label", "page"]}},
        "gst_treatment": _flag("Does the base price include GST?", FLAG_VALUES["gst_treatment"]),
        "extra_charges_complete": _flag("Does the quote state that there are no other charges?",
                                        FLAG_VALUES["extra_charges_complete"]),
        "net_cost_subsidy_basis": _flag("Which subsidy does the quote take away to get the net cost?",
                                        FLAG_VALUES["net_cost_subsidy_basis"]),
        "give_it_up": _flag("Does the quote mention the 'Give It Up' subsidy option?", FLAG_VALUES["give_it_up"]),
    },
    "required": ["multiple_options", "module_groups", "inverters", "extra_charges"],
}

SCALAR_FIELDS = ("stated_capacity", "vendor_registration", *AMOUNT_FIELDS)
FLAG_FIELDS = tuple(FLAG_VALUES)
ITEM_LISTS = ("options", "module_groups", "inverters", "extra_charges")


# --- local validation ------------------------------------------------------------------

def _blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _prune(data):
    """Drop not-found entries: null fields, fields with a blank value, blank item strings."""
    out = {}
    for key, value in data.items():
        if isinstance(value, dict) and "value" in value:
            if not _blank(value.get("value")):
                out[key] = value
        elif isinstance(value, list):
            out[key] = [{k: v for k, v in item.items() if not _blank(v)} if isinstance(item, dict) else item
                        for item in value]
        elif not _blank(value):
            out[key] = value
    return out


def _check(schema, value, path, pages, errors):
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected an object")
            return
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required")
        for key, sub in value.items():
            if key in schema["properties"]:
                _check(schema["properties"][key], sub, f"{path}.{key}", pages, errors)
    elif kind == "array":
        if not isinstance(value, list):
            errors.append(f"{path}: expected an array")
            return
        for i, item in enumerate(value):
            _check(schema["items"], item, f"{path}[{i}]", pages, errors)
    elif kind == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected a string, got {type(value).__name__}")
        elif "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: not one of the allowed values")
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{path}: expected an integer, got {type(value).__name__}")
        elif path.endswith(".page") and value not in pages:
            errors.append(f"{path}: page {value} is not in this batch")


def validate(data, pages):
    """(cleaned_input, errors, ignored_keys). Errors name paths and types only, never document text."""
    if not isinstance(data, dict):
        return None, ["input: expected an object"], []
    ignored = sorted(k for k in data if k not in TOOL_SCHEMA["properties"])
    cleaned = _prune({k: v for k, v in data.items() if k in TOOL_SCHEMA["properties"]})
    errors = [f"input.{key}: required" for key in TOOL_SCHEMA["required"] if key not in cleaned]
    for key, value in cleaned.items():
        _check(TOOL_SCHEMA["properties"][key], value, f"input.{key}", set(pages), errors)
    return cleaned, errors, ignored


# --- normaliser into contract v1 -----------------------------------------------------------

_COUNT = re.compile(r"\s*(\d{1,4})\s*(?:nos?\.?|numbers?|pcs\.?|pieces?|panels?|modules?|units?)?\s*", re.I)


def count_value(raw):
    """An integer when the text is just a number of panels, else the text unchanged."""
    m = _COUNT.fullmatch(raw)
    return int(m.group(1)) if m else raw


def amount_value(raw):
    r = parse_amount(raw)
    return jsonable({"raw": raw, "parsed": r["parsed"], "parse_status": r["parse_status"]})


def capacity_value(raw):
    r = parse_capacity(raw)
    return jsonable({"raw": raw, "parsed": r["parsed"], "unit": r["unit"], "parse_status": r["parse_status"]})


def _option_id(raw):
    return " ".join(raw.split()) if isinstance(raw, str) and raw.strip() else None


def _f(value, evidence, page, batch):
    return {"value": value, "evidence_text": evidence, "page": page, "batch": batch}


def _item_field(item, key, convert, batch, evidence_key=None):
    if key not in item:
        return None
    evidence = item.get(evidence_key or f"{key}_evidence")
    return _f(convert(item[key]), evidence, item["page"], batch)


def to_contract(data, batch):
    """Contract v1 fields from one validated tool input. Ids are assigned at merge."""
    out = {name: None for name in (*SCALAR_FIELDS, "dcr_declaration")}
    for name in SCALAR_FIELDS:
        f = data.get(name)
        if f is None:
            continue
        if name in AMOUNT_FIELDS:
            value = amount_value(f["value"])
        elif name == "stated_capacity":
            value = capacity_value(f["value"])
        else:
            value = f["value"].strip()
        out[name] = _f(value, f["evidence"], f["page"], batch)
    dcr = data.get("dcr_declaration")
    if dcr is not None:
        out["dcr_declaration"] = _f(DCR_VALUES[dcr["value"]], dcr.get("evidence"), dcr.get("page"), batch)

    proposed = {}
    for name in FLAG_FIELDS:
        f = data.get(name)
        value = FLAG_VALUES[name].get(f["value"]) if f is not None else None
        proposed[name] = None if value is None else _f(value, f.get("evidence"), f.get("page"), batch)
    out["flags"] = {"model_proposed": proposed, "user_confirmed": {}}

    out["options"] = [
        {"option_id": _option_id(o["option_id"]),
         "label": _f(o["label"], o["label"], o["page"], batch) if "label" in o else None}
        for o in data.get("options") or [] if _option_id(o.get("option_id"))
    ]
    out["module_groups"] = [
        {
            "group_id": None,
            "option_id": _option_id(g.get("option_id")),
            "count": _item_field(g, "count", count_value, batch),
            "wattage": _item_field(g, "wattage", capacity_value, batch),
            "make_model": _item_field(g, "make_model", str.strip, batch),
            "make_model_alternatives": [_f(a.strip(), a.strip(), g["page"], batch)
                                        for a in g.get("make_model_alternatives") or [] if a.strip()],
        }
        for g in data.get("module_groups") or []
    ]
    out["inverters"] = [
        {
            "inverter_id": None,
            "option_id": _option_id(i.get("option_id")),
            "make_model": _item_field(i, "make_model", str.strip, batch),
            "rating": _item_field(i, "rating", capacity_value, batch),
            "make_model_alternatives": [_f(a.strip(), a.strip(), i["page"], batch)
                                        for a in i.get("make_model_alternatives") or [] if a.strip()],
        }
        for i in data.get("inverters") or []
    ]
    out["extra_charges"] = [
        {
            "charge_id": None,
            "option_id": _option_id(e.get("option_id")),
            "label": _f(e["label"].strip(), e.get("evidence"), e["page"], batch),
            "amount": _item_field(e, "amount", amount_value, batch, "evidence"),
            "included_in_total": _item_field(e, "included_in_total", str, batch, "evidence"),
            "total_label": e.get("total_label"),
        }
        for e in data.get("extra_charges") or []
    ]
    return out
