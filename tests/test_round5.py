"""The red-team's round-5 confirmation cases (37, 39, 40, 41, 42, against reader-safe-2), run as
request sequences through POST /jobs/{id}/checks with moto. Each must end at "needs confirmation":
a confirmation counts only for the job and the review revision it was issued for."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from test_api import aws, call, common, create, jobs  # noqa: F401  (aws is a fixture)
from extract import textract_client as tc
from extract.merge import merge_batches

FIXTURES = Path(__file__).parent / "fixtures" / "redteam"
ANSWERS = {"gst_treatment": "excluded", "extra_charges_complete": True, "multiple_options": False}
GROSS = "C3_gross_total"
DEFINITIVE = ("consistent", "inconsistent")


def case(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def seed(reply):
    """A done job holding the reading of one synthetic page."""
    job = create(1)
    wire, _ = tc.map_page(reply, 1)
    quote = merge_batches([{"batch": 1, "pages": [1], "model_id": "textract", "contract": tc.to_contract(wire, 1)}])
    common.table().update_item(Key={"job_id": job["job_id"]}, UpdateExpression="SET #s = :s, extraction = :q",
                               ExpressionAttributeNames={"#s": "status"},
                               ExpressionAttributeValues={":s": "done", ":q": json.dumps(quote)})
    return job


def post(job, body):
    status, result = call(jobs.recheck, body, path={"id": job["job_id"]}, query={"t": job["token"]})
    assert status == 200, result
    return result


def gross(result):
    (f,) = [f for f in result["findings"] if f["check_id"] == GROSS]
    return f


def confirmed(body, result):
    return {**copy.deepcopy(body), "confirmed_operands": [gross(result)["confirm_token"]]}


@pytest.mark.parametrize("name,middle", [("37_edit_revert", "101000"), ("39_reformat_revert", "100000.00")])
def test_an_edit_then_revert_needs_a_fresh_confirmation(aws, name, middle):  # noqa: F811
    job = seed(case(name)["textract_reply"])
    body = {"answers": dict(ANSWERS), "corrections": {"base_price": "100000"}}
    first = post(job, body)
    yes = confirmed(body, first)
    assert gross(post(job, yes))["status"] in DEFINITIVE  # confirmed: the check gives its result
    edited = copy.deepcopy(yes)
    edited["corrections"]["base_price"] = middle
    assert gross(post(job, edited))["status"] == "needs_confirmation"
    assert gross(post(job, yes))["status"] == "needs_confirmation"  # back to 100000: the old token is stale


def test_a_token_from_another_job_is_refused(aws):  # noqa: F811
    reply = case("40_other_job")["textract_reply"]
    job, other = seed(reply), seed(reply)
    body = {"answers": dict(ANSWERS)}
    yes = confirmed(body, post(job, body))
    assert gross(post(job, yes))["status"] in DEFINITIVE
    assert gross(post(other, yes))["status"] == "needs_confirmation"


def test_a_token_computed_outside_the_server_is_refused(aws):  # noqa: F811
    job = seed(case("41_precomputed")["textract_reply"])
    body = {"answers": dict(ANSWERS)}
    shown = gross(post(job, body))
    payload = {"check": GROSS, "item": None, "option": None, "operands": shown["operands"]}
    forged = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    for token in (forged[:24], forged, shown["confirm_token"][::-1]):
        assert gross(post(job, {**body, "confirmed_operands": [token]}))["status"] == "needs_confirmation"


def test_every_option_switch_clears_even_back_to_the_first(aws):  # noqa: F811
    job = seed(case("42_option_back")["textract_reply"])
    body = {"answers": {**ANSWERS, "gst_treatment": "included", "multiple_options": True, "selected_option": "3 kW"}}
    yes = confirmed(body, post(job, body))
    assert gross(post(job, yes))["status"] in DEFINITIVE
    other = copy.deepcopy(yes)
    other["answers"]["selected_option"] = "5 kW"
    assert gross(post(job, other))["status"] == "needs_confirmation"
    assert gross(post(job, yes))["status"] == "needs_confirmation"  # A to B to A needs a fresh confirmation
