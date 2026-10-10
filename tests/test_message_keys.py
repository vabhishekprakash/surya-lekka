"""Message keys add a stable key and parameters to every finding, question and vendor-message
line. They change no status and no word: tests/fixtures/messages_golden.json holds the statuses
and English text of a fixed synthetic corpus saved before keys were added."""

import json
from pathlib import Path

import pytest

import message_corpus as mc

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "messages_golden.json").read_text(encoding="utf-8"))
NOW = mc.run_all()


def test_the_corpus_is_the_one_the_golden_file_was_saved_from():
    assert sorted(NOW) == sorted(GOLDEN)


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_statuses_and_english_text_are_unchanged(name):
    assert json.loads(json.dumps(NOW[name], default=str)) == GOLDEN[name]


from checks import messages, run_checks  # noqa: E402


def full_runs():
    import copy
    return [(name, run_checks(copy.deepcopy(q), copy.deepcopy(i))) for name, q, i in mc.cases()]


RUNS = full_runs()


def test_every_finding_has_a_key_whose_template_gives_back_its_exact_message():
    seen = set()
    for name, result in RUNS:
        for f in result["findings"]:
            assert f["message_key"] != "unkeyed", (name, f["check_id"])
            assert messages.render(f["message_key"], f["message_params"]) == f["message"], name
            seen.add(f["message_key"])
    assert len(seen) >= 50


def test_every_question_and_vendor_line_has_a_key_and_renders_exactly():
    from checks.questions import TEMPLATES
    for name, result in RUNS:
        for q in result["questions"]:
            assert q["key"] == f"question.{q['id']}"
            assert TEMPLATES[q["id"]].format(**q["params"]) == q["text"]
        lines = [messages.render(line["key"], line["params"]) for line in result["vendor_message_lines"]]
        assert ("\n".join(lines) if lines else None) == result["vendor_message"], name


def test_one_message_never_matches_two_templates():
    import re
    for key, (checks, template) in messages.TEMPLATES.items():
        sample = re.sub(r"\{(\w+)\}", lambda m: {"stated": "₹1,000", "difference": "₹10", "rule": "₹2,000",
                                                 "amount_low": "₹1", "amount_high": "₹2", "direction": "more",
                                                 "what": "total", "hint": "Please check the amounts with the vendor."}
                        .get(m.group(1), "1"), template)
        for check_id in checks or ("C1_capacity", "C2_central_subsidy", "C3_gross_total", "C4_missing_details"):
            assert messages.identify(check_id, sample)[0] == key


def test_keys_are_stable_names():
    import re
    assert all(re.fullmatch(r"[A-Za-z0-9_]+(\.[a-z0-9_]+)+", k) for k in messages.TEMPLATES)
