"""The external review's round-6 probes (synthetic), against confirm-bound-1 and 9cc704c.

Taken from the review's tests/test_round6_local.py and tests/test_judge_review.py. Where a probe
asserted the faulty behaviour it found as a step (a rejected edit moving the revision on, a stale
write persisting, a saved sample created with every cap at zero), the assertion here is the
required behaviour instead; each such change is marked "required:".
"""

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from api import manual
from test_api import aws, call, common, jobs, put_sample  # noqa: F401  (aws is a fixture)
from test_manual import definitive
from test_manual import post as manual_post
from test_round5 import ANSWERS, DEFINITIVE, case, confirmed, gross, post, seed

ROOT = Path(__file__).resolve().parent.parent


def setup_job():
    job = seed(case("37_edit_revert")["textract_reply"])
    body = {"answers": dict(ANSWERS), "corrections": {"base_price": "100000"}}
    yes = confirmed(body, post(job, body))
    assert gross(post(job, yes))["status"] in DEFINITIVE
    return job, body, yes


def stored(job):
    return common.table().get_item(Key={"job_id": job["job_id"]}, ConsistentRead=True)["Item"]


def recheck(job, body):
    return call(jobs.recheck, body, path={"id": job["job_id"]}, query={"t": job["token"]})


def test_failed_edit_does_not_leave_old_definitive_result(aws):  # noqa: F811
    job, body, yes = setup_job()
    before = stored(job)
    status, result = recheck(job, {**body, "corrections": {"not_a_field": "1"}})
    assert status == 400
    after = stored(job)
    # required: a rejected edit changes nothing, the revision included
    assert int(after["review_revision"]) == int(before["review_revision"])
    assert after["checked"] == before["checked"] and after["corrections"] == before["corrections"]
    status, view = call(jobs.get_job, path={"id": job["job_id"]}, query={"t": job["token"]})
    assert status == 200
    # the stored result still belongs to the stored inputs and revision
    assert int(after.get("checked_revision", -1)) == int(after["review_revision"])


def test_overlapping_confirmation_cannot_overwrite_new_revision(aws, monkeypatch):  # noqa: F811
    job, body, yes = setup_job()
    original = jobs.run_checks
    entered = [False]

    def interleaved(*args, **kwargs):
        if not entered[0]:
            entered[0] = True
            changed = {**body, "corrections": {"base_price": "101000"}}
            assert gross(post(job, changed))["status"] not in DEFINITIVE
        return original(*args, **kwargs)

    monkeypatch.setattr(jobs, "run_checks", interleaved)
    status, stale = recheck(job, yes)
    item = stored(job)
    # required: the stale confirmation is refused and nothing it computed is stored
    assert status == 409 and stale["error"] == "review_changed"
    assert json.loads(item["corrections"])["user_inputs"]["corrections"]["base_price"] == "101000"
    assert gross(json.loads(item["checked"]))["status"] not in DEFINITIVE


def test_stale_database_read_cannot_revive_old_confirmation(aws, monkeypatch):  # noqa: F811
    job, body, yes = setup_job()
    old_item = copy.deepcopy(stored(job))
    assert gross(post(job, {**body, "corrections": {"base_price": "101000"}}))["status"] not in DEFINITIVE
    monkeypatch.setattr(jobs, "authorised_job", lambda event: old_item)
    status, result = recheck(job, yes)
    # required: refused (409), never a definitive result
    assert status == 409 or gross(result)["status"] not in DEFINITIVE
    assert gross(json.loads(stored(job)["checked"]))["status"] not in DEFINITIVE


TYPED = {"fields": {"base_price": "100000", "gst_amount": "18000", "gross_total": "118000", "discount": "0"},
         "answers": dict(ANSWERS)}


def typed_ready(monkeypatch):
    clock = [1800000000.0]
    monkeypatch.setattr(manual.time, "time", lambda: clock[0])
    status, first = manual_post(TYPED)
    assert status == 200 and gross(first).get("confirm_token") and not definitive(first)
    yes = {**copy.deepcopy(TYPED), "challenge": first["challenge"],
           "confirmed_operands": [gross(first)["confirm_token"]]}
    assert definitive(manual_post(yes)[1])
    return clock, yes


@pytest.mark.parametrize("variant", ["expired", "altered", "other_values", "forged_token"])
def test_typed_invalid_credentials_refused(aws, monkeypatch, variant):  # noqa: F811
    clock, yes = typed_ready(monkeypatch)
    clock[0] += 5
    if variant == "expired":
        clock[0] += 900
    elif variant == "altered":
        yes["challenge"] = yes["challenge"].rsplit(".", 1)[0] + "." + "0" * 64
    elif variant == "other_values":
        yes["fields"]["base_price"] = "101000"
    else:
        yes["confirmed_operands"] = [hashlib.sha256(b"client").hexdigest()]
    assert not definitive(manual_post(yes)[1])


@pytest.mark.parametrize("variant", ["missing", "altered"])
def test_same_second_invalid_challenge_cannot_recreate_old_one(aws, monkeypatch, variant):  # noqa: F811
    clock, yes = typed_ready(monkeypatch)
    if variant == "missing":
        yes.pop("challenge")
    else:
        yes["challenge"] = yes["challenge"].rsplit(".", 1)[0] + "." + "0" * 64
    assert not definitive(manual_post(yes)[1])


def test_typed_edit_revert_cannot_revive_confirmation(aws, monkeypatch):  # noqa: F811
    clock, yes = typed_ready(monkeypatch)
    clock[0] += 5
    changed = copy.deepcopy(yes)
    changed["fields"]["base_price"] = "101000"
    assert not definitive(manual_post(changed)[1])
    assert not definitive(manual_post(yes)[1])


def test_exact_typed_replay_is_accepted_within_documented_lifetime(aws, monkeypatch):  # noqa: F811
    clock, yes = typed_ready(monkeypatch)
    clock[0] += 899
    assert definitive(manual_post(yes)[1])


@pytest.mark.parametrize("kind", ["other_job", "old_revision", "client_hash"])
def test_job_basic_replays_blocked(aws, kind):  # noqa: F811
    job, body, yes = setup_job()
    if kind == "other_job":
        job = seed(case("37_edit_revert")["textract_reply"])
    elif kind == "old_revision":
        post(job, {**body, "corrections": {"base_price": "101000"}})
    else:
        yes["confirmed_operands"] = [hashlib.sha256(b"client").hexdigest()]
    assert gross(post(job, yes))["status"] not in DEFINITIVE


# --- the judge review --------------------------------------------------------------------------------

def test_saved_samples_respect_zero_caps_and_the_kill_switch(aws, monkeypatch):  # noqa: F811
    put_sample(aws)
    for name in ("SAMPLE_DAILY_CAP", "IP_DAILY_JOB_CAP"):
        monkeypatch.setenv(name, "0")
    status, _ = call(jobs.create_sample_job, path={"sample_id": "S1"})
    assert status == 429  # required: refused, not created
    monkeypatch.setenv("SAMPLE_DAILY_CAP", "100")
    monkeypatch.setenv("IP_DAILY_JOB_CAP", "100")
    monkeypatch.setenv("UPLOADS_ENABLED", "false")
    status, body = call(jobs.create_sample_job, path={"sample_id": "S1"})
    assert status == 503 and body["error"] == "uploads_disabled"  # required: the kill switch covers samples
    assert not [i for i in common.table().scan()["Items"] if i.get("source") == "sample:S1"]


SENTINEL = r'''
import json, os, sys
sys.path.insert(0, "src")
os.environ["AWS_LAMBDA_FUNCTION_NAME"] = "probe"
from api import common
common.trace_aws_calls()
from aws_xray_sdk.core import xray_recorder
xray_recorder.configure(sampling=False, context_missing="IGNORE_ERROR")
from extract.dryrun import stubbed_client
segment = xray_recorder.begin_segment("probe")
client, stub = stubbed_client("textract")
stub.add_client_error("analyze_document", service_error_code="BadDocumentException",
                      service_message="SYNTHETIC-SENSITIVE-TEXT-ROUND7", http_status_code=400)
try:
    client.analyze_document(Document={"Bytes": b"synthetic"}, FeatureTypes=["TABLES"])
except Exception:
    pass
print(json.dumps(segment.to_dict(), default=str))
'''


def test_sdk_error_trace_must_not_include_sensitive_exception_message():
    # required: the probe uses the app's own tracing set-up (api.common.trace_aws_calls)
    run = subprocess.run([sys.executable, "-c", SENTINEL], capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert run.returncode == 0, run.stderr
    trace = run.stdout.strip().splitlines()[-1]
    assert "SYNTHETIC-SENSITIVE-TEXT-ROUND7" not in trace and "Traceback" not in trace and "stack" not in trace
    (sub,) = json.loads(trace)["subsegments"]
    assert sub["aws"] == {"operation": "AnalyzeDocument"} and sub["error"] is True
