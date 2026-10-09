import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ci = runpy.run_path(str(ROOT / "scripts" / "ci_tests.py"))
WORKFLOW = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")


class Runner:
    """Stands in for subprocess.run: returns the given exit codes and writes pytest's lastfailed."""

    def __init__(self, root, codes, failed):
        self.root, self.codes, self.failed, self.commands = root, list(codes), failed, []

    def __call__(self, command, cwd):
        self.commands.append(command[3:])
        cache = self.root / ".pytest_cache" / "v" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        if len(self.commands) == 1:
            (cache / "lastfailed").write_text(json.dumps({k: True for k in self.failed}), encoding="utf-8")
        return type("Done", (), {"returncode": self.codes.pop(0)})


def test_flaky_test_alone_runs_once_more(tmp_path):
    for second, result in ((0, 0), (1, 1)):
        run = Runner(tmp_path, [1, second], [ci["FLAKY"]])
        assert ci["main"]([], tmp_path, run) == result
        assert run.commands == [["--cache-clear"], ["-p", "no:cacheprovider", ci["FLAKY"]]]


def test_other_failures_fail_without_a_rerun(tmp_path):
    run = Runner(tmp_path, [1], [ci["FLAKY"], "tests/test_api.py::test_kill_switch"])
    assert ci["main"]([], tmp_path, run) == 1 and len(run.commands) == 1
    run = Runner(tmp_path, [0], [])
    assert ci["main"](["-q"], tmp_path, run) == 0 and run.commands == [["--cache-clear", "-q"]]


def test_flaky_test_still_exists():
    path, name = ci["FLAKY"].split("::")
    assert f"def {name}(" in (ROOT / path).read_text(encoding="utf-8")


def test_workflow_runs_the_tests_and_the_lint_on_push():
    assert "on: push" in WORKFLOW and "runs-on: ubuntu-latest" in WORKFLOW
    assert 'python-version: "3.12"' in WORKFLOW
    assert "python scripts/ci_tests.py" in WORKFLOW and "python scripts/lint_template.py template.yaml" in WORKFLOW
    assert "requirements-redact.txt" in WORKFLOW  # so the flaky test runs rather than being skipped
