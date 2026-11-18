"""Every save of a research answer is kept as a version (memorive/research/workspace/answer_saves.py).

The answers come from the stand-in model of examples/research_qa.py, so the whole path runs and nothing
leaves the computer: a question is answered, then the answer is edited, saved by the editor, taken over
from an Agent's reply, revised claim by claim, restored, compared and read back after a restart. Every
text below is made up.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

EXAMPLE_FILE = Path(__file__).resolve().parents[1] / "examples" / "research_qa.py"
_spec = importlib.util.spec_from_file_location("research_qa_example_for_saves", EXAMPLE_FILE)
example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(example)

from memorive.research.workspace.service import ResearchWorkspace  # noqa: E402

AGENT = {"profile_ref": "cli:codex", "eligible": True, "profile_kind": "CLI", "adapter_id": "codex_cli",
         "binding_hash": "agent", "model_name": "agent-model"}
EDITED = "第一段：生长速度在 25 °C 和 37 °C 之间翻倍。\n\n  第二段保留两个前导空格和行尾空格  \n\tTab 开头的一行\n"


class Agent:
    """Stands in for a coding agent: cites the first excerpt it was shown."""

    def __call__(self, *, profile_ref, prompt, job_id, response_schema):
        first = example.EXCERPT_ID.search(prompt).group(0)
        return {"response": {"answer": "An Agent's reading: growth doubles between 25 C and 37 C.", "citations": [first],
                             "needs_agent": False, "opportunities": []},
                "model_name": AGENT["model_name"], "execution_receipt": {"status": "PASS"}}


def _open(root):
    workspace = ResearchWorkspace(root, catalog=lambda: [dict(example.PROFILE), dict(AGENT)], model=example.ExampleModel())
    workspace.agent_model = Agent()
    return workspace


def _finish(workspace):
    for future in list(workspace.futures.values()):
        future.result(timeout=120)


@pytest.fixture
def answered(tmp_path):
    """A conversation with one answer from the model; returns (workspace, thread id, answer id)."""
    library = tmp_path / "library"
    library.mkdir()
    paper = library / "k12.md"
    paper.write_text(example.PAPER, encoding="utf-8")
    workspace = _open(tmp_path / "workspace")
    thread = workspace.thread_create()
    material = workspace.index.add_library_file(
        thread["project"], path=paper, root=library, external_id="k12",
        expected_hash=hashlib.sha256(paper.read_bytes()).hexdigest(), title="K12", material_layer="cleaned")
    workspace.attachments.configure(thread["id"], thread["revision"],
                                    {"artifact_ids": [material["id"]], "profile_ref": example.PROFILE["profile_ref"]})
    workspace.ask(thread["id"], example.QUESTION, "request-0001", artifact_ids=[material["id"]])
    _finish(workspace)
    [answer] = [m for m in workspace.thread_get(thread["id"])["messages"] if m["role"] == "assistant"]
    yield workspace, thread["id"], answer["id"]
    workspace.close()


def call(workspace, method, **params):
    return workspace.call(method, params, desktop=True)


def current(workspace, thread_id, answer_id):
    return call(workspace, "memo.answer_history", thread_id=thread_id, answer_id=answer_id)["current_version_id"]


def save(workspace, thread_id, answer_id, text, source="EDIT"):
    return call(workspace, "memo.answer_save", thread_id=thread_id, answer_id=answer_id, text=text,
                expected_version_id=current(workspace, thread_id, answer_id), source=source)


def test_an_answer_never_saved_has_its_generated_text_as_the_only_version(answered):
    workspace, thread_id, answer_id = answered
    history = call(workspace, "memo.answer_history", thread_id=thread_id, answer_id=answer_id)
    [version] = history["versions"]
    assert (version["number"], version["source"], version["parent_version_id"]) == (1, "GENERATED", None)
    generated = call(workspace, "memo.answer_version_get", thread_id=thread_id, answer_id=answer_id,
                     version_id=version["version_id"])
    message = next(m for m in workspace.thread_get(thread_id)["messages"] if m["id"] == answer_id)
    assert (generated["text"], generated["citations"]) == (message["text"], message["citations"])


def test_the_first_save_keeps_the_generated_answer_and_the_edit_exactly(answered):
    workspace, thread_id, answer_id = answered
    before = next(m for m in workspace.thread_get(thread_id)["messages"] if m["id"] == answer_id)
    result = save(workspace, thread_id, answer_id, EDITED)
    assert result["saved"] is True
    first, second = result["history"]["versions"]
    assert (first["source"], second["source"], second["parent_version_id"]) == ("GENERATED", "EDIT", first["version_id"])
    edited = call(workspace, "memo.answer_version_get", thread_id=thread_id, answer_id=answer_id,
                  version_id=second["version_id"])
    assert edited["text"] == EDITED                       # Chinese, blank line, spaces and tab as typed
    assert edited["citations"] == before["citations"]     # an edit keeps the citations
    message = next(m for m in workspace.thread_get(thread_id)["messages"] if m["id"] == answer_id)
    assert message["text"] == EDITED and message["saved_version_id"] == second["version_id"]
    assert message["answer_version"] == before.get("answer_version", 1) + 1
    original = call(workspace, "memo.answer_version_get", thread_id=thread_id, answer_id=answer_id,
                    version_id=first["version_id"])
    assert original["text"] == before["text"]


def test_saving_the_same_content_again_makes_no_version(answered):
    workspace, thread_id, answer_id = answered
    save(workspace, thread_id, answer_id, EDITED)
    again = save(workspace, thread_id, answer_id, EDITED, source="AUTOSAVE")
    assert again["saved"] is False and len(again["history"]["versions"]) == 2
    autosaved = save(workspace, thread_id, answer_id, EDITED + "补一句。\n", source="AUTOSAVE")
    assert autosaved["version"]["source"] == "AUTOSAVE" and autosaved["version"]["number"] == 3


@pytest.mark.parametrize("text", ["", "  \n\t "])
def test_an_empty_answer_is_not_saved(answered, text):
    workspace, thread_id, answer_id = answered
    with pytest.raises(ValueError, match="ANSWER_TEXT_INVALID"):
        save(workspace, thread_id, answer_id, text)


def test_a_save_made_from_an_older_version_is_refused(answered):
    workspace, thread_id, answer_id = answered
    seen = current(workspace, thread_id, answer_id)
    save(workspace, thread_id, answer_id, EDITED)        # another window saved meanwhile
    with pytest.raises(ValueError, match="ANSWER_VERSION_CONFLICT") as refused:
        call(workspace, "memo.answer_save", thread_id=thread_id, answer_id=answer_id, text="mine",
             expected_version_id=seen)
    # The error names the version saved meanwhile, for the window to compare with.
    assert str(refused.value) == "ANSWER_VERSION_CONFLICT:" + current(workspace, thread_id, answer_id)
    assert len(call(workspace, "memo.answer_history", thread_id=thread_id, answer_id=answer_id)["versions"]) == 2


def test_restoring_saves_the_old_content_as_a_new_version_and_keeps_the_later_ones(answered):
    workspace, thread_id, answer_id = answered
    second = save(workspace, thread_id, answer_id, EDITED)["version"]
    third = save(workspace, thread_id, answer_id, "第三版。\n")["version"]
    restored = call(workspace, "memo.answer_restore", thread_id=thread_id, answer_id=answer_id,
                    version_id=second["version_id"], expected_version_id=third["version_id"])["version"]
    assert (restored["number"], restored["source"], restored["restored_from"], restored["parent_version_id"]) == (
        4, "RESTORE", second["version_id"], third["version_id"])
    assert restored["content_sha256"] == second["content_sha256"]
    still = call(workspace, "memo.answer_version_get", thread_id=thread_id, answer_id=answer_id,
                 version_id=third["version_id"])
    assert still["text"] == "第三版。\n"
    message = next(m for m in workspace.thread_get(thread_id)["messages"] if m["id"] == answer_id)
    assert message["text"] == EDITED


def test_the_difference_of_two_versions_is_exact_by_line_and_by_word(answered):
    workspace, thread_id, answer_id = answered
    old = save(workspace, thread_id, answer_id, "第一行\n结果是 25% higher growth。\n末行\n")["version"]
    new = save(workspace, thread_id, answer_id, "第一行\n结果是 30% higher growth。\n末行\n新增一行\n")["version"]
    diff = call(workspace, "memo.answer_diff", thread_id=thread_id, answer_id=answer_id,
                from_version_id=old["version_id"], to_version_id=new["version_id"])
    kinds = [(b["kind"], b["old_lines"], b["new_lines"]) for b in diff["blocks"]]
    assert kinds == [("equal", ["第一行\n"], ["第一行\n"]),
                     ("change", ["结果是 25% higher growth。\n"], ["结果是 30% higher growth。\n"]),
                     ("equal", ["末行\n"], ["末行\n"]),
                     ("insert", [], ["新增一行\n"])]
    words = diff["blocks"][1]["words"]
    assert [w for w in words if w["op"] != "equal"] == [{"op": "delete", "text": "25"}, {"op": "insert", "text": "30"}]
    assert "".join(w["text"] for w in words if w["op"] != "insert") == "结果是 25% higher growth。\n"
    assert "".join(w["text"] for w in words if w["op"] != "delete") == "结果是 30% higher growth。\n"
    assert diff["citations"] == {"added": [], "removed": []}


def test_chinese_text_is_compared_character_by_character(answered):
    workspace, thread_id, answer_id = answered
    old = save(workspace, thread_id, answer_id, "温度升高使生长加快。\n")["version"]
    new = save(workspace, thread_id, answer_id, "温度升高使生长明显加快。\n")["version"]
    diff = call(workspace, "memo.answer_diff", thread_id=thread_id, answer_id=answer_id,
                from_version_id=old["version_id"], to_version_id=new["version_id"])
    [block] = diff["blocks"]
    assert [w for w in block["words"] if w["op"] != "equal"] == [{"op": "insert", "text": "明显"}]
    for side, skip in ((block["old_lines"], "insert"), (block["new_lines"], "delete")):
        assert "".join(w["text"] for w in block["words"] if w["op"] != skip) == "".join(side)


def test_the_history_is_still_there_after_a_restart(answered, tmp_path):
    workspace, thread_id, answer_id = answered
    save(workspace, thread_id, answer_id, EDITED)
    before = call(workspace, "memo.answer_history", thread_id=thread_id, answer_id=answer_id)
    workspace.close()
    reopened = _open(tmp_path / "workspace")
    try:
        assert call(reopened, "memo.answer_history", thread_id=thread_id, answer_id=answer_id) == before
    finally:
        reopened.close()


def test_taking_over_an_agent_reply_joins_the_same_history(answered):
    workspace, thread_id, answer_id = answered
    edited = save(workspace, thread_id, answer_id, EDITED)["version"]
    workspace.agent_dispatch(thread_id, example.QUESTION, "agent-request-0001")
    _finish(workspace)
    reply = next(m for m in workspace.thread_get(thread_id)["messages"] if m.get("agent_result"))
    adopted = call(workspace, "memo.answer_adopt", thread_id=thread_id, answer_id=answer_id,
                   from_message_id=reply["id"], expected_version_id=edited["version_id"])["version"]
    assert (adopted["source"], adopted["adopted_from"], adopted["parent_version_id"]) == ("AGENT", reply["id"], edited["version_id"])
    saved = call(workspace, "memo.answer_version_get", thread_id=thread_id, answer_id=answer_id,
                 version_id=adopted["version_id"])
    assert (saved["text"], saved["citations"]) == (reply["text"], reply["citations"])
    after = save(workspace, thread_id, answer_id, reply["text"] + "人工补充一句。\n")["version"]
    assert (after["source"], after["parent_version_id"]) == ("EDIT", adopted["version_id"])
    with pytest.raises(ValueError, match="AGENT_REPLY_NOT_FOUND"):
        call(workspace, "memo.answer_adopt", thread_id=thread_id, answer_id=answer_id,
             from_message_id=answer_id, expected_version_id=after["version_id"])


def test_a_claim_revision_is_saved_as_a_version_too(answered):
    workspace, thread_id, answer_id = answered
    check = call(workspace, "memo.answer_check", thread_id=thread_id, answer_id=answer_id)
    claim = check["claims"][0]
    call(workspace, "memo.answer_revise", thread_id=thread_id, answer_id=answer_id, expected_binding=check["binding"],
         claim_id=claim["id"], replacement="生长速度约翻倍")
    history = call(workspace, "memo.answer_history", thread_id=thread_id, answer_id=answer_id)
    first, second = history["versions"]
    assert (first["source"], second["source"]) == ("GENERATED", "CLAIM_REVISION")
    revised = call(workspace, "memo.answer_version_get", thread_id=thread_id, answer_id=answer_id,
                   version_id=second["version_id"])
    assert "生长速度约翻倍" in revised["text"] and claim["text"] not in revised["text"]
    # The check's own list of earlier texts is still kept.
    assert [h["version"] for h in call(workspace, "memo.answer_check", thread_id=thread_id, answer_id=answer_id)["history"]] == [1]


def test_forgetting_the_conversation_removes_its_saved_versions(answered):
    workspace, thread_id, answer_id = answered
    save(workspace, thread_id, answer_id, EDITED)
    thread = workspace.thread_get(thread_id)
    workspace.thread_forget(thread_id, thread["revision"])
    assert [r for r in workspace.store.list("answer_save", thread["project"]) if r.get("thread_id") == thread_id] == []
