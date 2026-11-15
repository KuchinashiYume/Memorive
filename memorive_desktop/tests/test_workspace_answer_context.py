"""Research chat answers are saved only if their inputs did not change while the model ran
(memorive_research_workspace.answer_versions.validate_context / validate_retry_topic).

The question is frozen with the chosen model's configuration and the confirmed memories. If the
model configuration or a memory changes before the answer is saved, the answer is discarded and
the job reports why. A retry reuses the original frozen inputs, but not research-topic entries or
sources that changed or were withdrawn since.
"""
from __future__ import annotations

import copy

import pytest

from memorive_research_workspace.answer_versions import AnswerVersions
from memorive_research_workspace.service import ResearchWorkspace

RECEIPT = {"status": "PASS", "behavior_sha256": "b" * 64, "token_usage": {"input_tokens": 5}}
PROFILE = {"profile_ref": "api:chat", "eligible": True, "profile_kind": "API", "binding_hash": "h1",
           "model_name": "model-a"}


class _Model:
    def __init__(self):
        self.during = None

    def __call__(self, **kwargs):
        if self.during:
            self.during()
        return {"response": {"answer": "Growth doubled.", "citations": []}, "execution_receipt": RECEIPT,
                "model_name": "model-a"}


@pytest.fixture
def chat(tmp_path):
    catalog = [dict(PROFILE)]
    model = _Model()
    ws = ResearchWorkspace(tmp_path / "ws", catalog=lambda: copy.deepcopy(catalog), model=model)
    thread = ws.thread_create()
    ws.attachments.configure(thread["id"], thread["revision"], {"profile_ref": "api:chat"})
    yield ws, thread["id"], catalog, model
    ws.close()


def _ask(ws, thread_id, request_id="request-0001"):
    job = ws.ask(thread_id, "What changed in the results?", request_id)
    for future in list(ws.futures.values()):
        future.result(timeout=60)
    return ws.job_status(job["id"])


def _assistant_messages(ws, thread_id):
    return [m for m in ws.thread_get(thread_id)["messages"] if m["role"] == "assistant"]


def test_an_answer_whose_inputs_held_is_saved(chat):
    ws, thread_id, _, _ = chat
    job = _ask(ws, thread_id)
    assert job["status"] == "COMPLETE"
    assert [m["text"] for m in _assistant_messages(ws, thread_id)] == ["Growth doubled."]


@pytest.mark.parametrize("change", [
    lambda catalog: catalog[0].update(binding_hash="h2"),
    lambda catalog: catalog[0].update(model_name="model-b"),
    lambda catalog: catalog[0].update(eligible=False),
    lambda catalog: catalog.clear(),
])
def test_an_answer_is_discarded_when_the_model_configuration_changed_meanwhile(chat, change):
    ws, thread_id, catalog, model = chat
    model.during = lambda: change(catalog)
    job = _ask(ws, thread_id)
    assert (job["status"], job["error"]) == ("ERROR", "CHAT_MODEL_CONFIGURATION_CHANGED")
    assert _assistant_messages(ws, thread_id) == []


def test_an_answer_is_discarded_when_a_memory_changed_meanwhile(chat):
    ws, thread_id, _, model = chat
    memory = ws.memory_save("Report temperatures in Celsius.")
    model.during = lambda: ws.memory_save("Report temperatures in Kelvin.", memory_id=memory["id"],
                                          expected_revision=memory["revision"])
    job = _ask(ws, thread_id)
    assert (job["status"], job["error"]) == ("ERROR", "MEMORY_CHANGED_DURING_REQUEST")
    assert _assistant_messages(ws, thread_id) == []


def test_an_answer_is_discarded_when_a_memory_was_withdrawn_meanwhile(chat):
    ws, thread_id, _, model = chat
    memory = ws.memory_save("Report temperatures in Celsius.")
    model.during = lambda: ws.memory_forget(memory["id"], memory["revision"])
    job = _ask(ws, thread_id)
    assert (job["status"], job["error"]) == ("ERROR", "MEMORY_CHANGED_DURING_REQUEST")


def test_an_unrelated_change_does_not_discard_the_answer(chat):
    ws, thread_id, catalog, model = chat
    model.during = lambda: (catalog.append(dict(PROFILE, profile_ref="api:other", binding_hash="x")),
                            ws.memory_save("A new memory added during the call."))
    assert _ask(ws, thread_id)["status"] == "COMPLETE"


# Retries reuse the frozen topic inputs only while they are unchanged

def _entry(entry_id="e1", text="Growth doubles at 37 C.", **changes):
    value = {"id": entry_id, "kind": "finding", "text": text, "state": "active", "origin": "user", "anchor": None,
             "parents": []}
    value.update(changes)
    return value


def _snapshot(entries=(), sources=(), withheld=()):
    return {"entries": list(entries), "sources": list(sources), "unavailable_entry_ids": [],
            "withheld_message_ids": list(withheld)}


SOURCE = {"artifact_id": "art_1", "content_hash": "c" * 64}
CONTEXT = {"recent_messages": [{"message_id": "m1"}], "extractive_summary": [{"message_id": "m0"}]}


def _retry(saved, current, context=CONTEXT):
    return AnswerVersions.validate_retry_topic(None, {"topic_snapshot": current}, {"topic_snapshot": saved},
                                               copy.deepcopy(context))


def test_a_retry_with_unchanged_topic_inputs_goes_ahead():
    saved = _snapshot([_entry()], [SOURCE])
    assert _retry(saved, _snapshot([_entry(), _entry("e2", "Added later in another branch.")], [SOURCE])) is None


@pytest.mark.parametrize("current", [
    _snapshot([_entry(text="Growth triples at 37 C.")], [SOURCE]),
    _snapshot([_entry(state="withdrawn")], [SOURCE]),
    _snapshot([_entry(parents=["e0"])], [SOURCE]),
    _snapshot([], [SOURCE]),
    _snapshot([_entry()], [dict(SOURCE, content_hash="d" * 64)]),
    _snapshot([_entry()], []),
    _snapshot([_entry()], [SOURCE], withheld=["m1"]),
    _snapshot([_entry()], [SOURCE], withheld=["m0"]),
])
def test_a_retry_after_a_topic_entry_source_or_message_changed_is_refused(current):
    with pytest.raises(ValueError, match="RETRY_TOPIC_CONTEXT_CHANGED"):
        _retry(_snapshot([_entry()], [SOURCE]), current)


def test_withholding_a_message_the_answer_did_not_use_is_allowed():
    _retry(_snapshot([_entry()], [SOURCE]), _snapshot([_entry()], [SOURCE], withheld=["m9"]))
