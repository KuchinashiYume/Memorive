"""Crash recovery in segmented distillation (memorive_workflow.segmented_distill._validate_replay_card).

When a run restarts and finds a card already written for this paper, the card is reused only if
it belongs to this paper, title and ownership, was written by the expected model under the
current card schema, and every content field is filled and anchored. Anything else stops the run
instead of silently reusing someone else's card.
"""
from __future__ import annotations

import pytest
import yaml

from evidence_extraction.card import CONTENT_FIELDS, LIST_FIELDS, build_card, write_card
from evidence_extraction.config import CARD_SCHEMA_VERSION
from memorive_workflow.segmented_distill import _validate_replay_card

SOURCE = {"paper_id": "Doe2024", "title": "Rates of incubation", "data_ownership": "user_owned"}
MODEL = "deepseek-v4-pro"


def _fields():
    return {f: (["Value for " + f] if f in LIST_FIELDS else "Value for " + f) for f in CONTENT_FIELDS}


def _card(tmp_path, *, edit=None, **overrides):
    values = dict(paper_id=SOURCE["paper_id"], title=SOURCE["title"], data_ownership=SOURCE["data_ownership"],
                  fields=_fields(), by_field={f: [{"chunk_id": "c1", "quote": None, "section_page": None}]
                                              for f in CONTENT_FIELDS},
                  distill_model=MODEL, distilled_at="2026-11-15 10:00", schema_version=CARD_SCHEMA_VERSION)
    values.update(overrides)
    fm = build_card(**values)
    if edit:
        edit(fm)
    path = tmp_path / "[Card][Doe2024] Rates.md"
    write_card(path, fm)
    return path


def test_the_cards_own_run_is_reused(tmp_path):
    card, self_check = _validate_replay_card(_card(tmp_path), source_frontmatter=SOURCE, expected_model=MODEL)
    assert card["distill_model"] == MODEL and card["source_anchor"]["paper_id"] == "Doe2024"
    assert isinstance(self_check, dict)


@pytest.mark.parametrize("change", [
    dict(paper_id="Roe2023"),
    dict(title="Another title"),
    dict(data_ownership="third_party"),
    dict(distill_model="other-model"),
    dict(schema_version=CARD_SCHEMA_VERSION - 1),
])
def test_a_card_of_another_paper_model_or_schema_is_not_reused(tmp_path, change):
    with pytest.raises(RuntimeError, match="REPLAY_IDENTITY_CONFLICT"):
        _validate_replay_card(_card(tmp_path, **change), source_frontmatter=SOURCE, expected_model=MODEL)


@pytest.mark.parametrize("edit", [
    lambda fm: fm.pop("source_anchor"),
    lambda fm: fm.update(source_anchor="Doe2024"),
    lambda fm: fm["source_anchor"].pop("by_field"),
])
def test_a_card_without_its_anchor_block_is_not_reused(tmp_path, edit):
    with pytest.raises(RuntimeError, match="REPLAY_IDENTITY_CONFLICT"):
        _validate_replay_card(_card(tmp_path, edit=edit), source_frontmatter=SOURCE, expected_model=MODEL)


LIST_FIELD = sorted(LIST_FIELDS)[0]
TEXT_FIELD = next(f for f in CONTENT_FIELDS if f not in LIST_FIELDS)


@pytest.mark.parametrize("edit, field", [
    (lambda fm: fm.update({LIST_FIELD: []}), LIST_FIELD),
    (lambda fm: fm.update({LIST_FIELD: ["ok", " "]}), LIST_FIELD),
    (lambda fm: fm.update({LIST_FIELD: "not a list"}), LIST_FIELD),
    (lambda fm: fm.update({TEXT_FIELD: " "}), TEXT_FIELD),
    (lambda fm: fm.update({TEXT_FIELD: ["a list"]}), TEXT_FIELD),
    (lambda fm: fm["source_anchor"]["by_field"].update({TEXT_FIELD: []}), TEXT_FIELD),
    (lambda fm: fm["source_anchor"]["by_field"].pop(LIST_FIELD), LIST_FIELD),
])
def test_a_card_with_an_empty_or_unanchored_field_is_not_reused(tmp_path, edit, field):
    with pytest.raises(RuntimeError, match="REPLAY_FIELD_INVALID:" + field):
        _validate_replay_card(_card(tmp_path, edit=edit), source_frontmatter=SOURCE, expected_model=MODEL)


def test_a_file_without_frontmatter_is_not_reused(tmp_path):
    path = tmp_path / "card.md"
    path.write_text("no frontmatter here", encoding="utf-8")
    with pytest.raises(RuntimeError, match="CARD_FRONTMATTER_INVALID"):
        _validate_replay_card(path, source_frontmatter=SOURCE, expected_model=MODEL)
    path.write_text("---\n- a list\n---\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="CARD_FRONTMATTER_INVALID"):
        _validate_replay_card(path, source_frontmatter=SOURCE, expected_model=MODEL)


def test_the_check_reads_the_file_as_written(tmp_path):
    path = _card(tmp_path)
    text = path.read_text(encoding="utf-8")
    front = yaml.safe_load(text.split("---", 2)[1])
    front["distill_model"] = "other-model"
    path.write_text("---\n" + yaml.safe_dump(front, allow_unicode=True) + "---\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="REPLAY_IDENTITY_CONFLICT"):
        _validate_replay_card(path, source_frontmatter=SOURCE, expected_model=MODEL)
