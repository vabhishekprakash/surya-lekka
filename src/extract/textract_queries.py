"""The questions Amazon Textract answers on every page, and where each answer goes
in the wire input (the same input Nova fills in).

At most 15 queries per page and English only (Textract's limits for
AnalyzeDocument). Each alias is stable: the mapping, the tests and the spike's
confidence report key on it. Wording is tuned on the development quotes only.
"""

MAX_QUERIES = 15

# (alias, question, kind). kind says how the answer must parse and where it goes:
#   capacity  stated system capacity, needs a kW, kWp or W unit
#   wattage   one panel's wattage, needs a W or Wp unit
#   rating    inverter rating, needs a kW, W or kVA unit
#   count     a whole number of panels
#   amount    a price or subsidy amount
#   dcr       DCR or non-DCR panels
#   text      copied as written
QUERIES = (
    ("SYSTEM_CAPACITY", "What is the total system capacity in kW or kWp?", "capacity"),
    ("PANEL_COUNT", "What is the quantity of solar panels or modules?", "count"),
    ("PANEL_WATTAGE", "What is the wattage of each solar panel in Wp?", "wattage"),
    ("PANEL_MAKE_MODEL", "What is the solar panel make and model?", "text"),
    ("INVERTER_MAKE_MODEL", "What is the inverter make and model?", "text"),
    ("INVERTER_CAPACITY", "What is the inverter capacity?", "rating"),
    ("PRICE_BEFORE_GST", "What is the price before GST?", "amount"),
    ("GST_AMOUNT", "What is the GST amount in rupees?", "amount"),
    ("TOTAL_PAYABLE", "What is the total amount payable?", "amount"),
    ("SUBSIDY_AMOUNT", "What is the subsidy amount?", "amount"),
    ("NET_COST", "What is the net cost after subsidy?", "amount"),
    ("DCR_STATUS", "What is the DCR or non-DCR status of the panels?", "dcr"),
    ("VENDOR_NAME", "What is the name of the company issuing the quotation?", "text"),
    ("QUOTE_DATE", "What is the quotation date?", "text"),
    ("VENDOR_REGISTRATION", "What is the vendor registration or empanelment number?", "text"),
)

# alias -> (wire list or field, key inside it)
TARGETS = {
    "SYSTEM_CAPACITY": ("capacities", None),
    "PANEL_COUNT": ("module_groups", "count"),
    "PANEL_WATTAGE": ("module_groups", "wattage"),
    "PANEL_MAKE_MODEL": ("module_groups", "make_model"),
    "INVERTER_MAKE_MODEL": ("inverters", "make_model"),
    "INVERTER_CAPACITY": ("inverters", "rating"),
    "PRICE_BEFORE_GST": ("prices", "base_price"),
    "GST_AMOUNT": ("prices", "gst_amount"),
    "TOTAL_PAYABLE": ("prices", "gross_total"),
    "SUBSIDY_AMOUNT": ("subsidies", None),
    "NET_COST": ("prices", "net_cost"),
    "DCR_STATUS": ("dcr_declaration", None),
    "VENDOR_NAME": ("vendor_name", None),
    "QUOTE_DATE": ("quote_date", None),
    "VENDOR_REGISTRATION": ("vendor_registration", None),
}
KINDS = {alias: kind for alias, _, kind in QUERIES}
MONEY = {alias for alias, _, kind in QUERIES if kind == "amount"}


def queries_config():
    return {"Queries": [{"Text": text, "Alias": alias} for alias, text, _ in QUERIES]}
