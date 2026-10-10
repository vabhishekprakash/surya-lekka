"""Round 7: Telugu fallback probes (synthetic S1), run against the production app functions."""
import json
from pathlib import Path
import subprocess
import shutil

import pytest
from checks import run_checks

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='needs node')

@pytest.fixture(scope='module')
def browser_results(tmp_path_factory):
    quote = json.loads((ROOT/'samples/cached/S1.json').read_text(encoding='utf-8'))['quote']
    result = run_checks(quote)
    path = tmp_path_factory.mktemp('round7')/'input.json'  # adapted: a temporary file, not the repo root
    path.write_text(json.dumps(result,default=str),encoding='utf-8')
    run = subprocess.run(['node','tests/round7_telugu.mjs',str(path)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)

@pytest.mark.parametrize('variant', ['normal_transition', 'image_transition'])
def test_first_transition_has_no_english_page_controls(browser_results, variant):
    row = browser_results[variant]
    assert row['error'] is None, row['error']
    assert row['language'] == 'te'
    assert 'Page 1' not in row['groups'] and 'Show where page' not in row['groups'], row['groups']

def test_missing_fixed_key_falls_back_without_crash(browser_results):
    row = browser_results['missing_fixed_key']
    assert row['error'] is None, row['error']
    assert row['language'] == 'en' and row['notice']

def test_missing_result_key_falls_back_to_english(browser_results):
    row = browser_results['missing_message_key']
    assert row['error'] is None, row['error']
    assert row['language'] == 'en' and row['notice']

def test_missing_vendor_parts_falls_back_without_losing_message(browser_results):
    row = browser_results['missing_vendor_parts']
    assert row['error'] is None, row['error']
    assert row['language'] == 'en' and row['notice'], row['vendor']
