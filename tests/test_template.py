import json
import re
import runpy
from pathlib import Path

import pytest

pytest.importorskip("cfnlint")
pytest.importorskip("samtranslator")

from cfnlint.decode import decode

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE, _ = decode(str(ROOT / "template.yaml"))
RAW = TEMPLATE["Resources"]
NO_VALUE = {"Ref": "AWS::NoValue"}


def parameters(**overrides):
    """Template defaults with overrides, as CloudFormation passes them (lists split on commas)."""
    values = {}
    for name, p in TEMPLATE["Parameters"].items():
        value = overrides.get(name, p.get("Default"))
        if p["Type"] == "CommaDelimitedList" and isinstance(value, str):
            value = value.split(",")
        values[name] = value
    return values


def evaluate(node, params, condition):
    """A condition expression, or a value used in one."""
    if isinstance(node, list):
        return [evaluate(n, params, condition) for n in node]
    if not isinstance(node, dict):
        return node
    (fn, arg), = node.items()
    if fn == "Ref":
        return params[arg]
    if fn == "Condition":
        return condition(arg)
    args = evaluate(arg, params, condition)
    return {"Fn::Equals": lambda: str(args[0]) == str(args[1]), "Fn::Not": lambda: not args[0],
            "Fn::And": lambda: all(args), "Fn::Or": lambda: any(args),
            "Fn::Join": lambda: args[0].join(args[1])}[fn]()


def resolve(**overrides):
    """The template as CloudFormation sees it for these parameters: conditions
    evaluated, Fn::If resolved, AWS::NoValue and resources with a false condition removed."""
    params, cache = parameters(**overrides), {}

    def condition(name):
        if name not in cache:
            cache[name] = evaluate(TEMPLATE["Conditions"][name], params, condition)
        return cache[name]

    lists = {n for n, p in TEMPLATE["Parameters"].items() if p["Type"] == "CommaDelimitedList"}

    def walk(node):
        if isinstance(node, dict):
            if set(node) == {"Ref"} and node["Ref"] in lists:
                return params[node["Ref"]]
            if set(node) == {"Fn::If"}:
                name, yes, no = node["Fn::If"]
                return walk(yes if condition(name) else no)
            out = {k: walk(v) for k, v in node.items()}
            return {k: v for k, v in out.items() if v != NO_VALUE}
        if isinstance(node, list):
            return [v for v in (walk(n) for n in node) if v != NO_VALUE]
        return node

    out = {}
    for section in ("Resources", "Outputs"):
        out[section] = {k: walk(v) for k, v in TEMPLATE.get(section, {}).items()
                        if "Condition" not in v or condition(v["Condition"])}
    out["Conditions"] = {name: condition(name) for name in TEMPLATE["Conditions"]}
    return out


RES = resolve()["Resources"]
SITE_ORIGIN = {"Fn::Sub": "https://${SiteDistribution.DomainName}"}


def role_of(name, resources=None):
    resources = resources or RES
    return resources[name]["Properties"]["Role"]["Fn::GetAtt"][0]


def statements(name, resources=None, xray=False):
    """A function's own permissions, from its role (X-Ray writes left out unless asked for)."""
    resources = resources or RES
    role = resources[role_of(name, resources)]
    return [s for p in role["Properties"].get("Policies", []) for s in p["PolicyDocument"]["Statement"]
            if xray or s.get("Sid") != "WriteTraces"]


def actions(statement):
    a = statement["Action"]
    return {a} if isinstance(a, str) else set(a)


def text(value):
    return str(value)


def test_lints_clean_offline():
    lint = runpy.run_path(str(ROOT / "scripts" / "lint_template.py"))
    assert lint["main"]([str(ROOT / "template.yaml")]) == 0


def test_lint_needs_no_aws_configuration(tmp_path):
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
    env.update(AWS_CONFIG_FILE=str(tmp_path / "none"), AWS_SHARED_CREDENTIALS_FILE=str(tmp_path / "none"))
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "lint_template.py"), str(ROOT / "template.yaml")],
                         capture_output=True, text=True, env=env, cwd=tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr


def test_bucket_is_private_encrypted_and_expires_uploads():
    props = RES["UploadBucket"]["Properties"]
    assert all(props["PublicAccessBlockConfiguration"].values()) and len(props["PublicAccessBlockConfiguration"]) == 4
    assert props["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]["ServerSideEncryptionByDefault"] == {
        "SSEAlgorithm": "AES256"}
    (cors,) = props["CorsConfiguration"]["CorsRules"]
    assert cors["AllowedOrigins"] == [SITE_ORIGIN] and cors["AllowedMethods"] == ["POST"]
    (rule,) = props["LifecycleConfiguration"]["Rules"]
    assert rule["Prefix"] == "uploads/" and rule["ExpirationInDays"] == 1  # samples/ is never expired


def test_jobs_table_has_ttl():
    props = RES["JobsTable"]["Properties"]
    assert props["KeySchema"] == [{"AttributeName": "job_id", "KeyType": "HASH"}]
    assert props["TimeToLiveSpecification"] == {"AttributeName": "expires_at", "Enabled": True}


def test_http_api_throttling_and_cors():
    props = RES["HttpApi"]["Properties"]
    assert props["DefaultRouteSettings"] == {"ThrottlingRateLimit": 5, "ThrottlingBurstLimit": 10}
    assert props["CorsConfiguration"]["AllowOrigins"] == [SITE_ORIGIN]
    routes = {(e["Properties"]["Method"], e["Properties"]["Path"]) for r in RES.values()
              for e in (r.get("Properties", {}).get("Events") or {}).values() if e["Type"] == "HttpApi"}
    assert routes == {("POST", "/jobs"), ("GET", "/jobs/{id}"), ("POST", "/jobs/{id}/checks"),
                      ("POST", "/jobs/{id}/retry"), ("POST", "/samples/{sample_id}"), ("POST", "/checks")}


def test_logs_kept_seven_days_for_every_function():
    functions = {n for n, r in RES.items() if r["Type"] == "AWS::Serverless::Function"}
    groups = {r["Properties"]["LogGroupName"]["Fn::Sub"].split("${")[1].rstrip("}"): r["Properties"]["RetentionInDays"]
              for r in RES.values() if r["Type"] == "AWS::Logs::LogGroup"}
    assert set(groups) == functions and set(groups.values()) == {7}


def test_reserved_concurrency_is_optional_and_off_by_default():
    assert TEMPLATE["Parameters"]["ReservedConcurrency"]["Default"] == 0
    note = " ".join(TEMPLATE["Parameters"]["ReservedConcurrency"]["Description"].split())
    assert "at least 100" in note and "at least 10 " not in note
    assert RAW["WorkerFunction"]["Properties"]["ReservedConcurrentExecutions"] == {
        "Fn::If": ["UseReservedConcurrency", {"Ref": "ReservedConcurrency"}, {"Ref": "AWS::NoValue"}]}


def sub(value):
    """The string inside a Fn::Sub, or the plain string."""
    return value["Fn::Sub"] if isinstance(value, dict) else value


def as_list(value):
    return value if isinstance(value, list) else [value]


PROFILE = "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:inference-profile/${ModelId}"
VIA_PROFILE = text({"StringEquals": {"bedrock:InferenceProfileArn": {"Fn::Sub": PROFILE}}})
APAC_ARNS = [f"arn:aws:bedrock:{r}::foundation-model/amazon.nova-pro-v1:0" for r in
             ("ap-south-1", "ap-southeast-1", "ap-southeast-2", "ap-northeast-1")]
GLOBAL_ARN = "arn:aws:bedrock:::foundation-model/amazon.nova-2-lite-v1:0"
LITE_HERE = "arn:aws:bedrock:ap-south-1::foundation-model/amazon.nova-2-lite-v1:0"


def bedrock_grants(**overrides):
    resources = resolve(**overrides)["Resources"]
    bedrock = [s for s in statements("WorkerFunction", resources)
               if any(a.startswith("bedrock:") for a in actions(s))]
    assert all(actions(s) == {"bedrock:InvokeModel"} and s["Effect"] == "Allow" for s in bedrock)
    return {(sub(r), text(s.get("Condition"))) for s in bedrock for r in as_list(s["Resource"])}


def test_no_bedrock_permission_while_reading_is_off():
    assert TEMPLATE["Parameters"]["ReadingEngine"]["Default"] == "none"
    assert bedrock_grants() == set()
    assert bedrock_grants(ModelId="global.amazon.nova-2-lite-v1:0", GlobalModelArns=GLOBAL_ARN) == set()


def test_bedrock_permission_for_a_regional_inference_profile():
    assert bedrock_grants(ReadingEngine="nova", ModelId="apac.amazon.nova-pro-v1:0",
                          ProfileModelArns=",".join(APAC_ARNS)) == {
        (PROFILE, "None"), *{(arn, VIA_PROFILE) for arn in APAC_ARNS}}


def test_bedrock_permission_for_a_global_inference_profile():
    global_via = text({"StringEquals": {"aws:RequestedRegion": "unspecified",
                                        "bedrock:InferenceProfileArn": {"Fn::Sub": PROFILE}}})
    assert bedrock_grants(ReadingEngine="nova", ModelId="global.amazon.nova-2-lite-v1:0",
                          ProfileModelArns=LITE_HERE, GlobalModelArns=GLOBAL_ARN) == {
        (PROFILE, "None"), (LITE_HERE, VIA_PROFILE), (GLOBAL_ARN, global_via)}


def test_bedrock_permission_for_a_model_in_the_stack_region():
    assert bedrock_grants(ReadingEngine="nova", ModelId="amazon.nova-pro-v1:0") == {
        ("arn:${AWS::Partition}:bedrock:${AWS::Region}::foundation-model/${ModelId}", "None")}


def test_model_id_cannot_widen_the_grant():
    allowed = re.compile(TEMPLATE["Parameters"]["ModelId"]["AllowedPattern"])
    assert allowed.fullmatch("amazon.nova-pro-v1:0") and allowed.fullmatch("global.amazon.nova-2-lite-v1:0")
    assert not any(allowed.fullmatch(bad) for bad in ("*", "amazon.*", "a/b", "x" * 129))
    rule = TEMPLATE["Rules"]["NovaNeedsAModel"]
    assert rule["RuleCondition"] == {"Fn::Equals": [{"Ref": "ReadingEngine"}, "nova"]}
    assert rule["Assertions"][0]["Assert"] == {"Fn::Not": [{"Fn::Equals": [{"Ref": "ModelId"}, ""]}]}


def test_reading_engines_match_the_api():
    from api.common import READING_ENGINES

    assert TEMPLATE["Parameters"]["ReadingEngine"]["AllowedValues"] == ["none", *READING_ENGINES]
    env = TEMPLATE["Globals"]["Function"]["Environment"]["Variables"]
    assert env["READING_ENGINE"] == {"Ref": "ReadingEngine"}
    assert RAW["WorkerFunction"]["Properties"]["Environment"]["Variables"]["MODEL_ID"] == {"Ref": "ModelId"}


def test_stack_deploys_only_in_supported_regions():
    (assertion,) = TEMPLATE["Rules"]["SupportedRegion"]["Assertions"]
    assert assertion["Assert"] == {"Fn::Contains": [["ap-south-1", "ap-southeast-2"], {"Ref": "AWS::Region"}]}
    assert "BedrockRegion" not in TEMPLATE["Rules"]


def test_every_arn_is_built_from_the_stack_region_and_account():
    arns = re.findall(r"arn:[^\s\"']+", (ROOT / "template.yaml").read_text(encoding="utf-8"))
    assert arns and all(a.startswith("arn:${AWS::Partition}:") for a in arns)
    assert not any(re.search(r"(ap|us|eu)-[a-z]+-[0-9]|[0-9]{12}", a) for a in arns)
    assert all(":${AWS::Region}:" in a for a in arns if ":bedrock:" in a)


def test_worker_permissions():
    worker = statements("WorkerFunction")
    s3 = [s for s in worker if any(a.startswith("s3:") for a in actions(s))]
    assert [actions(s) for s in s3] == [{"s3:GetObject", "s3:DeleteObject"}]
    assert text(s3[0]["Resource"]).endswith("/uploads/*'}")
    assert all(text(s["Resource"]) == text({"Fn::GetAtt": ["JobsTable", "Arn"]})
               for s in worker if any(a.startswith("dynamodb:") for a in actions(s)))
    props = RAW["WorkerFunction"]["Properties"]
    assert props["EventInvokeConfig"]["MaximumRetryAttempts"] == 1
    assert actions(next(s for s in worker if s["Resource"] == {"Fn::GetAtt": ["JobsTable", "Arn"]})) == {
        "dynamodb:GetItem", "dynamodb:UpdateItem"}
    rules = props["Events"]["ManifestUploaded"]["Properties"]["Filter"]["S3Key"]["Rules"]
    assert {(r["Name"], r["Value"]) for r in rules} == {("prefix", "uploads/"), ("suffix", "manifest.json")}


@pytest.mark.parametrize("name,allowed", [
    ("CreateJobFunction", {"dynamodb:PutItem", "dynamodb:UpdateItem", "s3:PutObject"}),
    ("GetJobFunction", {"dynamodb:GetItem", "s3:GetObject"}),  # signs links to sample pages only
    ("RecheckFunction", {"dynamodb:GetItem", "dynamodb:UpdateItem", "secretsmanager:GetSecretValue"}),
    ("SampleJobFunction", {"dynamodb:PutItem", "dynamodb:UpdateItem", "s3:GetObject", "s3:PutObject",
                           "secretsmanager:GetSecretValue"}),
    ("RetryFunction", {"dynamodb:GetItem", "dynamodb:UpdateItem", "s3:PutObject"}),
    ("ManualCheckFunction", {"dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                             "secretsmanager:GetSecretValue"}),
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
    sends = [s for p in role if "PolicyDocument" in p for s in p["PolicyDocument"]["Statement"]
             if "sqs:SendMessage" in actions(s)]
    assert sends and all(text(s["Resource"]) == text({"Fn::GetAtt": ["WorkerFailures", "Arn"]}) for s in sends)


def test_every_function_is_traced_with_permission_to_send_traces():
    assert TEMPLATE["Globals"]["Function"]["Tracing"] == "Active"
    functions = [n for n, r in RAW.items() if r["Type"] == "AWS::Serverless::Function"]
    assert functions and all("Tracing" not in RAW[n]["Properties"] for n in functions)
    lint = runpy.run_path(str(ROOT / "scripts" / "lint_template.py"))
    translated = lint["translate"](ROOT / "template.yaml")["Resources"]
    for name in functions:
        assert translated[name]["Properties"]["TracingConfig"] == {"Mode": "Active"}
        (trace,) = [s for s in statements(name, xray=True) if s.get("Sid") == "WriteTraces"]
        assert actions(trace) == {"xray:PutTraceSegments", "xray:PutTelemetryRecords"}


FUNCTIONS = ["CreateJobFunction", "GetJobFunction", "RecheckFunction", "RetryFunction", "ManualCheckFunction",
             "SampleJobFunction", "WorkerFunction"]
LOGS = {f: f.replace("Function", "Logs") for f in FUNCTIONS}


def test_every_function_has_its_own_role_and_no_aws_managed_policy():
    lint = runpy.run_path(str(ROOT / "scripts" / "lint_template.py"))
    translated = lint["translate"](ROOT / "template.yaml")["Resources"]
    roles = [n for n, r in translated.items() if r["Type"] == "AWS::IAM::Role"]
    assert sorted(roles) == sorted(f"{f}Role" for f in FUNCTIONS)
    for name in roles:
        assert "ManagedPolicyArns" not in translated[name]["Properties"], name
        assert "AWSLambdaBasicExecutionRole" not in json.dumps(translated[name])
    for f in FUNCTIONS:
        assert role_of(f) == f"{f}Role"


def test_each_function_writes_logs_only_to_its_own_log_group():
    for f in FUNCTIONS:
        (policy,) = [r for r in RAW.values() if r["Type"] == "AWS::IAM::Policy"
                     and r["Properties"]["Roles"] == [{"Ref": f"{f}Role"}]]
        (statement,) = policy["Properties"]["PolicyDocument"]["Statement"]
        assert actions(statement) == {"logs:CreateLogStream", "logs:PutLogEvents"}  # never CreateLogGroup
        assert statement["Resource"] == {"Fn::GetAtt": [LOGS[f], "Arn"]}
        assert not [s for s in statements(f, xray=True) if any(a.startswith("logs:") for a in actions(s))]


def test_the_worker_may_send_to_its_failure_queue_only():
    (send,) = [s for s in statements("WorkerFunction") if "sqs:SendMessage" in actions(s)]
    assert send["Resource"] == {"Fn::GetAtt": ["WorkerFailures", "Arn"]} and actions(send) == {"sqs:SendMessage"}


def test_alarms_reach_an_email_only_when_one_is_given():
    assert TEMPLATE["Parameters"]["AlarmEmail"]["Default"] == "none"
    assert "AlarmsByEmail" not in RES  # no address, no subscription
    res = resolve(AlarmEmail="someone@example.com")["Resources"]
    assert res["AlarmsByEmail"]["Properties"]["Endpoint"] == {"Ref": "AlarmEmail"}
    pattern = re.compile(TEMPLATE["Parameters"]["AlarmEmail"]["AllowedPattern"])
    assert pattern.fullmatch("none") and pattern.fullmatch("a@b.co") and not pattern.fullmatch("not an email")
    alarms = {n: r for n, r in RAW.items() if r["Type"] == "AWS::CloudWatch::Alarm"}
    assert set(alarms) == {"FailedJobsAlarm", "FailureQueueAlarm", "LambdaErrorsAlarm", "LambdaThrottlesAlarm"}
    assert all(a["Properties"]["AlarmActions"] == [{"Ref": "Alarms"}] for a in alarms.values())
    metric = RAW["FailedJobsMetric"]["Properties"]
    assert metric["LogGroupName"] == {"Ref": "WorkerLogs"} and "job_failed" in metric["FilterPattern"]
    for name in ("LambdaErrorsAlarm", "LambdaThrottlesAlarm"):
        functions = [m["MetricStat"]["Metric"]["Dimensions"][0]["Value"] for m in alarms[name]["Properties"]["Metrics"]
                     if "MetricStat" in m]
        assert functions == [{"Ref": f} for f in FUNCTIONS]
    assert not re.search(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}", json.dumps(TEMPLATE))  # no address in the template


def test_get_job_reads_sample_page_images_only():
    (read,) = [s for s in statements("GetJobFunction") if "s3:GetObject" in actions(s)]
    assert str(read["Resource"]).endswith("/samples/*/page-*.jpg'}")


def test_bucket_name_starts_with_the_stack_prefix():
    params = TEMPLATE["Parameters"]
    assert "BucketNamePrefix" not in params and "Default" not in params["StackPrefix"]
    name = {"Fn::Sub": "${StackPrefix}-${AWS::AccountId}-${AWS::Region}"}
    assert RES["UploadBucket"]["Properties"]["BucketName"] == name
    assert TEMPLATE["Globals"]["Function"]["Environment"]["Variables"]["BUCKET_NAME"] == name
    assert "BucketNamePrefix" not in (ROOT / "template.yaml").read_text(encoding="utf-8")
    allowed = re.compile(params["StackPrefix"]["AllowedPattern"])
    assert allowed.fullmatch("surya-lekka") and allowed.fullmatch("surya-lekka-arya")
    assert not any(allowed.fullmatch(bad) for bad in ("Surya-Lekka", "-x", "a" * 32, "surya_lekka"))
    # 31 + "-" + 12-digit account + "-" + the longest Region name stays within S3's 63 characters
    assert 31 + 1 + 12 + 1 + len("ap-southeast-1") <= 63


def test_deploy_script_passes_the_stack_name_as_the_prefix():
    script = (ROOT / "scripts" / "deploy.ps1").read_text(encoding="utf-8")
    assert "StackPrefix=$StackName" in script and "-cnotmatch" in script


def test_outputs():
    assert set(resolve()["Outputs"]) == {"ApiUrl", "BucketName", "AllowedOrigin", "SiteUrl", "SiteBucketName",
                                         "DistributionId"}
    assert set(resolve(HostingEnabled="false")["Outputs"]) == {"ApiUrl", "BucketName", "AllowedOrigin"}


PSEUDO = re.compile(r"\$\{([A-Za-z0-9]+)(?:\.[A-Za-z0-9.]+)?\}")


def references(node):
    """Names a value refers to with Ref, Fn::GetAtt or Fn::Sub."""
    if isinstance(node, list):
        return set().union(*map(references, node)) if node else set()
    if not isinstance(node, dict):
        return set()
    found = set()
    for key, value in node.items():
        if key == "Ref":
            found.add(value)
        elif key == "Fn::GetAtt":
            found.add(value[0] if isinstance(value, list) else value.split(".")[0])
        elif key == "Fn::Sub":
            found |= set(PSEUDO.findall(value if isinstance(value, str) else value[0]))
        found |= references(value)
    return found


@pytest.mark.parametrize("hosting", ["true", "false"])
def test_every_reference_exists_with_hosting_on_or_off(hosting):
    resolved = resolve(HostingEnabled=hosting)
    known = set(resolved["Resources"]) | set(TEMPLATE["Parameters"])
    for section in ("Resources", "Outputs"):
        for name, body in resolved[section].items():
            assert references(body) <= known, name
            assert set(as_list(body.get("DependsOn", []))) <= known, name


def test_hosting_serves_a_private_bucket_through_cloudfront_over_https():
    assert TEMPLATE["Parameters"]["HostingEnabled"]["Default"] == "true"
    res = resolve(HostingEnabled="true")["Resources"]
    bucket = res["SiteBucket"]["Properties"]
    assert all(bucket["PublicAccessBlockConfiguration"].values()) and len(bucket["PublicAccessBlockConfiguration"]) == 4
    assert "WebsiteConfiguration" not in bucket
    config = res["SiteDistribution"]["Properties"]["DistributionConfig"]
    assert config["DefaultRootObject"] == "index.html"
    behaviour = config["DefaultCacheBehavior"]
    assert behaviour["ViewerProtocolPolicy"] == "https-only" and behaviour["AllowedMethods"] == ["GET", "HEAD"]
    (origin,) = config["Origins"]
    assert origin["OriginAccessControlId"] == {"Fn::GetAtt": ["SiteOriginAccess", "Id"]}
    assert origin["S3OriginConfig"] == {"OriginAccessIdentity": ""}
    oac = res["SiteOriginAccess"]["Properties"]["OriginAccessControlConfig"]
    assert (oac["OriginAccessControlOriginType"], oac["SigningBehavior"], oac["SigningProtocol"]) == (
        "s3", "always", "sigv4")
    allow, deny = res["SiteBucketPolicy"]["Properties"]["PolicyDocument"]["Statement"]
    assert allow["Principal"] == {"Service": "cloudfront.amazonaws.com"} and allow["Action"] == "s3:GetObject"
    assert allow["Condition"] == {"StringEquals": {"AWS:SourceArn": {"Fn::Sub": (
        "arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${SiteDistribution}")}}}
    assert deny["Effect"] == "Deny" and deny["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}
    for cors in (res["HttpApi"]["Properties"]["CorsConfiguration"]["AllowOrigins"],
                 res["UploadBucket"]["Properties"]["CorsConfiguration"]["CorsRules"][0]["AllowedOrigins"]):
        assert cors == [SITE_ORIGIN]  # the CloudFront domain only, never a wildcard


def test_without_hosting_the_api_accepts_only_the_local_origin():
    res = resolve(HostingEnabled="false")["Resources"]
    assert not {n for n, r in res.items() if r["Type"].startswith("AWS::CloudFront::")}
    assert "SiteBucket" not in res and "SiteBucketPolicy" not in res
    for cors in (res["HttpApi"]["Properties"]["CorsConfiguration"]["AllowOrigins"],
                 res["UploadBucket"]["Properties"]["CorsConfiguration"]["CorsRules"][0]["AllowedOrigins"]):
        assert cors == [{"Ref": "SiteOrigin"}]
    assert TEMPLATE["Parameters"]["SiteOrigin"]["Default"] == "http://127.0.0.1:8000"
    allowed = re.compile(TEMPLATE["Parameters"]["SiteOrigin"]["AllowedPattern"])
    assert allowed.fullmatch("http://127.0.0.1:8000")
    assert not any(allowed.fullmatch(bad) for bad in ("*", "https://*.example.com", "http://127.0.0.1:8000/"))


def textract_grants(**overrides):
    resources = resolve(**overrides)["Resources"]
    return [s for s in statements("WorkerFunction", resources) if any(a.startswith("textract:") for a in actions(s))]


@pytest.mark.parametrize("engine", ["none", "nova", "textract"])
def test_each_engine_gets_only_its_own_permission(engine):
    params = {"ReadingEngine": engine, "ModelId": "amazon.nova-pro-v1:0" if engine == "nova" else ""}
    textract, bedrock = textract_grants(**params), bedrock_grants(**params)
    assert bool(textract) == (engine == "textract") and bool(bedrock) == (engine == "nova")
    if engine == "textract":
        (grant,) = textract
        # AnalyzeDocument has no resource types in the Service Authorization Reference.
        assert actions(grant) == {"textract:AnalyzeDocument"} and grant["Resource"] == "*"
        assert grant["Effect"] == "Allow"
        assert grant["Condition"] == {"StringEquals": {"aws:RequestedRegion": {"Ref": "AWS::Region"}}}
    # Pages go to Textract as bytes, so nothing but the worker's own S3 statement touches the bucket.
    s3 = [s for s in statements("WorkerFunction", resolve(**params)["Resources"])
          if any(a.startswith("s3:") for a in actions(s))]
    assert [actions(s) for s in s3] == [{"s3:GetObject", "s3:DeleteObject"}]


def test_textract_condition_and_template_wording():
    assert TEMPLATE["Conditions"]["TextractReading"] == {"Fn::Equals": [{"Ref": "ReadingEngine"}, "textract"]}
    source = (ROOT / "template.yaml").read_text(encoding="utf-8")
    assert "no resource-level permissions" in source


def test_daily_page_cap_reaches_the_functions():
    param = TEMPLATE["Parameters"]["DailyPageCap"]
    assert param["Default"] == 300 and param["MinValue"] == 0 and param["Type"] == "Number"
    env = TEMPLATE["Globals"]["Function"]["Environment"]["Variables"]
    assert env["DAILY_PAGE_CAP"] == {"Ref": "DailyPageCap"}


def test_retry_writes_only_manifests():
    s3 = [s for s in statements("RetryFunction") if any(a.startswith("s3:") for a in actions(s))]
    assert [actions(s) for s in s3] == [{"s3:PutObject"}]
    assert text(s3[0]["Resource"]).endswith("/uploads/*/manifest.json'}")


def test_get_job_reads_only_the_synthetic_samples():
    reads = [s for s in statements("GetJobFunction") if "s3:GetObject" in actions(s)]
    assert reads and all(str(s["Resource"]).endswith("/samples/*'}") or "/samples/*" in str(s["Resource"])
                         for s in reads)


def test_a_second_origin_is_accepted_only_when_named():
    res = resolve(HostingEnabled="false", SecondSiteOrigin="https://main.d2sqhcgne0nq26.amplifyapp.com")["Resources"]
    for cors in (res["HttpApi"]["Properties"]["CorsConfiguration"]["AllowOrigins"],
                 res["UploadBucket"]["Properties"]["CorsConfiguration"]["CorsRules"][0]["AllowedOrigins"]):
        assert cors == [{"Ref": "SiteOrigin"}, {"Ref": "SecondSiteOrigin"}]
    res = resolve(HostingEnabled="true", SecondSiteOrigin="https://main.d2sqhcgne0nq26.amplifyapp.com")["Resources"]
    assert res["HttpApi"]["Properties"]["CorsConfiguration"]["AllowOrigins"] == [SITE_ORIGIN]
    allowed = re.compile(TEMPLATE["Parameters"]["SecondSiteOrigin"]["AllowedPattern"])
    assert TEMPLATE["Parameters"]["SecondSiteOrigin"]["Default"] == "none"
    assert allowed.fullmatch("https://main.d2sqhcgne0nq26.amplifyapp.com") and allowed.fullmatch("none")
    for bad in ("*", "http://main.d2sqhcgne0nq26.amplifyapp.com", "https://a.com/path", "https://*.amplifyapp.com"):
        assert not allowed.fullmatch(bad), bad


def test_sam_keeps_the_api_cors_an_object_with_every_origin():
    # SAM moves the HTTP API's CORS into the OpenAPI body. One !If returning the whole list used to
    # replace the CORS object there, and API Gateway kept the old origins.
    lint = runpy.run_path(str(ROOT / "scripts" / "lint_template.py"))
    body = lint["translate"](ROOT / "template.yaml")["Resources"]["HttpApi"]["Properties"]["Body"]
    cors = body["x-amazon-apigateway-cors"]
    assert isinstance(cors, dict) and isinstance(cors["allowOrigins"], list) and len(cors["allowOrigins"]) == 2
    name, _, otherwise = cors["allowOrigins"][1]["Fn::If"]  # SAM fills in the parameter's value here
    assert name == "SecondOrigin" and otherwise == {"Ref": "AWS::NoValue"}


SIGNERS = {"RecheckFunction", "SampleJobFunction", "ManualCheckFunction", "WorkerFunction"}


def test_the_confirm_secret_is_generated_and_readable_only_by_the_signing_functions():
    secret = RAW["ConfirmSecret"]
    assert secret["Type"] == "AWS::SecretsManager::Secret"
    assert "SecretString" not in secret["Properties"]  # generated by CloudFormation, never in the repo
    assert secret["Properties"]["GenerateSecretString"]["PasswordLength"] >= 32
    env = TEMPLATE["Globals"]["Function"]["Environment"]["Variables"]
    assert env["CONFIRM_SECRET_ARN"] == {"Ref": "ConfirmSecret"}  # the ARN, not the value
    for name, resource in RAW.items():
        if resource["Type"] != "AWS::Serverless::Function":
            continue
        reads = [s for s in statements(name) if "secretsmanager:GetSecretValue" in actions(s)]
        if name in SIGNERS:
            assert [s["Resource"] for s in reads] == [{"Ref": "ConfirmSecret"}], name
            assert all(actions(s) == {"secretsmanager:GetSecretValue"} for s in reads), name
        else:
            assert not reads, name
