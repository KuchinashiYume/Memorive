"""Language guard on generated artifacts for an English paper (memorive.documents.workflow.executor).

Chinese text may appear in a generated artifact only where it was copied from the paper. Three
places hold application text rather than model prose and are exempt: the built-in retrieval
terms of a context pack, the local ledger path of a judgment, and the two old application warnings
kept in a card's omission record. The exemptions apply to those places only, and the saved
artifact is never rewritten.
"""
from __future__ import annotations

import copy
import json

import pytest
import yaml

from memorive.documents.card_distillation.card import DANGER_UNKNOWN_FIELD, DANGER_UNKNOWN_KD
from memorive.platform.language.source_preservation import SourceLanguageDriftError
from memorive.documents.workflow.executor import (_validate_artifact_language, _validate_context_pack_language,
                                        _validate_judgment_language)
from retrieval.structured_context import _FACET_SPECS

SOURCE = "Samples were incubated at 37 C. 实验条件 appeared in the original table."
EN = dict(source_text=SOURCE, source_language="en")
TERMS = list(_FACET_SPECS["experimental_conditions"]["terms"])


def _pack(**changes):
    value = {"pack": {"query_facets": [{"id": "experimental_conditions", "query_terms": list(TERMS)}],
                      "summary": "Conditions of the incubation."}}
    value["pack"].update(changes)
    return value


def test_the_built_in_retrieval_terms_are_not_counted_as_generated_text():
    pack = _pack()
    before = copy.deepcopy(pack)
    _validate_context_pack_language(pack, **EN)
    assert pack == before


@pytest.mark.parametrize("pack", [
    _pack(query_facets=[{"id": "experimental_conditions", "query_terms": TERMS[:-1]}]),
    _pack(query_facets=[{"id": "my_facet", "query_terms": list(TERMS)}]),
    _pack(summary="温度 was high."),
])
def test_chinese_text_elsewhere_in_a_context_pack_is_refused(pack):
    with pytest.raises(SourceLanguageDriftError, match="SOURCE_LANGUAGE_DRIFT:ContextPack"):
        _validate_context_pack_language(pack, **EN)


def test_text_copied_from_the_paper_is_allowed():
    _validate_context_pack_language(_pack(summary="Table: 实验条件."), **EN)


def _judgment(**changes):
    value = {"status": "pending", "debt": {"path": r"C:\Users\用户\文献库\ledger.json", "reason": "Needs review."}}
    value["debt"].update(changes)
    return value


def test_a_local_ledger_path_in_a_judgment_is_not_model_text():
    judgment = _judgment()
    _validate_judgment_language(judgment, **EN)
    assert judgment["debt"]["path"].startswith(r"C:\Users\用户")


@pytest.mark.parametrize("judgment", [_judgment(reason="需要复核"), dict(_judgment(), status="待定")])
def test_chinese_text_elsewhere_in_a_judgment_is_refused(judgment):
    with pytest.raises(SourceLanguageDriftError, match="SOURCE_LANGUAGE_DRIFT:Judgment.json"):
        _validate_judgment_language(judgment, **EN)


def _card_file(tmp_path, danger, body="Card body."):
    frontmatter = {"title": "Paper", "omissions": [{"field": "key_data", "removed_value": {"value": "84", "danger": danger}}]}
    path = tmp_path / "card.md"
    path.write_text("---\n" + yaml.safe_dump(frontmatter, allow_unicode=True) + "---\n" + body, encoding="utf-8")
    return path


@pytest.mark.parametrize("danger", [DANGER_UNKNOWN_KD, DANGER_UNKNOWN_FIELD])
def test_an_old_application_warning_kept_in_a_card_is_allowed(tmp_path, danger):
    path = _card_file(tmp_path, danger)
    before = path.read_bytes()
    _validate_artifact_language(path, artifact_kind="CORE_CARD", **EN)
    assert path.read_bytes() == before


@pytest.mark.parametrize("danger, body", [
    ("数值可能有误", "Card body."),
    (DANGER_UNKNOWN_KD, "结论：有效。"),
])
def test_other_chinese_text_in_a_card_is_refused(tmp_path, danger, body):
    with pytest.raises(SourceLanguageDriftError, match="SOURCE_LANGUAGE_DRIFT:CORE_CARD"):
        _validate_artifact_language(_card_file(tmp_path, danger, body), artifact_kind="CORE_CARD", **EN)


def test_the_same_warning_in_another_artifact_kind_is_refused(tmp_path):
    with pytest.raises(SourceLanguageDriftError, match="SOURCE_LANGUAGE_DRIFT:CORE_SUMMARY"):
        _validate_artifact_language(_card_file(tmp_path, DANGER_UNKNOWN_KD), artifact_kind="CORE_SUMMARY", **EN)


@pytest.mark.parametrize("kind, content, name", [
    ("CORE_CONTEXT_PACK", _pack(), None),
    ("CORE_CONTEXT_PACK", _pack(summary="温度"), "ContextPack"),
    ("CORE_JUDGMENT_REVIEW", _judgment(), None),
    ("CORE_JUDGMENT_REVIEW", _judgment(reason="需要复核"), "Judgment.json"),
])
def test_saved_packs_and_judgments_use_their_own_rules(tmp_path, kind, content, name):
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    if name is None:
        _validate_artifact_language(path, artifact_kind=kind, **EN)
    else:
        with pytest.raises(SourceLanguageDriftError, match="SOURCE_LANGUAGE_DRIFT:" + name):
            _validate_artifact_language(path, artifact_kind=kind, **EN)


def test_a_chinese_paper_is_not_held_to_the_guard(tmp_path):
    _validate_judgment_language(_judgment(reason="需要复核"), source_text="中文论文", source_language="zh")
    _validate_artifact_language(_card_file(tmp_path, "数值可能有误", "结论：有效。"), artifact_kind="CORE_CARD",
                                source_text="中文论文", source_language="zh")
