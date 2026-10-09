import runpy
from pathlib import Path

import pytest

pytest.importorskip("cfnlint")
pytest.importorskip("samtranslator")

from cfnlint.decode import decode

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE, _ = decode(str(ROOT / "template.yaml"))
RES = TEMPLATE["Resources"]


def statements(name):
    return [s for p in RES[name]["Properties"]["Policies"] for s in p["Statement"]]


def actions(statement):
    a = statement["Action"]
    return {a} if isinstance(a, str) else set(a)


def text(value):
    return str(value)


def test_lints_clean_offline():
    lint = runpy.run_path(str(ROOT / "scripts" / "lint_template.py"))
    assert lint["main"]([str(ROOT / "template.yaml")]) == 0


def test_bucket_is_private_encrypted_and_expires_uploads():
    props = RES["UploadBucket"]["Properties"]
    assert all(props["PublicAccessBlockConfiguration"].values()) and len(props["PublicAccessBlockConfiguration"]) == 4
    assert props["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]["ServerSideEncryptionByDefault"] == {
        "SSEAlgorithm": "AES256"}
    (cors,) = props["CorsConfiguration"]["CorsRules"]
    assert cors["AllowedOrigins"] == [{"Ref": "SiteOrigin"}] and cors["AllowedMethods"] == ["POST"]
    (rule,) = props["LifecycleConfiguration"]["Rules"]
    assert rule["Prefix"] == "uploads/" and rule["ExpirationInDays"] == 1  # samples/ is never expired


def test_jobs_table_has_ttl():
    props = RES["JobsTable"]["Properties"]
    assert props["KeySchema"] == [{"AttributeName": "job_id", "KeyType": "HASH"}]
    assert props["TimeToLiveSpecification"] == {"AttributeName": "expires_at", "Enabled": True}


def test_http_api_throttling_and_cors():
    props = RES["HttpApi"]["Properties"]
    assert props["DefaultRouteSettings"] == {"ThrottlingRateLimit": 5, "ThrottlingBurstLimit": 10}
    assert props["CorsConfiguration"]["AllowOrigins"] == [{"Ref": "SiteOrigin"}]
    routes = {(e["Properties"]["Method"], e["Properties"]["Path"]) for r in RES.values()
              for e in (r.get("Properties", {}).get("Events") or {}).values() if e["Type"] == "HttpApi"}
    assert routes == {("POST", "/jobs"), ("GET", "/jobs/{id}"), ("POST", "/jobs/{id}/checks"),
                      ("POST", "/jobs/{id}/retry"), ("POST", "/samples/{sample_id}")}


def test_logs_kept_seven_days_for_every_function():
    functions = {n for n, r in RES.items() if r["Type"] == "AWS::Serverless::Function"}
    groups = {r["Properties"]["LogGroupName"]["Fn::Sub"].split("${")[1].rstrip("}"): r["Properties"]["RetentionInDays"]
              for r in RES.values() if r["Type"] == "AWS::Logs::LogGroup"}
    assert set(groups) == functions and set(groups.values()) == {7}


def test_reserved_concurrency_is_optional_and_off_by_default():
    assert TEMPLATE["Parameters"]["ReservedConcurrency"]["Default"] == 0
    assert RES["WorkerFunction"]["Properties"]["ReservedConcurrentExecutions"] == {
        "Fn::If": ["UseReservedConcurrency", {"Ref": "ReservedConcurrency"}, {"Ref": "AWS::NoValue"}]}


def sub(value):
    """The string inside a Fn::Sub, or the plain string."""
    return value["Fn::Sub"] if isinstance(value, dict) else value


def as_list(value):
    return value if isinstance(value, list) else [value]


GLOBAL_PROFILE = "arn:${AWS::Partition}:bedrock:ap-south-1:${AWS::AccountId}:inference-profile/global.amazon.nova-2-lite-v1:0"
APAC_PROFILE = "arn:${AWS::Partition}:bedrock:ap-south-1:${AWS::AccountId}:inference-profile/apac.amazon.nova-pro-v1:0"
APAC_REGIONS = ("ap-south-1", "ap-southeast-1", "ap-southeast-2", "ap-northeast-1", "ap-northeast-2", "ap-northeast-3")


def test_worker_bedrock_permissions_are_exact():
    bedrock = [s for s in statements("WorkerFunction") if any(a.startswith("bedrock:") for a in actions(s))]
    assert all(actions(s) == {"bedrock:InvokeModel"} and s["Effect"] == "Allow" for s in bedrock)
    granted = {(sub(r), text(s.get("Condition"))) for s in bedrock for r in as_list(s["Resource"])}
    lite_via = text({"StringEquals": {"bedrock:InferenceProfileArn": {"Fn::Sub": GLOBAL_PROFILE}}})
    pro_via = text({"StringEquals": {"bedrock:InferenceProfileArn": {"Fn::Sub": APAC_PROFILE}}})
    global_any = text({"StringEquals": {"aws:RequestedRegion": "unspecified",
                                        "bedrock:InferenceProfileArn": {"Fn::Sub": GLOBAL_PROFILE}}})
    assert granted == {
        (GLOBAL_PROFILE, "None"),
        ("arn:${AWS::Partition}:bedrock:ap-south-1::foundation-model/amazon.nova-2-lite-v1:0", lite_via),
        ("arn:${AWS::Partition}:bedrock:::foundation-model/amazon.nova-2-lite-v1:0", global_any),
        (APAC_PROFILE, "None"),
        *{(f"arn:${{AWS::Partition}}:bedrock:{r}::foundation-model/amazon.nova-pro-v1:0", pro_via)
          for r in APAC_REGIONS},
    }
    assert not any("*" in r for r, _ in granted)


def test_stack_is_pinned_to_the_region_the_permissions_name():
    (assertion,) = TEMPLATE["Rules"]["BedrockRegion"]["Assertions"]
    assert assertion["Assert"] == {"Fn::Equals": [{"Ref": "AWS::Region"}, "ap-south-1"]}


def test_worker_permissions():
    worker = statements("WorkerFunction")
    s3 = [s for s in worker if any(a.startswith("s3:") for a in actions(s))]
    assert [actions(s) for s in s3] == [{"s3:GetObject", "s3:DeleteObject"}]
    assert text(s3[0]["Resource"]).endswith("/uploads/*'}")
    assert all(text(s["Resource"]) == text({"Fn::GetAtt": ["JobsTable", "Arn"]})
               for s in worker if any(a.startswith("dynamodb:") for a in actions(s)))
    props = RES["WorkerFunction"]["Properties"]
    assert props["EventInvokeConfig"]["MaximumRetryAttempts"] == 1
    assert actions(next(s for s in worker if s["Resource"] == {"Fn::GetAtt": ["JobsTable", "Arn"]})) == {
        "dynamodb:GetItem", "dynamodb:UpdateItem"}
    rules = props["Events"]["ManifestUploaded"]["Properties"]["Filter"]["S3Key"]["Rules"]
    assert {(r["Name"], r["Value"]) for r in rules} == {("prefix", "uploads/"), ("suffix", "manifest.json")}


@pytest.mark.parametrize("name,allowed", [
    ("CreateJobFunction", {"dynamodb:PutItem", "dynamodb:UpdateItem", "s3:PutObject"}),
    ("GetJobFunction", {"dynamodb:GetItem"}),
    ("RecheckFunction", {"dynamodb:GetItem", "dynamodb:UpdateItem"}),
    ("SampleJobFunction", {"dynamodb:PutItem", "dynamodb:UpdateItem", "s3:GetObject", "s3:PutObject"}),
    ("RetryFunction", {"dynamodb:GetItem", "dynamodb:UpdateItem", "s3:PutObject"}),
])
def test_api_functions_least_privilege(name, allowed):
    granted = set().union(*(actions(s) for s in statements(name)))
    assert granted == allowed
    assert all("*" not in a for a in granted)


def test_caps_reach_the_functions():
    env = TEMPLATE["Globals"]["Function"]["Environment"]["Variables"]
    assert env["DAILY_JOB_CAP"] == {"Ref": "DailyJobCap"} and env["IP_DAILY_JOB_CAP"] == {"Ref": "IpDailyJobCap"}
    assert TEMPLATE["Parameters"]["IpDailyJobCap"]["Default"] == 10
    assert env["IP_HASH_KEY"] == {"Ref": "AWS::StackId"}


def test_worker_timeout_matches_the_stale_claim_limit():
    from api.common import STALE_SECONDS

    assert RES["WorkerFunction"]["Properties"]["Timeout"] == 900 == STALE_SECONDS


def test_worker_failures_go_to_an_encrypted_queue():
    on_failure = RES["WorkerFunction"]["Properties"]["EventInvokeConfig"]["DestinationConfig"]["OnFailure"]
    assert on_failure == {"Type": "SQS", "Destination": {"Fn::GetAtt": ["WorkerFailures", "Arn"]}}
    queue = RES["WorkerFailures"]
    assert queue["Type"] == "AWS::SQS::Queue" and queue["Properties"]["SqsManagedSseEnabled"] is True
    lint = runpy.run_path(str(ROOT / "scripts" / "lint_template.py"))
    translated = lint["translate"](ROOT / "template.yaml")["Resources"]
    role = translated["WorkerFunctionRole"]["Properties"]["Policies"]
    sends = [s for p in role for s in p["PolicyDocument"]["Statement"] if "sqs:SendMessage" in actions(s)]
    assert sends and all(text(s["Resource"]) == text({"Fn::GetAtt": ["WorkerFailures", "Arn"]}) for s in sends)


def test_outputs():
    assert set(TEMPLATE["Outputs"]) == {"ApiUrl", "BucketName"}
