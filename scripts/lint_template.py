"""Lint template.yaml offline: cfn-lint on the SAM template, then the SAM
translator turns it into plain CloudFormation and cfn-lint checks that too
(full Lambda, IAM and API Gateway property checks).

    python scripts/lint_template.py [template.yaml]

Exits non-zero on any cfn-lint error or warning.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class _NoManagedPolicies:
    """The template uses inline policies only, so no AWS lookup is needed."""

    def load(self):
        return {}


def translate(template_path):
    from cfnlint.decode import decode
    from samtranslator.translator.transform import transform

    template, matches = decode(str(template_path))
    if matches:
        raise SystemExit("\n".join(str(m) for m in matches))
    # sam package swaps local code folders for S3 URIs; a placeholder does the same here.
    functions = [template.get("Globals", {}).get("Function", {})]
    functions += [r.get("Properties", {}) for r in template.get("Resources", {}).values()
                  if r.get("Type") == "AWS::Serverless::Function"]
    for props in functions:
        if isinstance(props.get("CodeUri"), str) and not props["CodeUri"].startswith("s3://"):
            props["CodeUri"] = "s3://placeholder-bucket/code.zip"
    return transform(template, {}, _NoManagedPolicies())


def cfn_lint(path):
    exe = Path(sys.executable).with_name("cfn-lint.exe" if sys.platform == "win32" else "cfn-lint")
    return subprocess.run([str(exe), str(path)], capture_output=True, text=True)


def main(argv=None):
    template = Path((argv or sys.argv[1:] or [str(ROOT / "template.yaml")])[0])
    failed = False
    result = cfn_lint(template)
    print(f"cfn-lint {template.name}: {'ok' if result.returncode == 0 else 'problems'}")
    if result.returncode:
        print(result.stdout + result.stderr)
        failed = True
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "translated.json"
        out.write_text(json.dumps(translate(template), indent=1, default=str), encoding="utf-8")
        result = cfn_lint(out)
        print(f"cfn-lint translated CloudFormation: {'ok' if result.returncode == 0 else 'problems'}")
        if result.returncode:
            print(result.stdout + result.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
