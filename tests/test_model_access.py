import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "model_access.py"
model_access = runpy.run_path(str(SCRIPT))["model_access"]


def profile(model_id, *arns):
    return {"inferenceProfileId": model_id, "status": "ACTIVE", "type": "SYSTEM_DEFINED",
            "models": [{"modelArn": a} for a in arns]}


def test_regional_profile_uses_the_regions_it_lists():
    arns = [f"arn:aws:bedrock:{r}::foundation-model/amazon.nova-pro-v1:0" for r in ("ap-south-1", "ap-southeast-1")]
    assert model_access("ap-south-1", "apac.amazon.nova-pro-v1:0", profile("apac.amazon.nova-pro-v1:0", *arns)) == {
        "ProfileModelArns": ",".join(arns), "GlobalModelArns": "none", "CrossRegion": True}


def test_global_profile_adds_the_model_in_the_calling_region():
    got = model_access("ap-southeast-2", "global.amazon.nova-2-lite-v1:0", profile(
        "global.amazon.nova-2-lite-v1:0", "arn:aws:bedrock:::foundation-model/amazon.nova-2-lite-v1:0"))
    assert got == {"ProfileModelArns": "arn:aws:bedrock:ap-southeast-2::foundation-model/amazon.nova-2-lite-v1:0",
                   "GlobalModelArns": "arn:aws:bedrock:::foundation-model/amazon.nova-2-lite-v1:0",
                   "CrossRegion": True}


def test_profile_that_stays_in_the_region_is_not_cross_region():
    arn = "arn:aws:bedrock:ap-south-1::foundation-model/amazon.nova-pro-v1:0"
    got = model_access("ap-south-1", "x.amazon.nova-pro-v1:0", profile("x.amazon.nova-pro-v1:0", arn, arn))
    assert got == {"ProfileModelArns": arn, "GlobalModelArns": "none", "CrossRegion": False}


def test_model_in_the_region_needs_no_lookup():
    assert model_access("ap-south-1", "amazon.nova-pro-v1:0") == {
        "ProfileModelArns": "none", "GlobalModelArns": "none", "CrossRegion": False}


@pytest.mark.parametrize("reply", [
    profile("other.amazon.nova-pro-v1:0", "arn:aws:bedrock:ap-south-1::foundation-model/amazon.nova-pro-v1:0"),
    profile("apac.amazon.nova-pro-v1:0"),
    profile("apac.amazon.nova-pro-v1:0", "arn:aws:bedrock:ap-south-1::foundation-model/*"),
    profile("apac.amazon.nova-pro-v1:0", "arn:aws:bedrock:ap-south-1:123456789012:inference-profile/x"),
])
def test_unexpected_profiles_are_refused(reply):
    with pytest.raises(ValueError):
        model_access("ap-south-1", "apac.amazon.nova-pro-v1:0", reply)


def test_command_line():
    reply = profile("apac.amazon.nova-pro-v1:0", "arn:aws:bedrock:ap-southeast-1::foundation-model/amazon.nova-pro-v1:0")
    run = subprocess.run([sys.executable, str(SCRIPT), "--region", "ap-south-1", "--model-id",
                          "apac.amazon.nova-pro-v1:0"], input=json.dumps(reply), capture_output=True, text=True)
    assert run.returncode == 0 and json.loads(run.stdout)["CrossRegion"] is True
    run = subprocess.run([sys.executable, str(SCRIPT), "--region", "ap-south-1", "--model-id", "x", "--in-region"],
                         capture_output=True, text=True)
    assert json.loads(run.stdout)["ProfileModelArns"] == "none"
    run = subprocess.run([sys.executable, str(SCRIPT), "--region", "ap-south-1", "--model-id", "x"],
                         input="{}", capture_output=True, text=True)
    assert run.returncode == 1 and "no models" in run.stderr
