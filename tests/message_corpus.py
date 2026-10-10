"""A fixed, synthetic set of check runs whose statuses and English text are saved in
tests/fixtures/messages_golden.json. Message keys must leave every status and every word
of it unchanged."""

import copy
import json
from pathlib import Path

from api import manual
from checks import run_checks
from extract import textract_client as tc
from extract.dryrun import load_dry_run_wire
from extract.merge import merge_batches
from extract.wire_schema import normalise_batch, validate

ROOT = Path(__file__).resolve().parent.parent
FULL = {"state": "Telangana", "consumer_type": "individual_household", "portal_application_on_or_after_cutoff": True,
        "first_system": True, "prior_central_subsidy": False, "give_it_up": False, "multiple_options": False,
        "capacity_basis": "dc_kwp", "gst_treatment": "included", "extra_charges_complete": True,
        "net_cost_subsidy_basis": "central"}
VARIANTS = {
    "none": {},
    "full": FULL,
    "assam": {**FULL, "state": "Assam"},
    "before_cutoff": {**FULL, "portal_application_on_or_after_cutoff": False},
    "give_it_up": {**FULL, "give_it_up": True},
    "rwa": {**FULL, "consumer_type": "rwa_ghs"},
    "not_first": {**FULL, "first_system": False, "prior_central_subsidy": True},
    "gst_excluded": {**FULL, "gst_treatment": "excluded"},
    "gst_unclear": {**FULL, "gst_treatment": "unclear"},
    "basis_ac": {**FULL, "capacity_basis": "ac_kw"},
    "basis_unclear": {**FULL, "capacity_basis": "unclear"},
    "options_unknown": {k: v for k, v in FULL.items() if k != "multiple_options"},
    "charges_unsure": {**FULL, "extra_charges_complete": "unclear"},
    "net_basis_combined": {**FULL, "net_cost_subsidy_basis": "central_and_state"},
}
MANUAL = {
    "good": {"stated_capacity": "3.3 kWp", "panel_count": "6", "panel_wattage": "550 Wp", "base_price": "1,50,000",
             "gst_amount": "18,000", "gross_total": "1,68,000", "subsidy_central": "78,000", "net_cost": "90,000"},
}
MANUAL.update({
    "mismatch": {**MANUAL["good"], "stated_capacity": "4 kWp", "gross_total": "1,70,000", "net_cost": "95,000",
                 "subsidy_central": "90,000"},
    "lower_subsidy": {**MANUAL["good"], "subsidy_central": "60,000", "net_cost": "1,08,000"},
    "range": {**MANUAL["good"], "panel_wattage": "5500 Wp"},
    "scale": {**MANUAL["good"], "gross_total": "16,800"},
    "range_kw": {**MANUAL["good"], "stated_capacity": "0.5-1 kWp"},
    "kva": {**MANUAL["good"], "stated_capacity": "3 kVA"},
    "empty": {},
    "charges": {**MANUAL["good"], "extra_charges": [{"label": "Net meter", "amount": "5,000", "included_in_total": "no"},
                                                    {"label": "Structure", "amount": "7,000", "included_in_total": "unclear"}]},
    "makes": {**MANUAL["good"], "panel_make_model": "Example PV EXM-550", "inverter_make_model": "Example Inverter X3",
              "inverter_rating": "3 kW", "vendor_registration": "EX-VR-0001", "dcr_declaration": True},
})


def _dry_quote(change=None):
    wire = load_dry_run_wire()
    if change:
        change(wire)
    cleaned, errors, _ = validate(wire, [1, 2])
    assert errors == []
    return merge_batches([{"batch": 1, "pages": [1, 2], "contract": normalise_batch(cleaned, 1)}])


def _redteam(path):
    case = json.loads(path.read_text(encoding="utf-8"))
    wire, _ = tc.map_page(case["textract_reply"], 1, sources=frozenset(case["sources"]), expense=case["expense"])
    return merge_batches([{"batch": 1, "pages": [1], "model_id": "textract", "contract": tc.to_contract(wire, 1)}])


def cases():
    """(name, quote, user_inputs) in a fixed order."""
    out = []
    for path in sorted((ROOT / "samples" / "expected").glob("S*.json")):
        sample = json.loads(path.read_text(encoding="utf-8"))
        for name, answers in VARIANTS.items():
            out.append((f"{sample['sample_id']}/{name}", sample["quote"], {"confirmations": answers}))
        out.append((f"{sample['sample_id']}/own", sample["quote"], sample["user_inputs"]))
    for name, answers in VARIANTS.items():
        out.append((f"dryrun/{name}", _dry_quote(), {"confirmations": answers}))
    for path in sorted((ROOT / "tests" / "fixtures" / "redteam").glob("[0-9][0-9]_*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        quote = _redteam(path)
        out.append((f"redteam/{path.stem}", quote, {"confirmations": case["confirmations"]}))
        out.append((f"redteam/{path.stem}/full", quote, {"confirmations": FULL}))
    for name, fields in MANUAL.items():
        for variant in ("full", "assam", "before_cutoff", "none"):
            charges, corrections = manual.typed_corrections(fields)
            out.append((f"manual/{name}/{variant}", manual.blank_quote(charges),
                        {"corrections": corrections, "confirmations": {"multiple_options": False,
                                                                       **VARIANTS[variant]}}))
    return out


def snapshot(result):
    """Statuses and English only: what message keys must not change."""
    return {
        "findings": [{k: f.get(k) for k in ("check_id", "item", "status", "message", "question", "question_params",
                                            "rule_id", "notes")} for f in result["findings"]],
        "questions": [{"id": q["id"], "text": q["text"]} for q in result["questions"]],
        "vendor_message": result["vendor_message"],
    }


def run_all():
    return {name: snapshot(run_checks(copy.deepcopy(quote), copy.deepcopy(inputs))) for name, quote, inputs in cases()}
