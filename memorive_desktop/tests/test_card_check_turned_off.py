"""A card admitted while the card check (workflow node 04) is turned off.

The workflow settings let the user turn node 04 off. Every paper then failed at step 4 with
Core_NODE_DISABLED_OR_MISSING:04_CARD_CROSS_CHECK, because the review still asked for node 04's model.
Now the card is admitted unchecked: active, with every field and key_data entry marked unknown.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from memorive.documents.card_distillation.card import CONTENT_FIELDS, CRED_UNKNOWN
from memorive.documents.card_review.grading import admit_unreviewed
from memorive.documents.card_review.mechanical import _read_frontmatter
from memorive.documents.card_states.card_io import read_status
from memorive.documents.workflow.card_review import review_card_revision
from memorive.documents.workflow.executor import CorePipelineExecutor

CARD = """---
title: Growth at 37 C
review_status: pending
source_anchor:
  paper_id: paper-0001
  page: 3
key_data:
  - name: growth rate
    value: 0.42
---
Body text.
"""


@pytest.fixture(autouse=True)
def logs(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORIVE_KNOWLEDGE_ADMISSION_LOG_DIR", str(tmp_path / "admission_log"))
    monkeypatch.setenv("MEMORIVE_RUNTIME_LOG_LOG_DIR", str(tmp_path / "runtime_log"))


def test_the_card_is_admitted_unchecked(tmp_path):
    folder, stage = tmp_path / "paper", tmp_path / "stage"
    folder.mkdir()
    stage.mkdir()
    (folder / "[Chunks] paper-0001.jsonl").write_text('{"chunk_id": "c1", "text": "Body text."}\n', encoding="utf-8")
    seed = stage / "[Card] paper-0001.md"
    seed.write_text(CARD, encoding="utf-8")

    admitted = review_card_revision(seed, folder, stage, reviewer=admit_unreviewed)

    assert read_status(admitted) == "active"
    receipt = json.loads((stage / "04_05_review_admission.json").read_text(encoding="utf-8"))
    assert receipt["outcome"] == "active"
    front = _read_frontmatter(admitted)
    assert {front["field_credibility"][f]["credibility"] for f in CONTENT_FIELDS} == {CRED_UNKNOWN}
    assert front["key_data"][0]["credibility"] == CRED_UNKNOWN
    assert read_status(folder / "[Card] paper-0001.md") == "active"   # published to the paper folder

def test_step_4_with_the_card_check_turned_off_admits_the_card(tmp_path):
    # The workflow step itself: node 04 off in the settings, the step completes instead of asking for its model.
    folder, stage = tmp_path / "paper", tmp_path / "stage"
    folder.mkdir()
    stage.mkdir()
    (folder / "[Chunks] paper-0001.jsonl").write_text('{"chunk_id": "c1", "text": "Body text."}\n', encoding="utf-8")
    card = stage / "[Card] paper-0001.md"
    card.write_text(CARD, encoding="utf-8")
    marked = []
    run = SimpleNamespace(completed=set(), stage_root=stage, folder=folder, card_path=card,
                          context=SimpleNamespace(source_sha256="S", job_id="job-1"),
                          bridge=SimpleNamespace(node_enabled=lambda node: node != "04_CARD_CROSS_CHECK"),
                          start=lambda node: None, mark=marked.append)
    CorePipelineExecutor._review_and_admit_card(None, run)
    assert read_status(run.card_path) == "active" and marked[-2:] == ["04_CARD_CROSS_CHECK", "05_CARD_ADMISSION"]
