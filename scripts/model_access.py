"""Work out the worker's Bedrock parameters for a ModelId, for scripts/deploy.ps1.

For an inference profile, pipe in the output of aws bedrock get-inference-profile:

    aws bedrock get-inference-profile --inference-profile-identifier <id> --output json |
        python scripts/model_access.py --region ap-south-1 --model-id <id>

For a model ID in the stack's own Region there is nothing to look up:

    python scripts/model_access.py --region ap-south-1 --model-id <id> --in-region

Prints one line of JSON: ProfileModelArns and GlobalModelArns (comma-separated,
or "none") and CrossRegion (true when pages may be read outside the stack's
Region). The destination Regions come only from the profile itself.
"""

import argparse
import json
import re
import sys

MODEL_ARN = re.compile(r"arn:(aws[a-z-]*):bedrock:([a-z0-9-]*):(\d*):foundation-model/([A-Za-z0-9._:-]+)")


def model_access(region, model_id, profile=None):
    """profile is the parsed get-inference-profile reply, or None for a model in this Region."""
    if profile is None:
        return {"ProfileModelArns": "none", "GlobalModelArns": "none", "CrossRegion": False}
    if profile.get("inferenceProfileId") not in (None, model_id):
        raise ValueError(f"The profile returned is not {model_id}.")
    regional, global_ = [], []
    for model in profile.get("models") or []:
        arn = model.get("modelArn", "") if isinstance(model, dict) else ""
        match = MODEL_ARN.fullmatch(arn)
        if not match:
            raise ValueError("The profile lists a model ARN in an unexpected form.")
        partition, model_region, _, name = match.groups()
        if model_region:
            regional.append(arn)
        else:
            global_.append(arn)
            # A global profile also calls the model in the Region it is called from.
            regional.append(f"arn:{partition}:bedrock:{region}::foundation-model/{name}")
    if not regional and not global_:
        raise ValueError("The profile lists no models.")
    regional = list(dict.fromkeys(regional))
    global_ = list(dict.fromkeys(global_))
    elsewhere = bool(global_) or any(MODEL_ARN.fullmatch(a).group(2) != region for a in regional)
    return {"ProfileModelArns": ",".join(regional), "GlobalModelArns": ",".join(global_) or "none",
            "CrossRegion": elsewhere}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--in-region", action="store_true", help="ModelId is a model in --region, not a profile")
    args = ap.parse_args(argv)
    try:
        profile = None if args.in_region else json.loads(sys.stdin.read())
        print(json.dumps(model_access(args.region, args.model_id, profile)))
    except ValueError as e:
        print(f"model_access: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
