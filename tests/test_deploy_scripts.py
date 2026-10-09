import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_render_samples_writes_the_layout_the_sample_route_reads(tmp_path):
    pytest.importorskip("pymupdf")
    render = runpy.run_path(str(ROOT / "scripts" / "render_samples.py"))["render_samples"]
    assert render(tmp_path) == {"S1": 2, "S2": 2, "S3": 2}
    for sid in ("S1", "S2", "S3"):
        folder = tmp_path / sid
        pages = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["pages"]
        images = sorted(folder.glob("page-*.jpg"))
        assert [p.name for p in images] == [f"page-{n:02d}.jpg" for n in range(1, pages + 1)]
        assert all(p.read_bytes()[:3] == b"\xff\xd8\xff" and p.stat().st_size <= 3_750_000 for p in images)
        reading = json.loads((folder / "reading.json").read_text(encoding="utf-8"))
        assert reading["sample_id"] == sid and reading["reading"] == "saved"


POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
windows_powershell = pytest.mark.skipif(
    not POWERSHELL or sys.platform != "win32" or not (ROOT / ".venv" / "Scripts" / "python.exe").is_file(),
    reason="the deploy scripts run on Windows with the repo's .venv")
ACCOUNT = "123456789012"
# aws and sam replaced by functions that record their arguments: nothing reaches AWS.
FAKES = r"""
$global:calls = @()
function global:aws {
    $global:calls += ,("aws " + ($args -join " "))
    $global:LASTEXITCODE = 0
    switch ($args[0]) {
        "sts" { return $env:FAKE_ACCOUNT }
        "bedrock" {
            if ($args[1] -eq "get-inference-profile" -and $env:FAKE_PROFILE) { return $env:FAKE_PROFILE }
            if ($args[1] -eq "get-foundation-model" -and $env:FAKE_MODEL) { return "{}" }
            $global:LASTEXITCODE = 254
            return
        }
        "cloudformation" { return $env:FAKE_STACK }
        "organizations" {
            if ($env:FAKE_POLICY) { return $env:FAKE_POLICY }
            $global:LASTEXITCODE = 254
            return
        }
        default { return "" }
    }
}
function global:sam { $global:calls += ,("sam " + ($args -join " ")); $global:LASTEXITCODE = 0 }
function global:gh { $global:calls += ,("gh " + ($args -join " ")); $global:LASTEXITCODE = 0 }
"""


# deploy.ps1 writes its build output here in tests, never into the repo's .build.
BUILD = Path(tempfile.mkdtemp(prefix="surya-deploy-test-"))


def run_ps(script, params, env, timeout=180):
    if script == "deploy.ps1":
        params += f" -BuildDir '{BUILD}'"
    command = (FAKES + f"try {{ & '{ROOT / 'scripts' / script}' {params}; $code = $LASTEXITCODE }}\n"
               "catch { 'THROWN: ' + $_.Exception.Message; $code = 1 }\n"
               "finally { $global:calls | ForEach-Object { 'CALL: ' + $_ } }\nexit $code")
    run = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
                         capture_output=True, text=True, timeout=timeout, cwd=ROOT,
                         env={**os.environ, "FAKE_ACCOUNT": ACCOUNT, **env})
    calls = [line[6:] for line in run.stdout.splitlines() if line.startswith("CALL: ")]
    return run, calls


def outputs(*pairs):
    return json.dumps([{"OutputKey": k, "OutputValue": v} for k, v in pairs])


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is not installed")
@pytest.mark.parametrize("script", ["deploy.ps1", "smoke_test.ps1", "aws_target.ps1"])
def test_powershell_scripts_parse(script):
    command = ("$errors = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
               f"'{ROOT / 'scripts' / script}', [ref]$null, [ref]$errors); if ($errors) {{ $errors; exit 1 }}")
    assert subprocess.run([POWERSHELL, "-NoProfile", "-Command", command], capture_output=True).returncode == 0


@windows_powershell
@pytest.mark.parametrize("script", ["deploy.ps1", "smoke_test.ps1"])
def test_scripts_stop_before_any_change_on_another_account(script):
    run, calls = run_ps(script, f"-Profile default -Region ap-south-1 -ExpectedAccount {ACCOUNT}",
                        {"FAKE_ACCOUNT": "111111111111"})
    assert run.returncode != 0
    assert "AWS account: 111111111111" in run.stdout and "Region:      ap-south-1" in run.stdout
    assert f"not {ACCOUNT}" in run.stdout + run.stderr
    assert calls == ["aws sts get-caller-identity --profile default --region ap-south-1 --query Account --output text"]


@windows_powershell
@pytest.mark.parametrize("script", ["deploy.ps1", "smoke_test.ps1"])
def test_scripts_need_the_expected_account(script):
    source = (ROOT / "scripts" / script).read_text(encoding="utf-8")
    assert r"[Parameter(Mandatory = $true)][ValidatePattern('^\d{12}$')][string]$ExpectedAccount," in source
    assert re.search(r"(?<!\d)\d{12}(?!\d)", source) is None  # no account number in the scripts
    run, calls = run_ps(script, "-Profile default -Region ap-south-1", {})
    assert run.returncode != 0 and "ExpectedAccount" in run.stdout + run.stderr
    assert calls == []


@windows_powershell
def test_deploy_with_hosting_and_a_cross_region_profile():
    profile = {"inferenceProfileId": "apac.amazon.nova-pro-v1:0", "models": [
        {"modelArn": f"arn:aws:bedrock:{r}::foundation-model/amazon.nova-pro-v1:0"} for r in ("ap-south-1", "ap-southeast-1")]}
    api = "https://abc123.execute-api.ap-south-1.amazonaws.com"
    run, calls = run_ps("deploy.ps1", f"-Profile default -Region ap-south-1 -ExpectedAccount {ACCOUNT} "
                        "-ReadingEngine nova -ModelId apac.amazon.nova-pro-v1:0", {
                            "FAKE_PROFILE": json.dumps(profile),
                            "FAKE_STACK": outputs(("ApiUrl", api), ("BucketName", "uploads-bucket"),
                                                  ("SiteBucketName", "site-bucket"), ("DistributionId", "E123"),
                                                  ("SiteUrl", "https://d111.cloudfront.net/"))})
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.index(f"AWS account: {ACCOUNT}") < run.stdout.index("== Build")
    (deploy,) = [c for c in calls if c.startswith("sam deploy")]
    overrides = deploy.split("--parameter-overrides ")[1].split()
    assert "--profile default --region ap-south-1" in deploy
    assert "--resolve-s3 --save-params --no-confirm-changeset --no-fail-on-empty-changeset" in deploy
    assert overrides[:5] == ["StackPrefix=surya-lekka", "HostingEnabled=true", "SiteOrigin=http://127.0.0.1:8000",
                             "ReadingEngine=nova", "ModelId=apac.amazon.nova-pro-v1:0"]
    assert overrides[5] == "ProfileModelArns=" + ",".join(m["modelArn"] for m in profile["models"])
    assert overrides[6] == "GlobalModelArns=none"
    uploads = {c.split("s3://site-bucket/")[1].split()[0]: c for c in calls if "s3://site-bucket/" in c}
    assert set(uploads) == {p.name for p in (ROOT / "web").iterdir() if p.is_file()}
    assert "--content-type text/javascript; charset=utf-8" in uploads["app.js"]
    assert "--content-type text/html; charset=utf-8" in uploads["index.html"]
    assert calls[-1].startswith("aws cloudfront create-invalidation --distribution-id E123 --paths /*")
    config = (BUILD / "web" / "config.js").read_text(encoding="utf-8")
    assert f'"API_BASE": "{api}", "REGION": "ap-south-1", "CROSS_REGION": true' in config
    assert f"API URL: {api}" in run.stdout and "Site:    https://d111.cloudfront.net/" in run.stdout


@windows_powershell
def test_deploy_without_hosting_prints_how_to_open_the_site_locally():
    api = "https://abc123.execute-api.ap-southeast-2.amazonaws.com"
    run, calls = run_ps("deploy.ps1", f"-Profile default -Region ap-southeast-2 -ExpectedAccount {ACCOUNT} "
                        "-HostingEnabled false", {
        "FAKE_STACK": outputs(("ApiUrl", api), ("BucketName", "uploads-bucket"))})
    assert run.returncode == 0, run.stdout + run.stderr
    assert not any(c.startswith(("aws bedrock", "aws cloudfront")) or "s3://site" in c for c in calls)
    (deploy,) = [c for c in calls if c.startswith("sam deploy")]
    assert "HostingEnabled=false" in deploy and "ReadingEngine=none" in deploy and "ProfileModelArns=none" in deploy
    assert f"python -m src.api.local_server --port 8000 --api {api} --region ap-southeast-2" in run.stdout
    assert "Then open http://127.0.0.1:8000/" in run.stdout


def test_deploy_script_steps():
    script = (ROOT / "scripts" / "deploy.ps1").read_text(encoding="utf-8")
    order = ["Confirm-AwsTarget -AwsProfile", "Get-ModelAccess\n", r"scripts\render_samples.py --out",
             "sam build --template-file", "sam deploy @deployArgs", "describe-stacks --stack-name",
             'aws s3 cp (Join-Path $BuildDir "samples")', r"scripts\build_site.py @siteArgs", "create-invalidation",
             "{ gh variable set", "{ gh workflow run", 'Write-Host "API URL']
    positions = [script.index(step) for step in order]
    assert positions == sorted(positions)
    assert "--guided" not in script and "samconfig.toml" in script  # unattended, and the first run saves samconfig.toml
    assert "ap-southeast-1" not in script and "ap-northeast" not in script  # Regions come from the profile


def test_build_site_writes_the_config_for_one_deployment(tmp_path):
    build = runpy.run_path(str(ROOT / "scripts" / "build_site.py"))
    api = "https://abc123.execute-api.ap-south-1.amazonaws.com"
    names = build["build_site"](api + "/", "ap-south-1", False, tmp_path)
    assert names == sorted(p.name for p in (ROOT / "web").iterdir() if p.is_file())
    for name in names:
        if name != "config.js":
            assert (tmp_path / name).read_bytes() == (ROOT / "web" / name).read_bytes()
    config = (tmp_path / "config.js").read_text(encoding="utf-8")
    assert ('window.SURYA_CONFIG = {"API_BASE": "' + api + '", "REGION": "ap-south-1", "CROSS_REGION": false, '
            '"ENGINE": "none", "AI_OPT_OUT": false};') in config
    from api.local_server import config_js

    assert config == config_js(api, "ap-south-1", False)  # the local --api mode serves the same file
    for api_base, region in (("http://abc.example", "ap-south-1"), ('https://x.example/";alert(1)//', "ap-south-1"),
                             (api, "us-east-1")):
        with pytest.raises(ValueError):
            build["build_site"](api_base, region, False, tmp_path)


def test_build_site_regions_match_the_template_and_the_web_app():
    from cfnlint.decode import decode

    regions = runpy.run_path(str(ROOT / "scripts" / "build_site.py"))["REGIONS"]
    template, _ = decode(str(ROOT / "template.yaml"))
    assert list(regions) == template["Rules"]["SupportedRegion"]["Assertions"][0]["Assert"]["Fn::Contains"][0]
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert all(f'"{r}": "AWS\'s ' in app for r in regions)


@windows_powershell
def test_deploy_with_textract_needs_no_model_lookup():
    api = "https://abc123.execute-api.ap-south-1.amazonaws.com"
    run, calls = run_ps("deploy.ps1", f"-Profile default -Region ap-south-1 -ExpectedAccount {ACCOUNT} "
                        "-HostingEnabled false -ReadingEngine textract", {
                            "FAKE_STACK": outputs(("ApiUrl", api), ("BucketName", "uploads-bucket"))})
    assert run.returncode == 0, run.stdout + run.stderr
    assert not any(c.startswith("aws bedrock") for c in calls)
    (deploy,) = [c for c in calls if c.startswith("sam deploy")]
    assert "ReadingEngine=textract" in deploy and "ProfileModelArns=none" in deploy and "GlobalModelArns=none" in deploy


def opt_out_policy(services):
    return json.dumps({"EffectivePolicy": {"PolicyType": "AISERVICES_OPT_OUT_POLICY",
                                           "PolicyContent": json.dumps({"services": services})}})


@windows_powershell
@pytest.mark.parametrize("policy,opted_out", [
    (opt_out_policy({"default": {"opt_out_policy": "optOut"}}), True),
    (opt_out_policy({"default": {"opt_out_policy": "optIn"}}), False),
    (None, False),  # the call is denied or fails
])
def test_deploy_with_textract_checks_the_ai_opt_out_policy(policy, opted_out):
    api = "https://abc123.execute-api.ap-south-1.amazonaws.com"
    env = {"FAKE_STACK": outputs(("ApiUrl", api), ("BucketName", "uploads-bucket"), ("SiteBucketName", "site-bucket"),
                                 ("DistributionId", "E123"), ("SiteUrl", "https://d111.cloudfront.net/"))}
    if policy:
        env["FAKE_POLICY"] = policy
    run, calls = run_ps("deploy.ps1", f"-Profile default -Region ap-south-1 -ExpectedAccount {ACCOUNT} "
                        "-ReadingEngine textract", env)
    assert run.returncode == 0, run.stdout + run.stderr
    (check,) = [c for c in calls if c.startswith("aws organizations")]
    assert check.startswith(f"aws organizations describe-effective-policy --policy-type AISERVICES_OPT_OUT_POLICY "
                            f"--target-id {ACCOUNT}")
    config = (BUILD / "web" / "config.js").read_text(encoding="utf-8")
    assert f'"ENGINE": "textract", "AI_OPT_OUT": {str(opted_out).lower()}' in config
    assert f"Textract opt-out confirmed: {opted_out}" in run.stdout


@windows_powershell
def test_deploy_without_textract_does_not_ask_for_the_policy():
    api = "https://abc123.execute-api.ap-south-1.amazonaws.com"
    run, calls = run_ps("deploy.ps1", f"-Profile default -Region ap-south-1 -ExpectedAccount {ACCOUNT} "
                        "-HostingEnabled false", {"FAKE_STACK": outputs(("ApiUrl", api), ("BucketName", "b"))})
    assert run.returncode == 0 and not any(c.startswith("aws organizations") for c in calls)
    assert "--engine none" in run.stdout


@windows_powershell
def test_deploy_with_pages_sets_the_repo_variables_then_runs_the_workflow():
    api = "https://abc123.execute-api.ap-south-1.amazonaws.com"
    run, calls = run_ps("deploy.ps1", f"-Profile default -Region ap-south-1 -ExpectedAccount {ACCOUNT} "
                        "-HostingEnabled false -ReadingEngine textract -SiteOrigin https://example-user.github.io "
                        "-Pages", {"FAKE_STACK": outputs(("ApiUrl", api), ("BucketName", "b")),
                                   "FAKE_POLICY": opt_out_policy({"default": {"opt_out_policy": "optOut"}})})
    assert run.returncode == 0, run.stdout + run.stderr
    order = [next(i for i, c in enumerate(calls) if c.startswith(prefix))
             for prefix in ("sam deploy", "aws organizations", "gh variable set", "gh workflow run")]
    assert order == sorted(order)
    variables = {c.split()[3]: c.split("--body ")[1] for c in calls if c.startswith("gh variable set")}
    assert variables == {"SURYA_API_URL": api, "SURYA_REGION": "ap-south-1", "SURYA_READING_ENGINE": "textract",
                         "SURYA_AI_OPT_OUT": "true", "SURYA_CROSS_REGION": "false"}
    assert calls[-1] == "gh workflow run pages.yml --ref main"
    (deploy,) = [c for c in calls if c.startswith("sam deploy")]
    assert "SiteOrigin=https://example-user.github.io" in deploy
    assert "Site:    https://example-user.github.io/surya-lekka/" in run.stdout


@windows_powershell
def test_pages_says_not_opted_out_when_the_policy_is_unreadable():
    run, calls = run_ps("deploy.ps1", f"-Profile default -Region ap-south-1 -ExpectedAccount {ACCOUNT} "
                        "-HostingEnabled false -ReadingEngine textract -SiteOrigin https://example-user.github.io "
                        "-Pages", {"FAKE_STACK": outputs(("ApiUrl", "https://abc.example.com"), ("BucketName", "b"))})
    assert run.returncode == 0, run.stdout + run.stderr
    assert "gh variable set SURYA_AI_OPT_OUT --body false" in calls


@windows_powershell
@pytest.mark.parametrize("origin", ["http://127.0.0.1:8000", "https://example-user.github.io/surya-lekka",
                                    "https://example.com"])
def test_pages_needs_a_github_pages_origin_and_changes_nothing_otherwise(origin):
    run, calls = run_ps("deploy.ps1", f"-Profile default -Region ap-south-1 -ExpectedAccount {ACCOUNT} "
                        f"-HostingEnabled false -SiteOrigin {origin} -Pages", {})
    assert run.returncode != 0 and "github.io" in run.stdout + run.stderr
    assert not any(c.startswith(("sam", "gh", "aws s3")) for c in calls)


def test_pages_workflow_runs_only_by_hand_and_holds_no_aws_credentials():
    import yaml

    text = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {"workflow_dispatch": None}
    assert workflow["permissions"] == {"contents": "read", "pages": "write", "id-token": "write"}
    assert "secrets." not in text and "aws-actions" not in text and "AWS_" not in text
    for name in ("SURYA_API_URL", "SURYA_REGION", "SURYA_READING_ENGINE", "SURYA_AI_OPT_OUT", "SURYA_CROSS_REGION"):
        assert f"vars.{name}" in text
    steps = [st.get("uses", st.get("run", "")) for st in workflow["jobs"]["publish"]["steps"]]
    assert any("scripts/build_site.py" in st for st in steps)
    assert steps[-1].startswith("actions/deploy-pages@")


@pytest.mark.parametrize("value,opted_out", [("true", True), ("false", False), ("", False), ("yes", False),
                                             ("TRUE", False), ("unknown", False)])
def test_build_site_takes_the_opt_out_only_from_an_exact_true(tmp_path, value, opted_out):
    build = runpy.run_path(str(ROOT / "scripts" / "build_site.py"))
    assert build["main"](["--api", "https://abc123.execute-api.ap-south-1.amazonaws.com", "--region", "ap-south-1",
                          "--engine", "textract", "--ai-opt-out", value, "--out", str(tmp_path)]) == 0
    config = (tmp_path / "config.js").read_text(encoding="utf-8")
    assert f'"AI_OPT_OUT": {str(opted_out).lower()}' in config


@windows_powershell
@pytest.mark.parametrize("extra,caps", [("", ("200", "10", "300")),
                                        ("-DailyJobCap 50 -IpDailyJobCap 3 -DailyPageCap 120", ("50", "3", "120"))])
def test_every_deploy_sets_the_three_caps_and_prints_them(extra, caps):
    run, calls = run_ps("deploy.ps1", f"-Profile default -Region ap-south-1 -ExpectedAccount {ACCOUNT} "
                        f"-HostingEnabled false {extra}", {"FAKE_STACK": outputs(("ApiUrl", "https://abc.example.com"),
                                                                                  ("BucketName", "b"))})
    assert run.returncode == 0, run.stdout + run.stderr
    (deploy,) = [c for c in calls if c.startswith("sam deploy")]
    overrides = deploy.split("--parameter-overrides ")[1].split(" --")[0].split()
    assert {f"DailyJobCap={caps[0]}", f"IpDailyJobCap={caps[1]}", f"DailyPageCap={caps[2]}"} <= set(overrides)
    assert (f"Caps in effect: {caps[0]} checks a day, {caps[1]} per address a day, {caps[2]} pages a day"
            in run.stdout)
