"""Panel lines across batches.

Assumption: the same (count, wattage, normalised make and model) seen in two
batches is the same panel line repeated on another page, so it counts once and
keeps every piece of evidence. Anything else from different batches is never
combined; each version is kept as a separate candidate and flagged. Within one
batch, lines are kept exactly as the model listed them.
"""

import copy

from conftest import run_confirmed as run_checks  # checks after the household confirms the numbers
from extract.dryrun import load_dry_run_wire
from extract.merge import merge_batches
from test_extract_merge import ANSWERS, on_page, record


def merged(change):
    first, second = load_dry_run_wire(), on_page(load_dry_run_wire(), 3)
    change(first["module_groups"], second["module_groups"])
    return merge_batches([record(1, [1, 2], first), record(2, [3], second)])


def c1(quote):
    return run_checks(quote, ANSWERS)["findings"][0]


def test_identical_lines_count_once_with_all_evidence():
    def change(first, second):
        second[0]["make_model"] = "  example pv   EXM-550 "
    quote = merged(change)
    (group,) = quote["module_groups"]
    assert [(c["page"], c["batch"]) for c in group["count"]["candidates"]] == [(1, 1), (3, 2)]
    assert [(c["page"], c["batch"]) for c in group["wattage"]["candidates"]] == [(1, 1), (3, 2)]
    assert len(group["make_model"]["candidates"]) == 2
    assert quote["flags"]["needs_confirmation"] == []
    assert c1(quote)["status"] == "consistent"


def test_fragments_are_never_combined():
    def change(first, second):
        del first[0]["wattage"]  # batch 1 has only the count ...
        del second[0]["count"]  # ... batch 2 only the wattage
    quote = merged(change)
    groups = quote["module_groups"]
    assert len(groups) == 2
    assert groups[0]["wattage"]["candidates"] == [] and groups[1]["count"]["candidates"] == []
    assert groups[0]["count"]["candidates"][0]["batch"] == 1 and groups[1]["wattage"]["candidates"][0]["batch"] == 2
    assert all(g["conflict"] for g in groups)
    assert quote["flags"]["needs_confirmation"][0]["field"] == "module_groups[option=None]"
    assert c1(quote)["status"] == "needs_confirmation"


def test_different_counts_are_separate_flagged_candidates():
    def change(first, second):
        second[0]["count"] = "8"
    quote = merged(change)
    assert len(quote["module_groups"]) == 2
    assert [g["count"]["candidates"][0]["value"] for g in quote["module_groups"]] == [6, 8]
    assert c1(quote)["status"] == "needs_confirmation"


def test_lines_within_one_batch_kept_as_listed():
    data = load_dry_run_wire()
    data["module_groups"].append(copy.deepcopy(data["module_groups"][0]))
    quote = merge_batches([record(1, [1, 2], data)])
    assert len(quote["module_groups"]) == 2 and not any(g.get("conflict") for g in quote["module_groups"])
    assert [g["group_id"] for g in quote["module_groups"]] == ["G1", "G2"]


def test_repeated_pair_of_lines_matches_one_to_one():
    def change(first, second):
        for groups in (first, second):
            extra = copy.deepcopy(groups[0])
            extra.update(count="2", wattage="545 Wp")
            groups.append(extra)
    quote = merged(change)
    assert len(quote["module_groups"]) == 2 and quote["flags"]["needs_confirmation"] == []
