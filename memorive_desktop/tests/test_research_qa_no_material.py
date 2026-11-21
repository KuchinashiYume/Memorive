"""A research question with no material to answer from, and the scope of a new conversation.

A new conversation searches the whole project (ResearchWorkspace.thread_create). When the search keeps no
excerpt and a model is chosen, the answer says what is missing and gives no value from the model's memory;
the model's general background, if any, is kept apart and marked [AI] (memorive/research/workspace/
no_material.py, called from answering.py). The stand-in model answers in the no-material format, so the
whole path runs (queue, empty search, prompt, model, save) and nothing leaves the computer.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

EXAMPLE_FILE = Path(__file__).resolve().parents[1] / "examples" / "research_qa.py"
_spec = importlib.util.spec_from_file_location("research_qa_example_for_no_material", EXAMPLE_FILE)
example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(example)

from memorive.research.workspace import no_material  # noqa: E402
from memorive.research.workspace.service import ResearchWorkspace  # noqa: E402

QUESTION = "How much did residual connections lower the top-1 error of a 34-layer network on ImageNet?"


class NoMaterialModel:
    """Answers in the format it is given: the no-material one, or the ordinary answer when asked to."""

    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply if reply is not None else {
            "missing": "The ResNet paper's ImageNet validation table would answer this.",
            "background": ("Residual connections let a layer learn a change to its input instead of the whole mapping. "
                           "The plain network had 28.54% top-1 error. "
                           "Look for papers on deep residual learning and the degradation problem."),
        }

    def __call__(self, *, profile_ref, prompt, job_id, response_schema):
        self.calls.append({"prompt": prompt, "schema": response_schema})
        return {"response": dict(self.reply), "model_name": example.PROFILE["model_name"],
                "execution_receipt": {"status": "PASS", "token_usage": {"input_tokens": len(prompt) // 3}}}


@pytest.fixture
def ask(tmp_path):
    made = []

    def run(model):
        workspace = ResearchWorkspace(tmp_path / "workspace", catalog=lambda: [dict(example.PROFILE)], model=model)
        made.append(workspace)
        thread = workspace.thread_create()
        workspace.attachments.configure(thread["id"], thread["revision"], {"profile_ref": example.PROFILE["profile_ref"]})
        job = workspace.ask(thread["id"], QUESTION, "request-0001")
        for future in list(workspace.futures.values()):
            future.result(timeout=120)
        answers = [m for m in workspace.thread_get(thread["id"])["messages"] if m["role"] == "assistant"]
        return workspace, workspace.job_status(job["id"]), answers

    yield run
    for workspace in made:
        workspace.close()


def test_a_new_conversation_searches_the_whole_project(tmp_path):
    workspace = ResearchWorkspace(tmp_path / "workspace", catalog=lambda: [dict(example.PROFILE)])
    try:
        thread = workspace.thread_create()
        assert (thread["scope"], thread["include_project"]) == ("project", True)
    finally:
        workspace.close()


def test_with_no_material_the_answer_says_what_is_missing_and_gives_no_value(ask):
    model = NoMaterialModel()
    workspace, job, [answer] = ask(model)
    assert job["status"] == "COMPLETE"
    # The model was asked only what is missing, without the reading method that asks for cited answers.
    [call] = model.calls
    assert set(call["schema"]["properties"]) == {"missing", "background"}
    assert no_material.RULES in call["prompt"] and "Use the memo-research reading method" not in call["prompt"]
    record = answer["no_material"]
    assert answer["citations"] == [] and answer["text"].startswith(record["main_text"])
    assert "ImageNet validation table" in record["main_text"]
    # The sentence with a value from memory is left out; the qualitative background stays, marked [AI].
    assert "28.54" not in answer["text"] and record["sentences_with_values_left_out"] == 1
    assert record["ai_background"].startswith("Residual connections let a layer learn")
    assert record["ai_label"].startswith("[AI]") and record["ai_label"] in answer["text"]
    assert record["searched_project"] is True
    # Nothing of it becomes knowledge.
    assert workspace.state("default")["feedback"] == [] and "feedback_id" not in answer


def test_with_no_material_an_ordinary_answer_is_refused(ask):
    model = NoMaterialModel(reply={"answer": "The error fell from 28.54% to 25.03%.", "citations": [], "needs_agent": False})
    _, job, answers = ask(model)
    assert (job["status"], job["error"]) == ("ERROR", "CHAT_RESPONSE_INVALID")
    assert answers == []


@pytest.mark.parametrize("text, kept, left_out", [
    ("残差连接让信号跨层传递。ResNet-34 的错误率是 25.03%。VGG16 更深。", "残差连接让信号跨层传递。VGG16 更深。", 1),
    ("It was published in 2016. Shortcuts add no parameters.", "Shortcuts add no parameters.", 1),
    ("", "", 0),
])
def test_sentences_with_values_are_left_out(text, kept, left_out):
    assert no_material.without_values(text) == (kept, left_out)
