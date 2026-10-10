"""Round 8: web/app.js in a simulated browser (jsdom): a cold #sample=S4 open, the four status
words in both languages, Telugu rupee amounts against English, the English fallback, and where
"Fix a number" takes the household. Needs node and `npm ci --prefix tests`."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from checks import run_checks

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None or not (ROOT / "tests" / "node_modules" / "jsdom").is_dir(),
                                reason="needs node and npm ci --prefix tests")


def dom_run(tmp_path, script="tests/round8_dom.mjs"):
    quote = json.loads((ROOT / "samples" / "cached" / "S1.json").read_text(encoding="utf-8"))["quote"]
    path = tmp_path / "input.json"  # adapted: a temporary file, not the repo root
    path.write_text(json.dumps(run_checks(quote), default=str), encoding="utf-8")
    run = subprocess.run([NODE, script, str(path)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                         timeout=120)
    assert run.returncode == 0, run.stderr[-3000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


def test_round8_dom_checks_pass(tmp_path):
    out = dom_run(tmp_path)
    assert out["view"] == "review" and out["requests"][0] == ["POST", "/samples/S4"]
    assert out["missingKeyFallback"] == "entire results screen matches English baseline"


def test_a_cold_sample_link_retries_once_then_offers_try_again(tmp_path):
    out = dom_run(tmp_path, "tests/s4_guard_dom.mjs")
    assert out["fail1"] == {"view": "review", "posts": 2, "message": ""}  # one failure: retried and opened
    assert out["fail2"]["view"] == "problem" and out["fail2"]["posts"] == 2
    assert out["fail2"]["message"] == "Couldn't open the sample, tap to try again."
    assert out["fail2"]["afterRetry"] == "review"
