Evaluation scripts only. Data and labels stay outside this repo.

## Extraction spike

`python -m src.extract.spike` runs one or more Nova models on named documents, saves the raw replies, timings and token counts under `--out`, and scores each field against a hand-written answer key. It prints counts and scores only, never document text. `--out` must be outside the repo.

Install with `pip install -r requirements-spike.txt`. Add `--dry-run` to use stubbed replies without AWS.

The answer key is plain text, one block per document:

```
[Q12]
stated_capacity: 3.3 kWp | 1
base_price: Rs. 1,80,000 | 2
discount: null
panel_count: 6 + 4 | 1
```

The page after `|` is optional. `null`, `-` or `not found` mean the quote doesn't state it. Run `python -m src.extract.spike --help` for the field names it scores. Keys it doesn't recognise are listed and left out of the score.
