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
