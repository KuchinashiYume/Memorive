"""Front matter at the top of RawMD, CleanMD and card files (memorive/documents/front_matter.py).

The front matter ends at the next line that is only "---", so a value such as "title: A---B" stays
whole. Every reader of these files uses the same splitter; each one is read here once with such a
title. Everything is written in a temporary folder.
"""
from __future__ import annotations

import pytest

from memorive.documents.front_matter import split_front_matter

TITLE = "Growth A---B at 37 C"
FRONT = f"paper_id: paper-0001\ntitle: {TITLE}\ndata_ownership: self\ndoc_type: paper\n"
TEXT = f"---\n{FRONT}---\nFirst line of the body.\n---\nA dash line in the body.\n"


@pytest.fixture(autouse=True)
def runtime_log(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORIVE_RUNTIME_LOG_LOG_DIR", str(tmp_path / "runtime_log"))


def write(tmp_path, name, text=TEXT):
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return path


# The splitter

@pytest.mark.parametrize("text", [
    "---\nk: v\n---\nbody",
    "---\nk: v\n---\n",
    "---\r\nk: v\r\n---\r\nbody\r\n",
    "lead\n---\nk: v\n---\nbody",
])
def test_a_file_without_dashes_in_its_values_splits_as_before(text):
    assert split_front_matter(text) == tuple(text.split("---", 2))


def test_a_value_holding_dashes_stays_in_the_front_matter():
    before, front, after = split_front_matter(TEXT)
    assert before == "" and front == "\n" + FRONT
    assert after == "\nFirst line of the body.\n---\nA dash line in the body.\n"


@pytest.mark.parametrize("text", ["---\nk: v\n", "no front matter", "", "---\ntitle: A---B\n"])
def test_without_a_closing_fence_there_is_no_front_matter(text):
    assert split_front_matter(text) is None


def test_a_byte_order_mark_may_open_the_file():
    assert split_front_matter("﻿---\nk: v\n---\nbody") == ("", "\nk: v\n", "\nbody")


def test_a_fence_may_carry_trailing_spaces_but_nothing_else():
    assert split_front_matter("---  \nk: v\n---\t\nbody")[1] == "\nk: v\n"
    assert split_front_matter("---x\nk: v\n---\nbody") is None


# Every reader

def test_the_rawmd_reader_keeps_the_whole_title(tmp_path):
    from memorive.documents.processing.pipeline import _read_frontmatter
    assert _read_frontmatter(write(tmp_path, "raw.md"))["title"] == TITLE


def test_the_cleaning_step_keeps_the_whole_title_and_body():
    from memorive.documents.processing.document_clean_clean import _split_frontmatter
    front, body = _split_frontmatter(TEXT)
    assert front["title"] == TITLE
    assert body == "First line of the body.\n---\nA dash line in the body.\n"


def test_the_distill_reader_keeps_the_whole_title(tmp_path):
    from memorive.documents.card_distillation.distill import _read_frontmatter
    assert _read_frontmatter(write(tmp_path, "clean.md"))["title"] == TITLE


def test_the_segmented_distill_reader_keeps_the_whole_title(tmp_path):
    from memorive.documents.workflow.segmented_distill import _frontmatter
    assert _frontmatter(write(tmp_path, "card.md"))["title"] == TITLE


def test_the_card_readers_keep_the_whole_title(tmp_path):
    from memorive.documents.registry.adapters import read_frontmatter
    from memorive.documents.card_review.mechanical import _read_frontmatter as mechanical
    from retrieval.candidate_retrieval import _read_frontmatter as retrieval
    card = write(tmp_path, "card.md")
    assert read_frontmatter(card)["title"] == TITLE
    assert mechanical(card)["title"] == TITLE
    assert retrieval(card)["title"] == TITLE


def test_a_reader_still_refuses_a_file_without_front_matter(tmp_path):
    from memorive.documents.processing.errors import DocumentProcessingError
    from memorive.documents.processing.pipeline import _read_frontmatter
    with pytest.raises(DocumentProcessingError):
        _read_frontmatter(write(tmp_path, "raw.md", "no front matter here\n"))
