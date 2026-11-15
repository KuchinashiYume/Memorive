"""The repair a model proposes for flagged card items (evidence_review.repair_flow._validate_repairs).

Each repair must target exactly one flagged item, say why, and either replace it with text
anchored in a literal quote from the same paper, re-anchor it, or drop it with a stated reason.
Quotes that differ from the paper only in whitespace or letter case are taken as the paper's own
span; anything else is refused and the original item stays.
"""
from __future__ import annotations

import copy

import pytest

from evidence_review.repair_flow import RepairContractError, _validate_repairs

CHUNKS = {
    "c1": "Samples were incubated at 37 C for 20 minutes in buffer A.",
    "c2": "Reactor throughput rose from 48 to 84 runs per day.",
    "c3": "Buffer A was later replaced by buffer a with salt.",
}
CARD = {"method": ["Incubation at 37 C for 20 minutes", "Second method"], "key_results": "Throughput rose.",
        "key_data": [{"value": "84", "unit": "runs/day", "metric": "throughput", "sample": "reactor", "stat": "n/a",
                      "quote": "84 runs per day", "chunk_id": "c2"}]}
METHOD = ("by_field.method", 0)


def _anchor(chunk_id="c1", quote="incubated at 37 C", section_page=None):
    return {"chunk_id": chunk_id, "quote": quote, "section_page": section_page}


def _repair(location="by_field.method", item_index=0, **changes):
    value = {"location": location, "item_index": item_index, "action": "replace",
             "replacement": "Incubation at 37 C for 20 minutes in buffer A", "anchors": [_anchor()],
             "disposition": None, "reason": "Buffer was missing."}
    value.update(changes)
    return value


def _validate(*repairs, expected=(METHOD,), protection=None):
    return _validate_repairs({"repairs": list(repairs)}, set(expected), copy.deepcopy(CARD), CHUNKS, protection)


def test_a_replacement_anchored_in_the_paper_is_kept():
    [row] = _validate(_repair(reason="  Buffer was missing.  "))
    assert (row["action"], row["reason"], row["anchors"]) == ("replace", "Buffer was missing.", [_anchor()])


@pytest.mark.parametrize("repair", [
    _repair(action="reanchor", replacement=None, anchors=[_anchor(quote="20 minutes")]),
    _repair(action="drop", replacement=None, anchors=[], disposition="missing_anchor"),
])
def test_reanchoring_or_dropping_with_a_reason_is_kept(repair):
    [row] = _validate(repair)
    assert row["action"] == repair["action"]
    assert row["disposition"] == (None if repair["action"] == "reanchor" else "missing_anchor")


def test_a_quote_differing_only_in_case_or_spacing_takes_the_papers_span():
    [row] = _validate(_repair(anchors=[_anchor(quote="INCUBATED at 37 c")]))
    assert row["anchors"][0]["quote"] == "incubated at 37 C"
    [row] = _validate(_repair(anchors=[_anchor(quote="incubated  at\n37 C")]))
    assert row["anchors"][0]["quote"] == "incubated at 37 C"


def test_a_key_data_replacement_is_marked_for_review():
    replacement = dict(CARD["key_data"][0], value="48", quote="48 to 84")
    [row] = _validate(_repair("key_data[0]", 0, replacement=replacement, anchors=[_anchor("c2", "from 48 to 84")]),
                      expected=[("key_data[0]", 0)])
    assert row["replacement"]["needs_review"] is True and row["replacement"]["quote"] == "48 to 84"


@pytest.mark.parametrize("obj, message", [
    ({}, "must contain a repairs list"),
    ({"repairs": {}}, "must contain a repairs list"),
    ({"repairs": ["drop"]}, "must be an object"),
])
def test_a_reply_without_a_repairs_list_is_refused(obj, message):
    with pytest.raises(RepairContractError, match=message):
        _validate_repairs(obj, {METHOD}, copy.deepcopy(CARD), CHUNKS)


@pytest.mark.parametrize("repairs, message", [
    ([_repair(location="")], "location must be non-empty"),
    ([_repair(item_index=-1)], "non-negative integer"),
    ([_repair(item_index=True)], "non-negative integer"),
    ([_repair(item_index=1)], "unexpected or duplicated"),
    ([_repair(), _repair()], "unexpected or duplicated"),
    ([], "coverage mismatch"),
    ([_repair(action="rewrite")], "action is invalid"),
    ([_repair(reason=" ")], "reason must be non-empty"),
    ([_repair(reason=None)], "reason must be non-empty"),
    ([_repair(anchors=None)], "anchors must be a list"),
    ([_repair(anchors=["c1"])], "must be an object"),
    ([_repair(anchors=[_anchor(chunk_id="c9")])], "not a real same-paper chunk"),
    ([_repair(anchors=[_anchor(quote="")])], "not a literal chunk substring"),
    ([_repair(anchors=[_anchor(quote="incubated at 38 C")])], "not a literal chunk substring"),
    ([_repair(anchors=[_anchor(quote="incubated at 37 C", chunk_id="c2")])], "not a literal chunk substring"),
    ([_repair(anchors=[_anchor(quote="BUFFER A", chunk_id="c3")])], "not a literal chunk substring"),
    ([_repair(anchors=[_anchor(section_page=3)])], "section_page must be string or null"),
    ([_repair(action="drop", replacement="x", anchors=[], disposition="missing_anchor")], "drop requires replacement=null"),
    ([_repair(action="drop", replacement=None, disposition="missing_anchor")], "drop requires replacement=null"),
    ([_repair(action="drop", replacement=None, anchors=[], disposition="disagree")], "supported disposition"),
    ([_repair(action="reanchor")], "reanchor requires replacement=null"),
    ([_repair(action="reanchor", replacement=None, anchors=[])], "reanchor requires replacement=null"),
    ([_repair(anchors=[])], "replace requires non-empty anchors"),
    ([_repair(replacement=" ")], "core-field replacement must be a non-empty string"),
    ([_repair(replacement={"text": "x"})], "core-field replacement must be a non-empty string"),
])
def test_a_repair_that_does_not_fit_its_item_or_the_paper_is_refused(repairs, message):
    with pytest.raises(RepairContractError, match=message):
        _validate(*repairs)


@pytest.mark.parametrize("location, item_index, message", [
    ("by_field.abstract", 0, "unknown core-field location"),
    ("by_field.method", 5, "item_index outside"),
    ("by_field.key_results", 1, "scalar field cannot use item_index"),
    ("key_data[3]", 0, "invalid key_data target"),
    ("key_data[0]", 1, "invalid key_data target"),
    ("notes", 0, "unmapped repair target"),
])
def test_a_repair_aimed_at_something_the_card_does_not_have_is_refused(location, item_index, message):
    repair = _repair(location, item_index, action="reanchor", replacement=None)
    with pytest.raises(RepairContractError, match=message):
        _validate(repair, expected=[(location, item_index)])


@pytest.mark.parametrize("replacement, message", [
    ("48", "must be an object"),
    ({k: v for k, v in CARD["key_data"][0].items() if k != "stat"}, "replacement.stat must be non-empty"),
    (dict(CARD["key_data"][0], unit=" "), "replacement.unit must be non-empty"),
    (dict(CARD["key_data"][0], chunk_id="c9"), "not literal same-paper evidence"),
    (dict(CARD["key_data"][0], quote="85 runs per day"), "not a literal chunk substring"),
])
def test_a_key_data_replacement_without_its_parts_or_evidence_is_refused(replacement, message):
    repair = _repair("key_data[0]", 0, replacement=replacement, anchors=[_anchor("c2", "84 runs per day")])
    with pytest.raises(RepairContractError, match=message):
        _validate(repair, expected=[("key_data[0]", 0)])


@pytest.mark.parametrize("repair", [
    _repair(action="drop", replacement=None, anchors=[], disposition="missing_anchor"),
    _repair(replacement="Incubation in buffer A"),
    _repair(replacement=None),
])
def test_a_repair_cannot_remove_clauses_that_were_not_flagged(repair):
    with pytest.raises(RepairContractError, match="removed or rewrote unaffected clauses"):
        _validate(repair, protection={METHOD: ("20 minutes",)})


def test_a_replacement_keeping_the_unflagged_clauses_is_kept():
    _validate(_repair(), protection={METHOD: ("20 minutes", "37 C")})
