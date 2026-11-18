"""Follow-ups on an earlier research answer, typed in the chat (research/workspace/answer_conversation.py).

The synthetic paper and stand-in model of examples/research_qa.py give one cited answer; the
follow-ups below work on it without asking the model a new question: the evidence still missing,
a search for one aspect, a question back when the target is unclear (and the reply that names it),
a claim recast as a hypothesis. A follow-up with no earlier answer fails the job.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

EXAMPLE_FILE = Path(__file__).resolve().parents[1] / "examples" / "research_qa.py"
_spec = importlib.util.spec_from_file_location("research_qa_example_for_followups", EXAMPLE_FILE)
example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(example)

from memorive.research.workspace.service import ResearchWorkspace  # noqa: E402


@pytest.fixture
def answered(tmp_path):
    """A conversation with the example's answer; returns (workspace, thread id, material id, model)."""
    library = tmp_path / "library"
    library.mkdir()
    paper = library / "k12.md"
    paper.write_text(example.PAPER, encoding="utf-8")
    model = example.ExampleModel()
    workspace = ResearchWorkspace(tmp_path / "workspace", catalog=lambda: [dict(example.PROFILE)], model=model)
    thread = workspace.thread_create()
    material = workspace.index.add_library_file(
        thread["project"], path=paper, root=library, external_id="k12",
        expected_hash=hashlib.sha256(paper.read_bytes()).hexdigest(), title="K12", material_layer="cleaned")
    workspace.attachments.configure(thread["id"], thread["revision"],
                                    {"artifact_ids": [material["id"]], "profile_ref": example.PROFILE["profile_ref"]})
    ask(workspace, thread["id"], example.QUESTION, "question-0001", material["id"])
    yield workspace, thread["id"], material["id"], model
    workspace.close()


def ask(workspace, thread_id, question, request_id, material_id):
    job = workspace.ask(thread_id, question, request_id, artifact_ids=[material_id])
    for future in list(workspace.futures.values()):
        future.result(timeout=120)
    return workspace.job_status(job["id"])


def last_reply(workspace, thread_id):
    return [m for m in workspace.thread_get(thread_id)["messages"] if m["role"] == "assistant"][-1]


def test_asking_what_evidence_is_missing_answers_from_the_answer_itself(answered):
    workspace, thread_id, material_id, model = answered
    calls = len(model.calls)
    job = ask(workspace, thread_id, "查查还缺哪些证据", "followup-0001", material_id)
    reply = last_reply(workspace, thread_id)
    assert job["status"] == "COMPLETE" and job["answer_id"] == reply["id"]
    assert (reply["e1_action"], reply["engine"]) == ("coverage", "evidence_only")
    assert reply["citations"] and len(model.calls) == calls         # no new model question


@pytest.mark.parametrize("aspect", ["条件", "方法", "样本", "结果", "局限"])
def test_a_search_for_one_aspect_of_the_answer_lists_what_it_finds(answered, aspect):
    # A question about one paper plans only the question and its limitations; the other aspects
    # are searched with their standard terms.
    workspace, thread_id, material_id, _ = answered
    job = ask(workspace, thread_id, "补查上一回答的" + aspect, "followup-0002", material_id)
    reply = last_reply(workspace, thread_id)
    assert job["status"] == "COMPLETE" and reply["e1_action"] == "probe"
    assert reply["text"]


@pytest.mark.parametrize("question,pending", [
    ("核对上一回答的第9条", "review"),             # there is no ninth claim
    ("核对上一回答的这句", "review"),              # which sentence is not said
    ("补查一下上一回答", "probe"),                # which aspect is not said
    ("核对上一回答的第1条并补查上一回答的条件", None),  # two actions at once
])
def test_an_unclear_follow_up_is_answered_with_a_question(answered, question, pending):
    workspace, thread_id, material_id, _ = answered
    job = ask(workspace, thread_id, question, "followup-0003", material_id)
    reply = last_reply(workspace, thread_id)
    assert job["status"] == "COMPLETE" and reply["e1_action"] == "clarify" and reply["citations"] == []
    assert reply.get("e1_pending_request") == pending


def test_a_claim_can_be_recast_as_a_hypothesis(answered):
    workspace, thread_id, material_id, _ = answered
    job = ask(workspace, thread_id, "把上一回答的第1条改为待验证假设", "followup-0004", material_id)
    reply = last_reply(workspace, thread_id)
    assert job["status"] == "COMPLETE"
    assert reply["e1_action"] == "revise" and "待验证假设" in reply["text"]


def test_a_follow_up_without_an_earlier_answer_fails(tmp_path):
    workspace = ResearchWorkspace(tmp_path / "workspace", catalog=lambda: [dict(example.PROFILE)], model=example.ExampleModel())
    try:
        thread = workspace.thread_create()
        job = workspace.ask(thread["id"], "查查还缺哪些证据", "followup-0005")
        for future in list(workspace.futures.values()):
            future.result(timeout=120)
        status = workspace.job_status(job["id"])
        assert status["status"] == "ERROR" and "ANSWER_NOT_FOUND" in status["error"]
    finally:
        workspace.close()
