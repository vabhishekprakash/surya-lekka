"""web/pages.js: page numbers in the original quote, and every left-out page with its reason,
for POST /jobs. Run with node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node")


def plan(script):
    code = ("import { PagePlan } from './web/pages.js';\n" + script +
            "\nconsole.log(JSON.stringify(plan.request()));")
    run = subprocess.run([NODE, "--input-type=module", "-e", code], capture_output=True, text=True, cwd=ROOT,
                         timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_a_43_page_pdf_sends_the_first_20_and_lists_the_rest_as_over_the_limit():
    body = plan("const plan = new PagePlan(20);\nfor (let n = 1; n <= 43; n += 1) {"
                " if (plan.room() > 0) plan.keep('jpeg'); else plan.omit('over_limit'); }")
    assert body == {"page_count": 20, "page_numbers": list(range(1, 21)), "total_pages": 43,
                    "omitted": [{"page": n, "reason": "over_limit"} for n in range(21, 44)]}


def test_too_large_and_unreadable_pages_keep_the_numbering_of_the_rest():
    body = plan("const plan = new PagePlan(20);\nplan.keep('a'); plan.omit('too_large'); plan.keep('b');"
                " plan.omit('unreadable'); plan.keep('c');")
    assert body["page_numbers"] == [1, 3, 5] and body["total_pages"] == 5
    assert body["omitted"] == [{"page": 2, "reason": "too_large"}, {"page": 4, "reason": "unreadable"}]


def test_the_plan_is_what_the_server_accepts():
    from api.jobs import _page_plan
    body = plan("const plan = new PagePlan(20);\nfor (let n = 1; n <= 43; n += 1) {"
                " if (n === 7) plan.omit('unreadable'); else if (plan.room() > 0) plan.keep('jpeg');"
                " else plan.omit('over_limit'); }")
    numbers, total, omitted = _page_plan(body, body["page_count"])
    assert total == 43 and len(numbers) == 20 and len(omitted) == 23 and 7 not in numbers
