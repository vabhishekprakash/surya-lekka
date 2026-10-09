"""Copy web/ into a folder ready to upload, with config.js written for one deployment.

    python scripts/build_site.py --api <ApiUrl> --region ap-south-1 [--cross-region] [--out .build/web]

Prints the name of each file written, one per line, for scripts/deploy.ps1 to upload.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
API_URL = re.compile(r"https://[a-z0-9.-]+(/[A-Za-z0-9._-]+)*")
# The Regions template.yaml allows; web/app.js names each one in the privacy notice.
REGIONS = ("ap-south-1", "ap-southeast-2")


def config_js(api_base, region, cross_region):
    settings = {"API_BASE": api_base, "REGION": region, "CROSS_REGION": cross_region}
    return f"// Written by scripts/build_site.py for one deployment.\nwindow.SURYA_CONFIG = {json.dumps(settings)};\n"


def build_site(api_base, region, cross_region=False, out=ROOT / ".build" / "web"):
    """Write the site into out and return the file names, config.js included."""
    api_base = api_base.rstrip("/")
    if not API_URL.fullmatch(api_base):
        raise ValueError("--api must be the stack's https ApiUrl.")
    if region not in REGIONS:
        raise ValueError(f"--region must be one of {', '.join(REGIONS)}.")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    names = []
    for source in sorted(p for p in WEB.iterdir() if p.is_file()):
        if source.name != "config.js":
            shutil.copyfile(source, out / source.name)
        names.append(source.name)
    (out / "config.js").write_text(config_js(api_base, region, cross_region), encoding="utf-8")
    return names


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api", required=True, help="the stack's ApiUrl output")
    ap.add_argument("--region", required=True, help="the stack's Region")
    ap.add_argument("--cross-region", action="store_true", help="the stack reads with a cross-Region profile")
    ap.add_argument("--out", default=str(ROOT / ".build" / "web"))
    args = ap.parse_args(argv)
    try:
        names = build_site(args.api, args.region, args.cross_region, args.out)
    except ValueError as e:
        print(f"build_site: {e}", file=sys.stderr)
        return 1
    print("\n".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
