"""While reviewing a refinement, the user edits its items: new wording, an AI suggestion adopted, an open question's
conclusion.

The library keeps the edits per version, apart from the draft (whose hash it pins). The session page finds a
version's library product by its draft id, shows the edits and changes them; the readable Markdown lists an adopted
suggestion and a concluded question with the decisions, and is rewritten after a change unless the user has edited
that file.
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
    edit = lambda item, value: library.call(   # noqa: E731
        "library.edit_refinement_item", {"artifact_id": artifact_id, "item": item, "edit": value})
    return library, artifact_id, edit


def test_edits_are_kept_per_version_and_shown_in_the_markdown(tmp_path):
    library, artifact_id, edit = _library(tmp_path)
    review = library.call("library.refinement_review", {"draft_id": "refinement-demo"})
    assert (review["status"], review["artifact_id"], review["edits"]) == ("PASS", artifact_id, {})
    assert review["stable_locator"] == f"memorive://artifact/{artifact_id}"
    assert library.call("library.refinement_review", {"draft_id": "other"})["status"] == "NOT_REGISTERED"

    readable = library.resolve_refinement_readable_artifact(artifact_id)       # written before any edit
    assert edit(2, {"adopted": True})["edits"] == {"2": {"adopted": True}}
    assert edit(1, {"answer": "  v2 数据集 "})["edits"]["1"] == {"answer": "v2 数据集"}
    assert edit(0, {"text": "按文章分别列出要点。"})["edits"]["0"] == {"text": "按文章分别列出要点。"}
    assert library.call("library.refinement_review", {"draft_id": "refinement-demo"})["edits"] == {
        "0": {"text": "按文章分别列出要点。"}, "1": {"answer": "v2 数据集"}, "2": {"adopted": True}}

    text = readable.read_text(encoding="utf-8")     # rewritten: the user had not edited it
    decisions = text[text.index("## 已定事项"):text.index("---")]
    assert "- 按文章分别列出要点。 (#1)\n  - 你改过措辞" in decisions
    assert "- 第二篇用的是哪个版本？ → v2 数据集 (#3)\n  - 由你确认（原为待确认）" in decisions
    assert "- 核对第二篇的数据集版本。 (#2)\n  - 由你采纳（原为 AI 建议）" in decisions
    assert "## 待确认" not in text and "## AI 建议 · 未采纳" not in text
    assert library.resolve_refinement_readable_artifact(artifact_id) == readable

    assert edit(1, None)["edits"] == {"0": {"text": "按文章分别列出要点。"}, "2": {"adopted": True}}   # taken back
    assert "## 待确认" in readable.read_text(encoding="utf-8")


def test_a_markdown_the_user_edited_is_left_as_it_is(tmp_path):
    library, artifact_id, edit = _library(tmp_path)
    readable = library.resolve_refinement_readable_artifact(artifact_id)
    readable.write_text("my notes", encoding="utf-8")
    edit(2, {"adopted": True})
    assert readable.read_text(encoding="utf-8") == "my notes"
    with pytest.raises(LibraryProductError, match="READABLE_HASH_MISMATCH"):
        library.resolve_refinement_readable_artifact(artifact_id)


@pytest.mark.parametrize("item, value", [
    (0, {"adopted": True}),        # only a suggestion is adopted
    (2, {"answer": "yes"}),        # only a question gets a conclusion
    (1, {"adopted": True}),
    (0, {"text": "   "}),          # empty wording
    (0, {"text": "x" * 2_001}),
    (0, {"note": "x"}),            # unknown field
    (0, {}),
    (2, {"adopted": False}),
    (3, {"text": "x"}),            # no such item
    (True, {"text": "x"}),
])
def test_an_edit_must_fit_the_item(tmp_path, item, value):
    library, artifact_id, edit = _library(tmp_path)
    with pytest.raises(LibraryProductError, match="LIBRARY_REFINEMENT_EDIT_INVALID"):
        edit(item, value)
