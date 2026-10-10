"""Positive controls: made-up quotes with findings worked out by hand. The safety harness must
reproduce every one, so a run with no definitive findings means there were none to find, not a
harness that can't see them."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

import safety_harness as harness  # noqa: E402
from extract import scoring  # noqa: E402

LABELS = scoring.parse_answer_key((ROOT / "eval" / "positive_controls.txt").read_text(encoding="utf-8"))["docs"]
EXPECTED = json.loads((ROOT / "eval" / "positive_controls.json").read_text(encoding="utf-8"))
CASES = [(quote, scenario, check, status) for quote, by_scenario in EXPECTED.items()
         for scenario, checks in by_scenario.items() for check, status in checks.items()]


def test_the_controls_cover_both_outcomes_for_every_check():
    for check in harness.CHECKS:
        assert {s for _, _, c, s in CASES if c == check} == {"consistent", "inconsistent"}, check


@pytest.mark.parametrize("quote,scenario,check,status", CASES, ids=lambda v: str(v))
def test_the_harness_reproduces_each_expected_finding(quote, scenario, check, status):
    truth = LABELS[quote]
    result = harness.labelled(truth, harness.answers(scenario, truth))
    assert harness.by_check(result)[(check, None)]["status"] == status


@pytest.mark.parametrize("quote", sorted(EXPECTED))
def test_untouched_runs_of_the_controls_give_nothing_definitive(quote):
    truth = LABELS[quote]
    result = harness.untouched(harness.labelled_quote(truth), harness.answers("S-A", truth))
    assert not [f for f in result["findings"] if f["status"] in harness.DEFINITIVE]


def test_a_wrong_operand_is_found_against_the_labels():
    truth = LABELS["P1"]
    other = harness.labelled(LABELS["P2"], harness.answers("S-A", LABELS["P2"]))
    gross = harness.by_check(other)[("C3_gross_total", None)]
    assert harness.wrong_operands(gross, truth) == ["gross_total"]
