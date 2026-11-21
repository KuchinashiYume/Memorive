"""How a research question ends: answered, answered from local excerpts only, refused, failed,
cancelled, interrupted by a restart, saved after a restart from the result the settings
service kept, or answered again.

Each test asks about the synthetic paper of examples/research_qa.py with a stand-in model, so the
whole path runs (queue, excerpts, prompt, model, checks, save) and nothing leaves the computer.
The code these tests cover starts at ResearchWorkspace.ask (memorive/research/workspace/service.py);
the steps of answering are in memorive/research/workspace/answering.py.
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

from memorive.research.workspace.service import ResearchWorkspace  # noqa: E402
from memorive.research.workspace.store import digest  # noqa: E402


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


class SettingsService:
    """Stands in for desktop.py's model function and the settings service behind it: the call is frozen
    into a snapshot, which the job keeps, and the service keeps the result under that snapshot."""

    def __init__(self, model):
        self.model, self.workspace, self.kept = model, None, {}

    def __call__(self, **request):
        snapshot = {"snapshot_id": "snapshot-" + request["job_id"], "model_name": example.PROFILE["model_name"]}
        self.workspace.note_model_snapshot(request["job_id"], snapshot)
        result = self.model(**request)
        receipt = dict(result["execution_receipt"], behavior_sha256="0" * 64)
        self.kept[snapshot["snapshot_id"]] = dict(result, status="PASS", execution_receipt=receipt,
                                                  requested_model=snapshot["model_name"], returned_model=snapshot["model_name"])
        return result

    def stored_result(self, snapshot):
        return self.kept.get(snapshot["snapshot_id"])


def _restart_after_the_model_answered(setup, tmp_path, *, keep_result):
    """The model returns, but the restart has already happened, so the first process cannot save the answer."""
    model = Model()
    model.release.clear()
    service = SettingsService(model)
    workspace, thread_id, material_id = setup(service)
    service.workspace = workspace
    job_id = _ask(workspace, thread_id, material_id, wait=False)
    assert model.called.wait(60)
    after_restart = Model()
    restarted = _open(tmp_path / "workspace", after_restart, recover=True)
    model.release.set()
    _finish(workspace)
    assert workspace.job_status(job_id)["status"] == "INTERRUPTED"
    if not keep_result:
        service.kept.clear()
    restarted.finish_answers_received_before_restart(service.stored_result)
    _finish(restarted)
    return restarted, after_restart, model, thread_id, job_id


def test_a_temporary_conversation_cut_off_during_the_model_call_is_gone_after_a_restart(tmp_path):
    # The restart forgets the temporary conversation with its job; the job used to be answered again
    # afterwards from its stale id, and opening the workspace failed (TypeError on the missing job).
    library = tmp_path / "library"
    library.mkdir()
    paper = library / "k12.md"
    paper.write_text(example.PAPER, encoding="utf-8")
    model = Model()
    model.release.clear()
    service = SettingsService(model)
    workspace = _open(tmp_path / "workspace", service)
    service.workspace = workspace
    restarted = None
    try:
        thread = workspace.thread_create(temporary=True)
        material = workspace.index.add_library_file(
            thread["project"], path=paper, root=library, external_id="k12",
            expected_hash=hashlib.sha256(paper.read_bytes()).hexdigest(), title="K12", material_layer="cleaned")
        workspace.attachments.configure(thread["id"], thread["revision"],
                                        {"artifact_ids": [material["id"]], "profile_ref": example.PROFILE["profile_ref"]})
        job_id = _ask(workspace, thread["id"], material["id"], wait=False)
        assert model.called.wait(60)
        restarted = _open(tmp_path / "workspace", Model(), recover=True)
        model.release.set()
        _finish(workspace)
        restarted.finish_answers_received_before_restart(service.stored_result)
        _finish(restarted)
        assert restarted.interrupted_during_model_call == []
        assert restarted.store.get("job", job_id) is None
        with pytest.raises(ValueError, match="THREAD_NOT_FOUND"):
            restarted.thread_get(thread["id"])
    finally:
        model.release.set()
        workspace.close()
        if restarted is not None:
            restarted.close()


def test_after_a_restart_an_answer_the_model_already_returned_is_saved(setup, tmp_path):
    restarted, after_restart, model, thread_id, job_id = _restart_after_the_model_answered(setup, tmp_path, keep_result=True)
    try:
        job = restarted.job_status(job_id)
        [answer] = _answers(restarted, thread_id)
        assert job["status"] == "COMPLETE" and job["answer_id"] == answer["id"]
        assert answer["engine"] == example.PROFILE["model_name"]
        assert (len(model.calls), len(after_restart.calls)) == (1, 0)  # asked once, before the restart
    finally:
        restarted.close()


def test_an_answer_saved_after_a_restart_records_the_request_the_model_was_given(setup, tmp_path):
    # Between the model call and the restart the material's title changes. The answer is saved from the
    # evidence and prompt kept on the job before the call; it used to search again and record a prompt the
    # model never saw (with the new title).
    model = Model()
    model.release.clear()
    sent = []

    class Recording(SettingsService):
        def __call__(self, **request):
            sent.append(request["prompt"])
            return super().__call__(**request)

    service = Recording(model)
    workspace, thread_id, material_id = setup(service)
    service.workspace = workspace
    job_id = _ask(workspace, thread_id, material_id, wait=False)
    assert model.called.wait(60)
    after_restart = Model()
    restarted = _open(tmp_path / "workspace", after_restart, recover=True)
    model.release.set()
    _finish(workspace)
    try:
        material = restarted.store.get("artifact", material_id)
        restarted.store.put("artifact", material_id, material["project"], dict(material, title="K12, renamed"),
                            expected=material["revision"])
        restarted.finish_answers_received_before_restart(service.stored_result)
        _finish(restarted)
        assert restarted.job_status(job_id)["status"] == "COMPLETE"
        [answer] = _answers(restarted, thread_id)
        assert answer["generation"]["prompt_sha256"] == digest(sent[0].encode())
        assert "K12, renamed" not in str(answer["generation"]["context"])
        assert (len(sent), len(after_restart.calls)) == (1, 0)
    finally:
        restarted.close()


@pytest.mark.parametrize("asked_in_conversation", [False, True])
def test_a_claim_review_running_at_a_restart_stays_interrupted(setup, tmp_path, asked_in_conversation):
    """A claim review calls the same model function as an answer, but it is not answered again after a
    restart: it keeps no model snapshot, so its kept result is never looked up and nothing is sent.
    Two shapes: a review started on its own, and a question in the conversation turned into a review."""
    workspace, thread_id, material_id = setup()
    project = workspace.thread_get(thread_id)["project"]
    identity = "research-" + "a" * 24
    review = {"kind": "answer_review", "project": project, "thread_id": thread_id, "answer_id": "answer-x",
              "status": "RUNNING", "cancel_requested": False, "created_at": "2026-11-16T00:00:00Z"}
    if asked_in_conversation:
        review.update(question_message={"role": "user", "text": "Check this answer."}, artifact_ids=[material_id])
    workspace.store.put("job", identity, project, review, expected=0)
    workspace.note_model_snapshot(identity, {"snapshot_id": "snapshot-review"})
    assert "model_snapshot" not in workspace.store.get("job", identity)
    workspace.close()
    after_restart = Model()
    restarted = _open(tmp_path / "workspace", after_restart, recover=True)
    try:
        looked_up = []
        restarted.finish_answers_received_before_restart(lambda snapshot: looked_up.append(snapshot) or {"status": "PASS"})
        _finish(restarted)
        assert restarted.job_status(identity)["status"] == "INTERRUPTED"
        assert (looked_up, after_restart.calls) == ([], [])
    finally:
        restarted.close()


def test_after_a_restart_without_a_kept_result_the_job_stays_interrupted(setup, tmp_path):
    restarted, after_restart, model, thread_id, job_id = _restart_after_the_model_answered(setup, tmp_path, keep_result=False)
    try:
        job = restarted.job_status(job_id)
        assert (job["status"], job["error"]) == ("INTERRUPTED", "Restarted; retry explicitly")
        assert _answers(restarted, thread_id) == []
        assert (len(model.calls), len(after_restart.calls)) == (1, 0)
    finally:
        restarted.close()


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


def test_an_answer_is_not_saved_when_its_file_changed_while_the_model_ran(setup):
    class ChangesTheFile(Model):
        def __call__(self, **request):
            for snapshot in (self.workspace.store.root / "library_snapshots").iterdir():
                snapshot.write_bytes(b"changed while the model was answering")
            return super().__call__(**request)

    model = ChangesTheFile()
    workspace, thread_id, material_id = setup(model)
    model.workspace = workspace
    job = workspace.job_status(_ask(workspace, thread_id, material_id))
    # The excerpts are checked again before saving, after the model has answered.
    assert (job["status"], job["error"]) == ("ERROR", "EVIDENCE_STALE")
    assert _answers(workspace, thread_id) == []

def test_a_conversation_answers_one_question_at_a_time(setup):
    model = Model()
    model.release.clear()
    workspace, thread_id, material_id = setup(model)
    job_id = _ask(workspace, thread_id, material_id, wait=False)
    assert model.called.wait(60)
    with pytest.raises(ValueError, match="THREAD_BUSY"):
        _ask(workspace, thread_id, material_id, "request-0002", wait=False)
    # Cancelled, but the model call is still running: the conversation stays busy until it ends.
    workspace.cancel(job_id)
    with pytest.raises(ValueError, match="THREAD_BUSY"):
        _ask(workspace, thread_id, material_id, "request-0003", wait=False)
    model.release.set()
    _finish(workspace)
    assert workspace.job_status(_ask(workspace, thread_id, material_id, "request-0004"))["status"] == "COMPLETE"

AGENT = {"profile_ref": "cli:codex", "eligible": True, "profile_kind": "CLI", "adapter_id": "codex_cli",
         "binding_hash": "agent", "model_name": "agent-model"}


class Agent:
    """Stands in for a coding agent run from the command line; keeps the prompt it was given."""

    def __init__(self, opportunities=()):
        self.prompts, self.opportunities = [], list(opportunities)

    def __call__(self, *, profile_ref, prompt, job_id, response_schema):
        self.prompts.append(prompt)
        first = example.EXCERPT_ID.search(prompt).group(0)
        opportunities = [dict(row, evidence_ids=[first if i == "FIRST" else i for i in row["evidence_ids"]])
                         for row in self.opportunities]
        answer = {"answer": "Growth doubles from 25 C to 37 C.", "citations": [first], "needs_agent": False,
                  "opportunities": opportunities}
        return {"response": answer, "model_name": AGENT["model_name"], "execution_receipt": {"status": "PASS"}}


def test_an_agent_job_works_from_the_agent_instructions_and_leaves_a_handoff_pack(tmp_path):
    from memorive.models.gateway.research_prompt_cache import CHAT_RULES
    from memorive.research.opportunities.research_opportunities import instructions

    library = tmp_path / "library"
    library.mkdir()
    paper = library / "k12.md"
    paper.write_text(example.PAPER, encoding="utf-8")
    workspace = ResearchWorkspace(tmp_path / "workspace", catalog=lambda: [dict(example.PROFILE), dict(AGENT)], model=Model())
    workspace.agent_model = agent = Agent()
    try:
        thread = workspace.thread_create()
        material = workspace.index.add_library_file(
            thread["project"], path=paper, root=library, external_id="k12",
            expected_hash=hashlib.sha256(paper.read_bytes()).hexdigest(), title="K12", material_layer="cleaned")
        workspace.attachments.configure(thread["id"], thread["revision"], {"artifact_ids": [material["id"]]})
        job_id = workspace.agent_dispatch(thread["id"], example.QUESTION, "agent-request-0001")["id"]
        _finish(workspace)
        job = workspace.job_status(job_id)
        assert (job["status"], job.get("error"), job["kind"]) == ("COMPLETE", None, "agent")
        # The agent is given its own instructions and then the chat rules (so it cites with [1], [2] and
        # never prints excerpt IDs in its answer); the saved hash is of that prompt. The sessions view
        # recognizes a research task by the first sentence.
        [prompt] = agent.prompts
        assert prompt.startswith(instructions() + "\n" + CHAT_RULES)
        assert prompt.startswith("You are the user-selected research agent. Complete the research question")
        [answer] = _answers(workspace, thread["id"])
        assert answer["generation"]["prompt_sha256"] == digest(prompt.encode())
        assert answer["agent_result"] is True and answer["opportunities"] == []
        handoff = workspace.store.get("handoff", job["handoff_id"])
        assert (handoff["question"], handoff["instructions"]) == (example.QUESTION, instructions())
    finally:
        workspace.close()


def test_an_agent_answer_is_kept_when_one_of_its_opportunities_cites_an_excerpt_it_was_not_given(tmp_path):
    from memorive.research.opportunities.research_opportunities import FIELDS
    good = {field: "Temperature above 37 C" for field in FIELDS}
    good.update(classification="missing_evidence", evidence_ids=["FIRST"])
    bad = dict(good, evidence_ids=["ev_not_given"])
    library = tmp_path / "library"
    library.mkdir()
    paper = library / "k12.md"
    paper.write_text(example.PAPER, encoding="utf-8")
    workspace = ResearchWorkspace(tmp_path / "workspace", catalog=lambda: [dict(example.PROFILE), dict(AGENT)], model=Model())
    workspace.agent_model = agent = Agent([good, bad])
    try:
        thread = workspace.thread_create()
        material = workspace.index.add_library_file(
            thread["project"], path=paper, root=library, external_id="k12",
            expected_hash=hashlib.sha256(paper.read_bytes()).hexdigest(), title="K12", material_layer="cleaned")
        workspace.attachments.configure(thread["id"], thread["revision"], {"artifact_ids": [material["id"]]})
        job_id = workspace.agent_dispatch(thread["id"], example.QUESTION, "agent-request-0002")["id"]
        _finish(workspace)
        assert workspace.job_status(job_id)["status"] == "COMPLETE"
        [answer] = _answers(workspace, thread["id"])
        assert answer["text"].startswith("Growth doubles") and len(agent.prompts) == 1
        [kept] = answer["opportunities"]
        assert kept["evidence_ids"] != ["ev_not_given"] and kept["state"] == "draft"
        assert answer["opportunity_issues"] == ["OPPORTUNITY_CITATION_INVALID"]
    finally:
        workspace.close()
