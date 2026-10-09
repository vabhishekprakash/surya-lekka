"""AWS SDK calls traced as X-Ray subsegments in Lambda, without quote content or tokens."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_sdk_is_pinned_for_the_lambda_package():
    assert "aws-xray-sdk==2.15.0" in (ROOT / "src" / "requirements.txt").read_text(encoding="utf-8").splitlines()


def test_botocore_is_patched_only_inside_lambda(monkeypatch):
    from api import common

    patched = []
    import aws_xray_sdk.core as core

    monkeypatch.setattr(core, "patch", lambda modules: patched.append(tuple(modules)))
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    common.trace_aws_calls()
    assert patched == []
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "surya-lekka-WorkerFunction")
    common.trace_aws_calls()
    assert patched == [("botocore",)]


PROBE = r'''
import json, sys
sys.path.insert(0, "src")
from aws_xray_sdk.core import patch, xray_recorder
xray_recorder.configure(sampling=False, context_missing="IGNORE_ERROR")
patch(["botocore"])
from extract.dryrun import stubbed_client
segment = xray_recorder.begin_segment("probe")
textract, stub = stubbed_client("textract")
stub.add_response("analyze_document", {"DocumentMetadata": {"Pages": 1},
                  "Blocks": [{"BlockType": "LINE", "Id": "l1", "Text": "QUOTE-TEXT-7731"}]})
textract.analyze_document(Document={"Bytes": b"PAGE-BYTES-7731"}, FeatureTypes=["TABLES"])
dynamo, stub = stubbed_client("dynamodb")
stub.add_response("update_item", {})
dynamo.update_item(TableName="jobs", Key={"job_id": {"S": "job"}}, UpdateExpression="SET t = :t",
                   ExpressionAttributeValues={":t": {"S": "TOKEN-HASH-7731"}})
print(json.dumps(segment.to_dict(), default=str))
xray_recorder.end_segment()
'''


def test_a_traced_call_records_the_operation_and_never_the_content():
    run = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert run.returncode == 0, run.stderr
    segment = run.stdout.strip().splitlines()[-1]
    subsegments = json.loads(segment)["subsegments"]
    assert [(s["namespace"], s["aws"]["operation"]) for s in subsegments] == [("aws", "AnalyzeDocument"),
                                                                              ("aws", "UpdateItem")]
    assert subsegments[1]["aws"]["table_name"] == "jobs"
    for secret in ("QUOTE-TEXT-7731", "PAGE-BYTES-7731", "TOKEN-HASH-7731"):
        assert secret not in segment
