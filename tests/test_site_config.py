import json

import pytest

from api.site_config import config_js, textract_opted_out


def reply(services):
    return {"EffectivePolicy": {"PolicyType": "AISERVICES_OPT_OUT_POLICY",
                                "PolicyContent": json.dumps({"services": services})}}


@pytest.mark.parametrize("services,opted_out", [
    ({"textract": {"opt_out_policy": "optOut"}}, True),
    ({"default": {"opt_out_policy": "optOut"}}, True),
    ({"default": {"opt_out_policy": {"@@assign": "optOut"}}}, True),
    ({"default": {"opt_out_policy": "optOut"}, "textract": {"opt_out_policy": "optIn"}}, False),
    ({"default": {"opt_out_policy": "optIn"}}, False),
    ({"rekognition": {"opt_out_policy": "optOut"}}, False),
    ({}, False),
])
def test_textract_opt_out_is_confirmed_only_by_the_effective_policy(services, opted_out):
    assert textract_opted_out(reply(services)) is opted_out


@pytest.mark.parametrize("bad", [None, {}, {"EffectivePolicy": {}}, {"EffectivePolicy": {"PolicyContent": "not json"}},
                                 {"EffectivePolicy": {"PolicyContent": "[]"}}, "text"])
def test_an_unreadable_policy_is_not_a_confirmation(bad):
    assert textract_opted_out(bad) is False


def test_config_names_the_engine_and_the_opt_out():
    text = config_js("https://abc.example", "ap-south-1", False, "textract", True)
    assert text.endswith('window.SURYA_CONFIG = {"API_BASE": "https://abc.example", "REGION": "ap-south-1", '
                         '"CROSS_REGION": false, "ENGINE": "textract", "AI_OPT_OUT": true};\n')
    assert '"ENGINE": "none", "AI_OPT_OUT": false' in config_js("https://abc.example", "ap-south-1", False)
