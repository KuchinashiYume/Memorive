"""While reviewing a refinement, the user adopts an AI suggestion or marks an open question resolved.

The library keeps these choices per version, apart from the draft (whose hash it pins). The session page finds a
version's library product by its draft id, shows the choices and changes them; the readable Markdown lists an
adopted suggestion with the decisions and marks a resolved question, and is rewritten after a change unless the
user has edited it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from memorive.desktop.service.control_store import ControlStore
from memorive.documents.library import LibraryProductController
from memorive.documents.library.product_controller import LibraryProductError
from refinement_answers import OVERVIEW
from memorive.sessions.refinement.brief import make_brief

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "integration"
MESSAGES = [{"role": "user", "content": "按文章列出要点。"},
            {"role": "assistant", "content": "可以先核对第二篇的数据集版本。"},
            {"role": "user", "content": "第二篇用的是哪个版本？"}]


def _library(tmp_path):
    library = LibraryProductController(
        service=None, control_store=ControlStore(tmp_path / "ui"), contract_root=CONTRACTS,
        fixture_root=tmp_path / "unused", state_root=tmp_path / "library", include_synthetic_projection=False,
        core_artifact_provider=lambda: [])
    brief = make_brief({"overview": OVERVIEW, "items": [
        {"kind": "decision", "text": "按文章列出要点。", "message_refs": ["m1"]},
        {"kind": "open_question", "text": "第二篇用的是哪个版本？", "message_refs": ["m3"]},
        {"kind": "ai_suggestion", "text": "核对第二篇的数据集版本。", "message_refs": ["m2"]}]},
        messages=MESSAGES, include_tasks=True, include_risks=True)
    draft = {"schema_version": "DesktopSessionRefinementDraft-v4", "draft_id": "refinement-demo", "task_id": "job-1",
             "display_name": "精炼对话 · 样例", "local_projection_id": "session-1", "source_snapshot_sha256": "A" * 64,
             "created_at": "2026-11-22T00:00:00Z", "brief": brief, "language_context": None}
    artifact_id = library.register_refinement_draft(draft)["artifact_id"]
    return library, artifact_id


def test_choices_are_kept_per_version_and_shown_in_the_markdown(tmp_path):
    library, artifact_id = _library(tmp_path)
    review = library.call("library.refinement_review", {"draft_id": "refinement-demo"})
    assert (review["status"], review["artifact_id"], review["decisions"]) == ("PASS", artifact_id, {})
    assert review["stable_locator"] == f"memorive://artifact/{artifact_id}"
    assert library.call("library.refinement_review", {"draft_id": "other"})["status"] == "NOT_REGISTERED"

    readable = library.resolve_refinement_readable_artifact(artifact_id)       # written before any choice
    decide = lambda item, decision: library.call(   # noqa: E731
        "library.set_refinement_decision", {"artifact_id": artifact_id, "item": item, "decision": decision})
    assert decide(2, "adopted")["decisions"] == {"2": "adopted"}
    assert decide(1, "resolved")["decisions"] == {"1": "resolved", "2": "adopted"}
    assert library.call("library.refinement_review", {"draft_id": "refinement-demo"})["decisions"] == {
        "1": "resolved", "2": "adopted"}

    text = readable.read_text(encoding="utf-8")     # rewritten: the user had not edited it
    decisions_part = text[text.index("## 已定事项"):text.index("## 待确认")]
    assert "核对第二篇的数据集版本。 (#2)" in decisions_part and "由你采纳（原为 AI 建议）" in decisions_part
    assert "## AI 建议 · 未采纳" not in text
    assert "第二篇用的是哪个版本？ (#3)\n  - 已解决" in text
    assert library.resolve_refinement_readable_artifact(artifact_id) == readable

    assert decide(2, None)["decisions"] == {"1": "resolved"}                    # taken back
    assert "## AI 建议 · 未采纳" in readable.read_text(encoding="utf-8")


def test_a_markdown_the_user_edited_is_left_as_it_is(tmp_path):
    library, artifact_id = _library(tmp_path)
    readable = library.resolve_refinement_readable_artifact(artifact_id)
    readable.write_text("my notes", encoding="utf-8")
    library.call("library.set_refinement_decision", {"artifact_id": artifact_id, "item": 2, "decision": "adopted"})
    assert readable.read_text(encoding="utf-8") == "my notes"
    with pytest.raises(LibraryProductError, match="READABLE_HASH_MISMATCH"):
        library.resolve_refinement_readable_artifact(artifact_id)


@pytest.mark.parametrize("item, decision", [
    (0, "adopted"),        # a decision is not adopted
    (1, "adopted"),        # a question is resolved, not adopted
    (2, "resolved"),       # a suggestion is adopted, not resolved
    (3, "adopted"),        # no such item
    (True, "adopted"),
])
def test_a_choice_must_fit_the_item(tmp_path, item, decision):
    library, artifact_id = _library(tmp_path)
    with pytest.raises(LibraryProductError, match="LIBRARY_REFINEMENT_DECISION_INVALID"):
        library.call("library.set_refinement_decision", {"artifact_id": artifact_id, "item": item, "decision": decision})
