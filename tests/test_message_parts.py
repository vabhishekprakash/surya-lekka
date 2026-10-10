"""Every word a check fills into a message is its own key, so a translated message never mixes in
English. Rebuilding each message from its parts with the English vocabulary gives back exactly
the message the check wrote; what stays as printed is numbers, amounts, units or quote text."""

import copy
import json
import re

import message_corpus as mc
from checks import message_parts as mp
from checks import run_checks

RUNS = [(name, q, i, mc.confirmed_run(q, i)) for name, q, i in mc.cases()] + \
       [(name + "/held", q, i, run_checks(copy.deepcopy(q), copy.deepcopy(i))) for name, q, i in mc.cases()]
NUMERIC = re.compile(r"^(?:₹?[0-9][0-9,]*(?:\.[0-9]+)?(?: ?(?:kWp|kW|kVA|W|Wp))?|)$")


def quote_strings(quote, inputs):
    """Every string value in the quote and the household's inputs (not the field names)."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            found.append(node)
    walk([quote, inputs])
    return "\n".join(found)


def test_every_finding_rebuilds_exactly_from_its_parts():
    for name, _, _, result in RUNS:
        for f in result["findings"]:
            assert mp.render(f["message_key"], f["message_parts"]) == f["message"], (name, f["message_key"])


def test_every_question_and_vendor_line_rebuilds_exactly():
    for name, _, _, result in RUNS:
        for q in result["questions"]:
            assert mp.render(q["key"], q["parts"]) == q["text"], (name, q["key"])
        lines = [mp.render(line["key"], line.get("parts") or {k: mp.text(str(v)) for k, v in line["params"].items()})
                 for line in result["vendor_message_lines"]]
        assert ("\n".join(lines) if lines else None) == result["vendor_message"], name


def test_nothing_english_is_left_as_plain_text():
    """A plain-text part is a number, an amount, a unit, or text quoted from the quote itself."""
    for name, quote, inputs, result in RUNS:
        source = quote_strings(quote, inputs)
        nodes = [n for f in result["findings"] for n in f["message_parts"].values()]
        nodes += [n for q in result["questions"] for n in q["parts"].values()]
        for node in nodes:
            for value in mp.text_nodes(node):
                assert NUMERIC.match(value) or value in source, (name, value)


def test_every_new_key_is_in_the_vocabulary():
    for name, _, _, result in RUNS:
        for f in result["findings"]:
            for node in f["message_parts"].values():
                for key in keys(node):
                    assert key in mp.WORDS, (name, key)


def keys(node):
    if "key" in node:
        yield node["key"]
    for k in ("sep", "last"):
        if k in node:
            yield node[k]
    for child in node.get("join") or []:
        yield from keys(child)
    for child in (node.get("params") or {}).values():
        yield from keys(child)


def test_parsing_never_falls_back_to_plain_text_on_the_corpus():
    for name, _, _, result in RUNS:
        for f in result["findings"]:
            assert mp.message_parts(f["message_params"], f["message_key"]) == f["message_parts"], name  # strict
            for param, value in f["message_params"].items():
                if param in mp.PARSERS or param in mp._KEYS_OF:
                    assert "text" not in f["message_parts"][param] or param in ("dc_kwp", "cutoff"), (name, param)
