"""Session refinement: what the model's answer must look like, and the items made from it.

The workflow bridge (memorive_workflow_bridge/conversation_refinement.py) turns the selected
messages of a session into a conversation graph, asks a model for one proposal per message, and
turns each proposal into a typed refinement item (knowledge_feedback/conversation_refinement).
The model is replaced here by a local adapter that returns a prepared answer.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from knowledge_feedback.canonical import typed_payload_hash
from knowledge_feedback.conversation_refinement import validate_typed_refinement_item
from memorive_workflow_bridge.conversation_refinement import (
    ConversationRefinementBridgeError,
    ExistingConversationRefinementChannel,
)

PROFILE = "local:0123456789abcdef01234567"
MESSAGES = [
    {"role": "user", "content": "DECISION: keep the source of every claim."},
    {"role": "assistant", "content": "Then build the candidate items from the kept sources."},
    {"role": "user", "content": "Why did the first run fail?"},
]
ANSWER = {"items": [
    {"message_id": "message-00000000", "candidate_text": "Keep the source of every claim.", "refinement_type": "USER_DECISION_CLAIM"},
    {"message_id": "message-00000001", "candidate_text": "Build candidates from kept sources.", "refinement_type": "AI_SUGGESTION"},
    {"message_id": "message-00000002", "candidate_text": "Why did the first run fail?", "refinement_type": "OPEN_QUESTION"},
]}


def request(messages=MESSAGES):
    return {
        "task_id": "refinement-test-002", "logical_slot": "conversation_refinement", "profile_ref": PROFILE,
        "explicit_user_action": True, "source_snapshot_sha256": "a" * 64,
        "source": {"local_projection_id": "session-002", "provider_id": "deepseek"},
        "messages": deepcopy(messages), "review_required": True, "auto_ingest": False,
    }


class Registry:
    def call(self, method, params):
        return {"recognized_models": [{
            "profile_ref": PROFILE, "chat_endpoint": "http://127.0.0.1:11434/api/chat", "endpoint_kind": "ollama",
            "model_name": "qwen-test", "model_digest": "D" * 64, "exact_identity_available": True,
            "structured_chat_adapter": "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1", "execution_eligible": True,
            "connection_status": "AVAILABLE"}]}


class Adapter:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        model = kwargs["binding"]["requested_model"]
        return SimpleNamespace(success=True, error_code=None, response=deepcopy(self.answer), receipt={
            "schema_version": "EvaluationAssetsLocalStructuredChatReceipt-v1", "status": "PASS",
            "requested_model": model, "returned_model": model, "model_digest": "D" * 64,
            "local_model_request_count": 1, "external_request_count": 0, "cloud_fallback_count": 0, "api_cost_cny": 0.0})


def run(answer=ANSWER, messages=MESSAGES):
    adapter = Adapter(answer)
    channel = ExistingConversationRefinementChannel(local_model_registry=Registry(), local_adapter=adapter)
    return channel.execute(request(messages)), adapter


def test_each_answer_becomes_a_typed_item_bound_to_its_message():
    result, adapter = run()
    subject = result["subject_output"]
    assert subject["schema_version"] == "CONVERSATION_REFINEMENT_SUBJECT_OUTPUT_V1"
    assert subject["pack_id"] == "refinement-test-002"
    items = subject["items"]
    assert [i["message_refs"] for i in items] == [["message-00000000"], ["message-00000001"], ["message-00000002"]]
    assert [i["refinement_type"] for i in items] == ["USER_DECISION_CLAIM", "AI_SUGGESTION", "OPEN_QUESTION"]
    assert [i["evidence_state"] for i in items] == ["UNVERIFIED_CLAIM"] * 3
    assert [i["limitations"] for i in items] == [[], ["REQUIRES_INDEPENDENT_EVIDENCE_OR_USER_DECISION"], []]
    assert {i["producer"] for i in items} == {"CONVERSATION_REFINEMENT_MODEL_ASSISTED_REFINER_V1"}
    assert {i["graph_hash"]["value"] for i in items} == {subject["graph_hash"]["value"]}
    for item in items:
        assert item["candidate_text_hash"] == typed_payload_hash({"candidate_text": item["candidate_text"]})
        assert item["truth_decided_by_refiner"] is False and item["scope"] == "SELECTED_SESSION_MESSAGES"
        assert validate_typed_refinement_item(item) == item
    assert len(adapter.calls) == 1


def test_the_prompt_carries_every_selected_message_whole():
    long_text = "x" * 150_000
    messages = MESSAGES[:2] + [{"role": "user", "content": long_text}]
    answer = deepcopy(ANSWER)
    _, adapter = run(answer, messages)
    prompt = adapter.calls[0]["prompt"]
    assert long_text in prompt and MESSAGES[0]["content"] in prompt
    schema = adapter.calls[0]["schema"]
    assert [row["properties"]["message_id"]["const"] for row in schema["properties"]["items"]["prefixItems"]] == [
        "message-00000000", "message-00000001", "message-00000002"]


def test_secrets_in_an_answer_are_redacted():
    answer = deepcopy(ANSWER)
    answer["items"][1]["candidate_text"] = "Mail the summary to someone@example.org."
    result, _ = run(answer)
    item = result["subject_output"]["items"][1]
    assert item["candidate_text"] == "Mail the summary to [REDACTED:EMAIL]."


def test_each_item_names_the_context_it_was_read_in():
    result, _ = run()
    items = result["subject_output"]["items"]
    assert [i["context_capsule_ref"].split("@")[0] for i in items] == [
        f"capsule:graph:refinement-test-002:message-0000000{n}" for n in range(3)]
    assert all("CONTEXT_INSUFFICIENT" not in i["limitations"] for i in items)


def changed(change):
    answer = deepcopy(ANSWER)
    change(answer)
    return answer


@pytest.mark.parametrize("answer, code", [
    ("not an object", "REFINEMENT_PROPOSAL_OBJECT_REQUIRED"),
    ({"items": ANSWER["items"], "note": "extra"}, "REFINEMENT_PROPOSAL_FIELDS_INVALID"),
    ({"items": ANSWER["items"][:2]}, "REFINEMENT_PROPOSAL_ITEM_COUNT_INVALID"),
    ({"items": "none"}, "REFINEMENT_PROPOSAL_ITEM_COUNT_INVALID"),
    (changed(lambda a: a["items"][0].update(confidence=1)), "REFINEMENT_PROPOSAL_ITEM_FIELDS_INVALID"),
    (changed(lambda a: a["items"][0].pop("refinement_type")), "REFINEMENT_PROPOSAL_ITEM_FIELDS_INVALID"),
    (changed(lambda a: a["items"].reverse()), "REFINEMENT_PROPOSAL_ITEM_ORDER_INVALID"),
    (changed(lambda a: a["items"][1].update(candidate_text="   ")), "REFINEMENT_PROPOSAL_CANDIDATE_TEXT_INVALID"),
    (changed(lambda a: a["items"][1].update(candidate_text=7)), "REFINEMENT_PROPOSAL_CANDIDATE_TEXT_INVALID"),
    (changed(lambda a: a["items"][1].update(candidate_text="a\x00b")), "REFINEMENT_PROPOSAL_CANDIDATE_TEXT_INVALID"),
    (changed(lambda a: a["items"][1].update(candidate_text="y" * 10_001)), "REFINEMENT_PROPOSAL_CANDIDATE_TEXT_INVALID"),
    (changed(lambda a: a["items"][1].update(refinement_type="FACT")), "REFINEMENT_PROPOSAL_TYPE_INVALID"),
])
def test_an_answer_that_does_not_fit_is_refused(answer, code):
    with pytest.raises(ConversationRefinementBridgeError, match=code):
        run(answer)


def test_an_answer_of_exactly_ten_thousand_characters_is_accepted():
    answer = changed(lambda a: a["items"][1].update(candidate_text="z" * 10_000))
    result, _ = run(answer)
    assert result["subject_output"]["items"][1]["candidate_text"] == "z" * 10_000


def test_an_answer_padded_past_ten_thousand_characters_is_refused():
    answer = changed(lambda a: a["items"][1].update(candidate_text="z" * 9_999 + "  "))
    with pytest.raises(Exception):
        run(answer)


@pytest.mark.parametrize("change, code", [
    (lambda r: r.update(explicit_user_action=False), "REFINEMENT_EXPLICIT_ACTION_REQUIRED"),
    (lambda r: r.update(auto_ingest=True), "REFINEMENT_REVIEW_BOUNDARY_INVALID"),
    (lambda r: r.update(messages=[]), "REFINEMENT_MESSAGES_REQUIRED"),
    (lambda r: r["messages"].append({"role": "system", "content": "x"}), "REFINEMENT_MESSAGE_ROLE_INVALID"),
    (lambda r: r["messages"].append({"role": "user", "content": "  "}), "REFINEMENT_MESSAGE_CONTENT_INVALID"),
    (lambda r: r.update(source_snapshot_sha256="xyz"), "REFINEMENT_SNAPSHOT_HASH_INVALID"),
])
def test_a_request_must_come_from_a_reviewed_explicit_action(change, code):
    value = request()
    change(value)
    channel = ExistingConversationRefinementChannel(local_model_registry=Registry(), local_adapter=Adapter(ANSWER))
    with pytest.raises(ConversationRefinementBridgeError, match=code):
        channel.execute(value)
