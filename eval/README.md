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

## Safety harness and positive controls

`safety_harness.py` runs the household scenarios (S-A to S-E) on a quote's raw reading and on its labelled values and compares the findings check by check. Household facts come from the scenario; answers about the quote itself come from the labels where they have them. Labelled runs confirm every operand. Raw runs are either untouched or accept-all (every value ticked and every operand set confirmed as shown). It holds no data: the labels are passed in.

`positive_controls.txt` holds made-up quotes written like the labels, and `positive_controls.json` the findings worked out by hand for them, including a "matches" and a "doesn't match" for each of capacity, subsidy, total and net cost. `tests/test_positive_controls.py` checks that the harness reproduces every one.

## Held-out run (10 Oct 2026)

Run once from a clean worktree of the tag `reader-safe-2` (3fa34d6), with no code change between the tag and the run: `python -m src.extract.spike --engine textract --final-heldout` on the nine held-out quotes, both held-out label files, 61 pages (the 43-page quote capped at its first 20, as the product does), about $1.22. The run wrote its marker, and a second run is refused. Before it, the evaluation manifest (outside this repo) recorded each quote's file hash, its set, its label file with that file's hash and modified time, and that no live run had read a held-out file.

Scoring used the frozen `src/extract/scoring.py`, including the vendor-name rule set before the run (any one of several printed names counts). Development and held-out numbers are reported separately in the README's Results section, never pooled. Labels were written and frozen before any reading of the held-out quotes.

