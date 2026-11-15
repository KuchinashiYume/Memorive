"""How a research question ends: answered, answered from local excerpts only, refused, failed,
cancelled, interrupted by a restart, or answered again.

Each test asks about the synthetic paper of examples/research_qa.py with a stand-in model, so the
whole path runs (queue, excerpts, prompt, model, checks, save) and nothing leaves the computer.
The code these tests cover starts at ResearchWorkspace.ask (memorive_research_workspace/service.py).
"""
from __future__ import annotations

import hashlib
import importlib.util
import threading
from pathlib import Path

import pytest

EXAMPLE_FILE = Path(__file__).resolve().parents[1] / "examples" / "research_qa.py"
_spec = importlib.util.spec_from_file_location("research_qa_example_for_flow", EXAMPLE_FILE)
example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(example)

from memorive_research_workspace.service import ResearchWorkspace  # noqa: E402


class Model(example.ExampleModel):
    """The example's model, with hooks: wait for a signal, cite something else, or fail."""

    def __init__(self, *, cite=None, error=None):
        super().__init__()
        self.cite, self.error = cite, error
        self.called, self.release = threading.Event(), threading.Event()
        self.release.set()

    def __call__(self, **request):
        result = super().__call__(**request)
        self.called.set()
        self.release.wait(60)
        if self.error:
            raise self.error
        if self.cite is not None:
            result["response"]["citations"] = self.cite
        return result


def _open(root, model, *, recover=False):
    return ResearchWorkspace(root, catalog=lambda: [dict(example.PROFILE)], model=model, recover=recover)


@pytest.fixture
def setup(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    paper = library / "k12.md"
    paper.write_text(example.PAPER, encoding="utf-8")

    def make(model=None, *, with_model=True):
        workspace = _open(tmp_path / "workspace", model or Model())
        thread = workspace.thread_create()
        material = workspace.index.add_library_file(
            thread["project"], path=paper, root=library, external_id="k12",
            expected_hash=hashlib.sha256(paper.read_bytes()).hexdigest(), title="K12", material_layer="cleaned")
        settings = {"artifact_ids": [material["id"]]}
        if with_model:
            settings["profile_ref"] = example.PROFILE["profile_ref"]
        workspace.attachments.configure(thread["id"], thread["revision"], settings)
        made.append(workspace)
        return workspace, thread["id"], material["id"]

    made = []
    yield make
    for workspace in made:
        workspace.close()


def _ask(workspace, thread_id, material_id, request_id="request-0001", *, wait=True):
    job = workspace.ask(thread_id, example.QUESTION, request_id, artifact_ids=[material_id])
    if wait:
        _finish(workspace)
    return job["id"]


def _finish(workspace):
    for future in list(workspace.futures.values()):
        future.result(timeout=120)


def _answers(workspace, thread_id):
    return [m for m in workspace.thread_get(thread_id)["messages"] if m["role"] == "assistant"]


def test_the_answer_cites_the_excerpt_it_was_given_and_is_saved(setup):
    workspace, thread_id, material_id = setup()
    job = workspace.job_status(_ask(workspace, thread_id, material_id))
    [answer] = _answers(workspace, thread_id)
    assert job["status"] == "COMPLETE" and job["answer_id"] == answer["id"]
    assert [c["artifact_id"] for c in answer["citations"]] == [material_id]
    assert answer["engine"] == example.PROFILE["model_name"]
    assert answer["generation"]["job_id"] == job["id"]


def test_without_a_model_the_answer_lists_the_excerpts_found(setup):
    model = Model()
    workspace, thread_id, material_id = setup(model, with_model=False)
    job = workspace.job_status(_ask(workspace, thread_id, material_id))
    [answer] = _answers(workspace, thread_id)
    assert job["status"] == "COMPLETE" and model.calls == []
    assert answer["engine"] == "evidence_only"
    assert answer["citations"] and "Growth at 37 C" in answer["text"]


def test_an_answer_citing_an_excerpt_it_was_not_shown_is_refused(setup):
    workspace, thread_id, material_id = setup(Model(cite=["ev_" + "0" * 32]))
    job = workspace.job_status(_ask(workspace, thread_id, material_id))
    assert (job["status"], job["error"]) == ("ERROR", "CHAT_CITATION_NOT_IN_CONTEXT")
    assert _answers(workspace, thread_id) == []


def test_a_failed_model_call_ends_the_job_with_its_reason(setup):
    workspace, thread_id, material_id = setup(Model(error=ValueError("PROVIDER_UNAVAILABLE")))
    job = workspace.job_status(_ask(workspace, thread_id, material_id))
    assert (job["status"], job["error"]) == ("ERROR", "PROVIDER_UNAVAILABLE")
    assert _answers(workspace, thread_id) == []


def test_cancelling_during_the_model_call_discards_the_late_answer(setup):
    model = Model()
    model.release.clear()
    workspace, thread_id, material_id = setup(model)
    job_id = _ask(workspace, thread_id, material_id, wait=False)
    assert model.called.wait(60)
    cancelled = workspace.cancel(job_id)
    # The job reads as cancelled at once; the model call itself is not stopped.
    assert (cancelled["status"], cancelled["cancel_requested"]) == ("CANCELLED", True)
    model.release.set()
    _finish(workspace)
    job = workspace.job_status(job_id)
    assert job["status"] == "CANCELLED" and job["late_result"] == "DISCARDED" and job["late_usage"]
    assert _answers(workspace, thread_id) == []


def test_a_job_running_when_the_app_restarts_is_marked_interrupted(setup, tmp_path):
    model = Model()
    model.release.clear()
    workspace, thread_id, material_id = setup(model)
    job_id = _ask(workspace, thread_id, material_id, wait=False)
    assert model.called.wait(60)
    restarted = _open(tmp_path / "workspace", Model(), recover=True)
    try:
        job = restarted.job_status(job_id)
        assert (job["status"], job["error"]) == ("INTERRUPTED", "Restarted; retry explicitly")
    finally:
        model.release.set()
        _finish(workspace)
        restarted.close()
    # The answer the first process still received is not saved over the restart's decision.
    assert workspace.job_status(job_id)["status"] == "INTERRUPTED"
    assert _answers(workspace, thread_id) == []


def test_answering_again_calls_the_model_again_and_keeps_both_versions(setup):
    model = Model()
    workspace, thread_id, material_id = setup(model)
    _ask(workspace, thread_id, material_id)
    [first] = _answers(workspace, thread_id)
    retry = workspace.versions.retry(thread_id, first["id"], "request-0002")
    _finish(workspace)
    job = workspace.job_status(retry["id"])
    assert (job["status"], job.get("error")) == ("COMPLETE", None) and job["retry_of"] == first["id"]
    assert len(model.calls) == 2
    versions = [m for m in workspace.thread_get(thread_id)["messages"] if m["role"] == "assistant"]
    assert job["answer_id"] in {m["id"] for m in versions}
