"""The auxiliary blocks of a card (memorive.documents.card_distillation.distill._validate_aux).

The model's comparison context, author limitations and outlook, terms and citations are low-risk
extras: bad entries are dropped one by one and the card is still written. Kept entries are
trimmed; a limitation or outlook is kept only if it quotes a real chunk of this paper and the
quote reads like a limitation or an outlook.
"""
from __future__ import annotations

import importlib

import pytest

# The package exports a function named distill; take the module itself.
distill = importlib.import_module("memorive.documents.card_distillation.distill")

REAL = {"c1", "c2"}
NG = distill.key_data_labels("zh")["not_given"]


def _clean(obj, lang="zh"):
    return distill._validate_aux(obj, REAL, "P1", lang)


def test_every_block_is_present_even_when_the_model_returned_none():
    assert _clean({}) == {"comparison_context": [], "author_limitations_outlook": {"limitations": [], "outlook": []},
                          "terms": [], "citations": []}


def test_comparison_context_fills_missing_slots_with_the_cards_placeholder():
    [entry] = _clean({"comparison_context": [{"object": " 污泥 ", "groups": ["A", " ", 3, "B "]}]})["comparison_context"]
    assert entry["object"] == "污泥" and entry["groups"] == ["A", "B"]
    assert all(entry[k] == NG for k in ("condition", "scale", "time", "scenario", "applicability"))
    [english] = _clean({"comparison_context": [{"object": "sludge"}]}, lang="en")["comparison_context"]
    assert english["condition"] == distill.key_data_labels("en")["not_given"]


@pytest.mark.parametrize("entry", [
    "sludge", {"object": " "}, {"object": "not given", "groups": []}, {"groups": "A"}, {"object": "未给", "groups": [" "]},
])
def test_an_empty_or_malformed_comparison_entry_is_dropped(entry):
    assert _clean({"comparison_context": [entry]})["comparison_context"] == []


def _quote(text, chunk_id="c1"):
    return {"quote": text, "chunk_id": chunk_id}


def test_limitations_and_outlook_quoting_this_paper_are_kept():
    result = _clean({"author_limitations_outlook": {
        "limitations": [_quote(" 本研究样本量有限。 ")],
        "outlook": [_quote("Future work should test more sites.", "c2"), _quote("今后可考虑更长周期。")]}})
    alo = result["author_limitations_outlook"]
    assert alo["limitations"] == [{"quote": "本研究样本量有限。", "chunk_id": "c1"}]
    assert [q["chunk_id"] for q in alo["outlook"]] == ["c2", "c1"]


@pytest.mark.parametrize("entry", [
    "本研究样本量有限。",
    dict(_quote("本研究样本量有限。"), page=3),
    {"quote": "本研究样本量有限。"},
    _quote(" "),
    _quote("not given"),
    _quote("本研究样本量有限。", " "),
    _quote("本研究样本量有限。", "c9"),
    _quote("对照组为未处理污泥。"),
])
def test_a_limitation_without_a_real_quote_or_a_limitation_signal_is_dropped(entry):
    alo = _clean({"author_limitations_outlook": {"limitations": [entry]}})["author_limitations_outlook"]
    assert alo["limitations"] == []


def test_an_outlook_needs_an_outlook_signal():
    alo = _clean({"author_limitations_outlook": {"outlook": [_quote("本研究样本量有限。")]}})["author_limitations_outlook"]
    assert alo["outlook"] == []


def test_a_malformed_limitations_block_is_ignored():
    assert _clean({"author_limitations_outlook": ["本研究样本量有限。"]})["author_limitations_outlook"] == {
        "limitations": [], "outlook": []}


@pytest.mark.parametrize("block, good, bad", [
    ("terms", {"term": " SRT ", "definition": " 污泥停留时间 "}, [{"term": "SRT"}, {"term": " ", "definition": "x"},
                                                                {"term": "SRT", "definition": "未给"}, "SRT"]),
    ("citations", {"ref": " [12] ", "relation": " 方法来源 "}, [{"ref": "[12]"}, {"ref": "[12]", "relation": "Not Given"},
                                                             {"ref": 12, "relation": "x"}, "[12]"]),
])
def test_terms_and_citations_keep_complete_entries_only(block, good, bad):
    kept = _clean({block: [good, *bad]})[block]
    assert kept == [{k: v.strip() for k, v in good.items()}]


def test_placeholders_are_recognised_in_any_spacing_or_case():
    for placeholder in ("NOT GIVEN", "未给 / not given", "not given/未给", "記載なし"):
        assert _clean({"terms": [{"term": "x", "definition": placeholder}]})["terms"] == []
