"""Run the test suite for CI.

    python scripts/ci_tests.py [pytest arguments]

If the only failure is the redaction server test that is known to fail now and
then, that one test runs once more and decides the result. It is never skipped.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLAKY = "tests/redact/test_redact_server.py::test_rejects_foreign_host_and_origin"


def rerun_flaky(last_failed):
    """True when the tests pytest recorded as failed are exactly the known flaky one."""
    return set(last_failed) == {FLAKY}


def main(argv=None, root=ROOT, run=subprocess.run):
    args = list(sys.argv[1:] if argv is None else argv)
    first = run([sys.executable, "-m", "pytest", "--cache-clear", *args], cwd=root).returncode
    if first == 0:
        return 0
    try:
        last_failed = json.loads((root / ".pytest_cache" / "v" / "cache" / "lastfailed").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return first
    if not rerun_flaky(last_failed):
        return first
    print(f"Only {FLAKY} failed. It is known to fail now and then, so it runs once more.", flush=True)
    return run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", FLAKY], cwd=root).returncode


if __name__ == "__main__":
    sys.exit(main())
