"""config.js for one deployment of the web app, shared by scripts/build_site.py and the
local server's --api mode, and the AI services opt-out check behind its privacy notice."""

import json


def config_js(api_base, region, cross_region, engine="none", ai_opt_out=False):
    settings = {"API_BASE": api_base, "REGION": region, "CROSS_REGION": cross_region, "ENGINE": engine,
                "AI_OPT_OUT": ai_opt_out}
    return f"// Written by scripts/build_site.py for one deployment.\nwindow.SURYA_CONFIG = {json.dumps(settings)};\n"


def _setting(services, name):
    value = (services.get(name) or {}).get("opt_out_policy") if isinstance(services.get(name), dict) else None
    return value.get("@@assign") if isinstance(value, dict) else value


def textract_opted_out(reply):
    """True only when the account's effective AI services opt-out policy (the reply of
    aws organizations describe-effective-policy --policy-type AISERVICES_OPT_OUT_POLICY)
    opts Textract out, by name or through default. Anything else, including a reply
    that can't be read, is not a confirmation."""
    try:
        services = json.loads(reply["EffectivePolicy"]["PolicyContent"])["services"]
    except (KeyError, TypeError, ValueError):
        return False
    if not isinstance(services, dict):
        return False
    textract = _setting(services, "textract")
    if textract is not None:
        return textract == "optOut"
    return _setting(services, "default") == "optOut"
