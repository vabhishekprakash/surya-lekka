"""Round 7: synthetic probes; failing assertions state the required behaviour."""
import copy
import json
from pathlib import Path
import pytest
from api import manual
from test_api import aws, common
from test_manual import post as manual_post, definitive
from test_round6 import typed_ready, TYPED


def test_typed_confirmation_cannot_finish_after_session_advances(aws, monkeypatch):
    _, yes = typed_ready(monkeypatch)
    original = manual.run_checks
    entered = False
    new_challenge = None

    def interleave(*args, **kwargs):
        nonlocal entered, new_challenge
        if not entered:
            entered = True
            changed = copy.deepcopy(yes)
            changed['fields']['base_price'] = '101000'
            code, changed_result = manual_post(changed)
            assert code == 200 and not definitive(changed_result)
            new_challenge = changed_result['challenge']
        return original(*args, **kwargs)

    monkeypatch.setattr(manual, 'run_checks', interleave)
    code, result = manual_post(yes)
    assert new_challenge.split('.')[1] == '1'
    assert code == 409 or not definitive(result), (code, result['challenge'], new_challenge)


def test_rejected_typed_answer_does_not_consume_admission(aws):
    code, body = manual_post({**copy.deepcopy(TYPED), 'answers': {'unknown_answer': True}})
    assert code == 400, body
    assert common.table().scan()['Items'] == [], 'invalid answer consumed quotas and created a session'


def test_rejected_typed_edit_does_not_invalidate_valid_confirmation(aws, monkeypatch):
    _, yes = typed_ready(monkeypatch)
    rejected = copy.deepcopy(yes)
    rejected['answers']['unknown_answer'] = True
    code, body = manual_post(rejected)
    assert code == 400, body
    code, result = manual_post(yes)
    assert code == 200 and definitive(result), 'rejected edit advanced the typed session'


def test_saved_sample_whole_item_bound_is_checked_before_write(aws):
    from test_api import BUCKET, call, jobs
    reading = json.loads((Path(__file__).resolve().parents[1]/'samples/cached/S1.json').read_text(encoding='utf-8'))
    reading['pages'] = {'1': '\U0001f600' * 45000}
    raw = json.dumps(reading, ensure_ascii=False).encode('utf-8')
    assert len(raw) <= jobs.READING_MAX_BYTES, len(raw)
    assert len(json.dumps(reading['pages']).encode('utf-8')) > 409600
    aws.put_object(Bucket=BUCKET, Key='samples/S1/reading.json', Body=raw)
    code, result = call(jobs.create_sample_job, path={'sample_id':'S1'})
    assert code == 413 and result['error'] == 'result_too_large', (code, result)


def test_documented_four_read_limit_survives_hard_timeouts(aws, monkeypatch):
    from test_api import create, upload, worker, run_worker, retry, FakeTextract
    clock = [1800000000]
    monkeypatch.setattr(common.time, 'time', lambda: clock[0])
    monkeypatch.setenv('READING_ENGINE', 'textract')
    client = FakeTextract()
    monkeypatch.setattr(worker, 'textract_client', lambda: client)
    job = create(1)
    upload(aws, job, 1)
    reads = []
    # A hard runtime timeout interrupts Python without reaching its Exception handler.
    # Save of the reading has failed; the next invocation has nothing to resume.
    def hard_stop_after_unsaved_read(*args):
        reads.append(1)
        raise SystemExit('synthetic hard Lambda timeout after an unsaved read')
    monkeypatch.setattr(worker, '_save_batch', hard_stop_after_unsaved_read)
    for attempt in range(3):
        if attempt:
            assert retry(job)[0] == 202
        for delivery in range(2):  # first delivery and its one Lambda retry
            # adapted: once the persisted read budget is spent, a run ends without reading
            # (and so without the synthetic hard stop) instead of raising SystemExit
            try:
                run_worker(job['job_id'])
            except SystemExit:
                pass
            clock[0] += 961  # 900 second Lambda timeout plus retry delay
    assert len(client.calls) == len(reads)
    assert len(reads) <= 4, f'{len(reads)} successful mocked AnalyzeDocument calls are allowed'
    item = common.table().get_item(Key={'job_id': job['job_id']})['Item']
    assert item['status'] == 'failed' and item['reason'] == 'read_limit' and int(item['paid_reads']) == 4
