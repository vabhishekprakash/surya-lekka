import json
import os
import re
import runpy
import shutil
import subprocess
import sys
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
        default { return "" }
    }
}
function global:sam { $global:calls += ,("sam " + ($args -join " ")); $global:LASTEXITCODE = 0 }
"""


def run_ps(script, params, env, timeout=180):
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
    config = (ROOT / ".build" / "web" / "config.js").read_text(encoding="utf-8")
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
             r"aws s3 cp .build\samples", r"scripts\build_site.py @siteArgs", "create-invalidation",
             'Write-Host "API URL']
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
    assert 'window.SURYA_CONFIG = {"API_BASE": "' + api + '", "REGION": "ap-south-1", "CROSS_REGION": false};' in config
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
