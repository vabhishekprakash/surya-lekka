import runpy
from pathlib import Path

import pytest

pytest.importorskip("cfnlint")
pytest.importorskip("samtranslator")

from cfnlint.decode import decode

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE, _ = decode(str(ROOT / "template.yaml"))
RES = TEMPLATE["Resources"]
PROFILES = ("global.amazon.nova-2-lite-v1:0", "apac.amazon.nova-pro-v1:0")


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
                      ("POST", "/samples/{sample_id}")}


def test_logs_kept_seven_days_for_every_function():
    functions = {n for n, r in RES.items() if r["Type"] == "AWS::Serverless::Function"}
    groups = {r["Properties"]["LogGroupName"]["Fn::Sub"].split("${")[1].rstrip("}"): r["Properties"]["RetentionInDays"]
              for r in RES.values() if r["Type"] == "AWS::Logs::LogGroup"}
    assert set(groups) == functions and set(groups.values()) == {7}


def test_reserved_concurrency_is_optional_and_off_by_default():
    assert TEMPLATE["Parameters"]["ReservedConcurrency"]["Default"] == 0
    assert RES["WorkerFunction"]["Properties"]["ReservedConcurrentExecutions"] == {
        "Fn::If": ["UseReservedConcurrency", {"Ref": "ReservedConcurrency"}, {"Ref": "AWS::NoValue"}]}


def test_worker_permissions():
    worker = statements("WorkerFunction")
    bedrock = [s for s in worker if actions(s) == {"bedrock:InvokeModel"}]
    resources = text([s["Resource"] for s in bedrock])
    for profile in PROFILES:
        assert f"inference-profile/{profile}" in resources
        assert f"foundation-model/{profile.split('.', 1)[1]}" in resources
    s3 = [s for s in worker if any(a.startswith("s3:") for a in actions(s))]
    assert [actions(s) for s in s3] == [{"s3:GetObject", "s3:DeleteObject"}]
    assert text(s3[0]["Resource"]).endswith("/uploads/*'}")
    assert all(text(s["Resource"]) == text({"Fn::GetAtt": ["JobsTable", "Arn"]})
               for s in worker if any(a.startswith("dynamodb:") for a in actions(s)))
    props = RES["WorkerFunction"]["Properties"]
    assert props["EventInvokeConfig"]["MaximumRetryAttempts"] == 1
    rules = props["Events"]["ManifestUploaded"]["Properties"]["Filter"]["S3Key"]["Rules"]
    assert {(r["Name"], r["Value"]) for r in rules} == {("prefix", "uploads/"), ("suffix", "manifest.json")}


@pytest.mark.parametrize("name,allowed", [
    ("CreateJobFunction", {"dynamodb:PutItem", "dynamodb:UpdateItem", "s3:PutObject"}),
    ("GetJobFunction", {"dynamodb:GetItem"}),
    ("RecheckFunction", {"dynamodb:GetItem", "dynamodb:UpdateItem"}),
    ("SampleJobFunction", {"dynamodb:PutItem", "dynamodb:UpdateItem", "s3:GetObject", "s3:PutObject"}),
])
def test_api_functions_least_privilege(name, allowed):
    granted = set().union(*(actions(s) for s in statements(name)))
    assert granted == allowed
    assert all("*" not in a for a in granted)


def test_outputs():
    assert set(TEMPLATE["Outputs"]) == {"ApiUrl", "BucketName"}
