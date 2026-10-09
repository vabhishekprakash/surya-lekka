# Synthetic sample quotes

Three made-up rooftop solar quotes for testing the checks. The vendor (Example Solar Pvt Ltd), its address, phone number and every figure are invented. Each page carries the watermark "SAMPLE - NOT A REAL QUOTATION".

| Sample | What it tests |
|---|---|
| S1 | A clean quote. Every check comes out consistent. |
| S2 | Panels add up to 2.5 kWp but the quote says 3 kWp, and the central subsidy is higher than the rule allows. |
| S3 | Alternative brands, no panel wattage, charges outside the total, unclear GST and a single subsidy figure. |

`expected/<id>.json` holds the page text, the quote in contract v1 form, the user's answers, and the findings and vendor questions we expect. `tests/test_samples.py` runs the checks on each one and compares.

`cached/<id>.json` is the saved reading that the "Try a sample" button shows. It is copied from the expected quote and page text, so no model read it. After changing a sample, run `python samples/generate.py --saved-readings`.

The PDFs are not kept in git. The deploy step will generate them from the JSON files. To make them locally, run:

```
python samples/generate.py
```

This needs PyMuPDF (`pip install -r requirements-redact.txt`).
