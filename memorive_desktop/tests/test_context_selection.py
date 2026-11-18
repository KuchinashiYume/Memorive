"""Which excerpts of a research answer's sources fit its context (research/workspace/context_selection.py).

Two made-up papers: A reports a yield increase on page 2 (and another on the same page), a result
that was not significant on page 3 and a later remark on page 5; B reports a recovery improvement.
The budget is a number of excerpts or of characters, as the caller's fits() decides.
"""
from __future__ import annotations

from memorive.research.workspace.context_selection import select

QUESTION = "How much did the yield increase?"


def ref(identifier, document, page, line, text):
    return {"id": identifier, "artifact_id": document, "document_id": document, "page": page, "line_start": line,
            "text": text, "title": document}


A1 = ref("a1", "paper-a", 2, 1, "Results\nThe yield increased by 30% compared with controls.")
A3 = ref("a3", "paper-a", 2, 20, "Production increased by 12% in the second run.")
A2 = ref("a2", "paper-a", 3, 1, "The difference in yield was not statistically significant in the second reactor.")
A4 = ref("a4", "paper-a", 5, 1, "Production increased slightly in later runs.")
B1 = ref("b1", "paper-b", 4, 1, "Results\nRecovery improved by 18% relative to the untreated batch.")


def at_most(count):
    return lambda chosen: len(chosen) <= count


def ids(chosen):
    return [r["id"] for r in chosen]


def test_each_source_gets_one_excerpt_before_any_source_gets_a_second():
    assert sorted(ids(select([A1, A3, B1], QUESTION, at_most(2)))) == ["a1", "b1"]


def test_an_excerpt_too_long_for_the_budget_does_not_keep_its_source_out():
    long_a1 = dict(A1, text=A1["text"] + " " + "x" * 300)
    chosen = select([long_a1, A3, B1], QUESTION, lambda rows: sum(len(r["text"]) for r in rows) <= 160)
    assert sorted(ids(chosen)) == ["a3", "b1"]


def test_a_qualification_of_a_source_is_added_when_there_is_room():
    assert sorted(ids(select([A1, A2, B1], QUESTION, at_most(3)))) == ["a1", "a2", "b1"]


def test_without_room_a_qualification_replaces_its_own_source_head_while_another_source_keeps_a_finding():
    assert sorted(ids(select([A1, A2, B1], QUESTION, at_most(2)))) == ["a2", "b1"]


def test_a_methods_question_keeps_its_methods_passage_instead_of_a_qualification():
    methods = ref("a5", "paper-a", 1, 1, "Methods\nSamples were taken from batch reactors at 37 C.")
    assert sorted(ids(select([methods, A1, A2, B1], "What sampling method was used?", at_most(2)))) == ["a5", "b1"]


def test_a_single_source_never_trades_its_only_finding():
    assert ids(select([A1, A2], QUESTION, at_most(1))) == ["a1"]


def test_the_rest_comes_from_pages_not_yet_used_first():
    assert ids(select([A1, A3, A4], QUESTION, at_most(2))) == ["a1", "a4"]
    assert ids(select([A1, A3, A4], QUESTION, at_most(3))) == ["a1", "a4", "a3"]
