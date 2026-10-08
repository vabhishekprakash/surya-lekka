Evaluation scripts only. Data and labels stay outside this repo.

## Extraction spike

`python -m src.extract.spike` runs one or more Nova models on named documents, saves the raw replies, timings and token counts under `--out`, and scores each field against a hand-written answer key. It prints counts and scores only, never document text. `--out` must be outside the repo.

Install with `pip install -r requirements-spike.txt`. Add `--dry-run` to use stubbed replies without AWS.

The answer key is plain text, one block per document:

```
[Q12]
capacity_stated: 3.3 kWp | 1
total_price: Rs. 1,97,000 | 2
gst: extra | 2
subsidy: Rs. 78,000 | 2
subsidy_type: central
panel_count: 6 + 4 | 1
```

The page after `|` is optional. `not stated` means the correct answer is null. `pages_total` and `contradictions` are kept but not scored; the run warns when `pages_total` differs from the file. `python -m src.extract.spike --check-key --answer-key <file>` lists the field names it found for each document, and the ones it doesn't recognise, without any values. Run `--help` for the full list of names.
