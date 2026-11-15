"""Two checks around refinement and review:

* the result a model returns for a session refinement is kept only if it came from the chosen
  profile through exactly one call, carries a receipt and hash-sealed items for this task, and
  holds no credential field;
* the "logic review" choice on an inbox item can change only before the item is dispatched, and
  the dispatched request carries the choice.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from knowledge_feedback.canonical import make_hashed_payload, typed_payload_hash
from knowledge_feedback.conversation_refinement import make_context_capsule, validate_normalized_graph
from memorive_inbox import InboxController
from memorive_inbox.contracts import InboxError
from memorive_sessions import SessionsProductController
from memorive_sessions.product_controller import SessionProductError
from knowledge_feedback.conversation_refinement.refinement import make_typed_refinement_item

TASK = "refine-task-1"
PROFILE = "local:ollama:qwen"
REQUEST = {"task_id": TASK, "source_snapshot_sha256": "A" * 64}


def _formal_subject(request):
    """A refinement output for one selected user message, sealed as the refinement channel returns it."""
    conversation_id = "conversation:test"
    node = {"message_id": "message-00000000", "conversation_id": conversation_id, "parent_id": None, "role": "user",
            "blocks": [{"type": "text", "text": "验收会话需要生成可复核的本地精炼成品。", "metadata": {}}],
            "created_at": "SEQUENCE:00000000", "status": "VISIBLE_COMPLETE", "edit_of": None,
            "regeneration_group": None, "provider_sequence": 0}
    graph = validate_normalized_graph(make_hashed_payload({
        "schema_version": "CONVERSATION_REFINEMENT_NORMALIZED_CONVERSATION_GRAPH_V1",
        "graph_id": f"graph:{request['task_id']}", "revision": 1,
        "snapshot_ref": f"session-snapshot:{request['task_id']}@1",
        "snapshot_hash": typed_payload_hash({"source_snapshot_sha256": request["source_snapshot_sha256"]}),
        "conversation_ids": [conversation_id], "nodes": [node], "orphan_message_ids": [],
        "edge_policy": "EXPLICIT_PARENT_ONLY_NO_TIMESTAMP_INFERENCE", "deterministic": True}))
    capsule = make_context_capsule(capsule_id=f"capsule:{request['task_id']}:message-00000000",
                                   required_message_refs=[node["message_id"]], scope="SELECTED_SESSION_MESSAGES")
    item = make_typed_refinement_item(item_id=f"item:{request['task_id']}:message-00000000", graph=graph, node=node,
                                      capsule=capsule, scope="SELECTED_SESSION_MESSAGES")
    return {"schema_version": "CONVERSATION_REFINEMENT_SUBJECT_OUTPUT_V1", "pack_id": request["task_id"],
            "graph_hash": copy.deepcopy(graph["content_hash"]), "items": [item]}


def _result(**changes):
    value = {"profile_ref": PROFILE, "requested_model": "qwen3", "returned_model": "qwen3",
             "subject_output": _formal_subject(REQUEST), "model_calls": 1, "external_model_calls": 0,
             "external_network_calls": 0, "provider_calls": 1, "execution_receipt": {"status": "PASS"}}
    value.update(changes)
    return value


def _check(value, task_id=TASK):
    owner = SimpleNamespace(_reject_sensitive_channel_result=SessionsProductController._reject_sensitive_channel_result)
    return SessionsProductController._validate_refinement_channel_result(owner, value, task_id=task_id,
                                                                       profile_ref=PROFILE)


def test_a_result_from_the_chosen_profile_is_kept():
    value = _result()
    before = copy.deepcopy(value)
    items = value["subject_output"]["items"]
    accepted = _check(value)
    assert accepted["subject_output"]["items"][0]["item_id"] == value["subject_output"]["items"][0]["item_id"]
    assert value == before and value["subject_output"]["items"] is items


def _subject(**changes):
    subject = _formal_subject(REQUEST)
    subject.update(changes)
    return subject


def _tampered_item():
    """An item claiming the refiner decided the claim is true."""
    subject = _formal_subject(REQUEST)
    subject["items"][0]["truth_decided_by_refiner"] = True
    return subject


@pytest.mark.parametrize("value, code", [
    ("result", "SESSION_REFINEMENT_CHANNEL_RESULT_FIELDS_INVALID"),
    ({k: v for k, v in _result().items() if k != "provider_calls"}, "SESSION_REFINEMENT_CHANNEL_RESULT_FIELDS_INVALID"),
    (dict(_result(), raw_text="x"), "SESSION_REFINEMENT_CHANNEL_RESULT_FIELDS_INVALID"),
    (_result(profile_ref="local:ollama:other"), "SESSION_REFINEMENT_CHANNEL_RESULT_IDENTITY_INVALID"),
    (_result(requested_model=" "), "SESSION_REFINEMENT_CHANNEL_MODEL_IDENTITY_MISSING"),
    (_result(returned_model=None), "SESSION_REFINEMENT_CHANNEL_MODEL_IDENTITY_MISSING"),
    (_result(model_calls=0), "SESSION_REFINEMENT_CHANNEL_CALL_COUNT_INVALID"),
    (_result(model_calls=2), "SESSION_REFINEMENT_CHANNEL_CALL_COUNT_INVALID"),
    (_result(external_model_calls=2), "SESSION_REFINEMENT_CHANNEL_EFFECT_COUNT_INVALID"),
    (_result(external_network_calls=True), "SESSION_REFINEMENT_CHANNEL_EFFECT_COUNT_INVALID"),
    (_result(provider_calls=-1), "SESSION_REFINEMENT_CHANNEL_EFFECT_COUNT_INVALID"),
    (_result(subject_output=[]), "SESSION_REFINEMENT_Capabilities_SUBJECT_OUTPUT_INVALID"),
    (_result(subject_output=_subject(extra=1)), "SESSION_REFINEMENT_Capabilities_SUBJECT_OUTPUT_INVALID"),
    (_result(subject_output=_subject(schema_version="OTHER")), "SESSION_REFINEMENT_Capabilities_SUBJECT_OUTPUT_INVALID"),
    (_result(subject_output=_subject(pack_id="refine-task-2")), "SESSION_REFINEMENT_Capabilities_SUBJECT_OUTPUT_INVALID"),
    (_result(subject_output=_subject(graph_hash="A" * 64)), "SESSION_REFINEMENT_Capabilities_SUBJECT_OUTPUT_INVALID"),
    (_result(subject_output=_subject(items=[])), "SESSION_REFINEMENT_Capabilities_SUBJECT_OUTPUT_INVALID"),
    (_result(subject_output=_subject(items={})), "SESSION_REFINEMENT_Capabilities_SUBJECT_OUTPUT_INVALID"),
    (_result(subject_output=_subject(items={"item": 1})), "SESSION_REFINEMENT_Capabilities_SUBJECT_OUTPUT_INVALID"),
    (_result(subject_output=_tampered_item()), "SESSION_REFINEMENT_Capabilities_TYPED_ITEM_INVALID"),
    (_result(execution_receipt={}), "SESSION_REFINEMENT_CHANNEL_RECEIPT_REQUIRED"),
    (_result(execution_receipt="PASS"), "SESSION_REFINEMENT_CHANNEL_RECEIPT_REQUIRED"),
    (_result(execution_receipt={"status": "PASS", "request": {"Authorization": "Bearer x"}}),
     "SESSION_REFINEMENT_RESULT_SENSITIVE_FIELD_FORBIDDEN"),
    (_result(execution_receipt={"status": "PASS", "attempts": [{"api_key": "x"}]}),
     "SESSION_REFINEMENT_RESULT_SENSITIVE_FIELD_FORBIDDEN"),
    (_result(execution_receipt={"status": "PASS", "tags": {"a", "b"}}), "SESSION_REFINEMENT_CHANNEL_RESULT_JSON_INVALID"),
])
def test_a_result_that_cannot_be_traced_or_holds_a_credential_is_refused(value, code):
    with pytest.raises(SessionProductError) as caught:
        _check(value)
    assert str(caught.value) == code


def test_a_result_for_another_task_is_refused():
    with pytest.raises(SessionProductError, match="SUBJECT_OUTPUT_INVALID"):
        _check(_result(), task_id="refine-task-2")


@pytest.mark.parametrize("counts", [
    {"external_model_calls": 1, "external_network_calls": 1, "provider_calls": 0},
    {"external_model_calls": 0, "external_network_calls": 0, "provider_calls": 0},
])
def test_zero_or_one_outside_effect_is_accepted(counts):
    _check(_result(**counts))


# Logic review choice on an inbox item

@pytest.fixture
def inbox(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "paper.md").write_text("paper text", encoding="utf-8")
    controller = InboxController(source_roots=[source_root], data_root=tmp_path / "inbox",
                                 include_sample_projection=False)
    item = controller.import_paths([source_root / "paper.md"], request_id="review-choice")["items"][0]["item"]
    return controller, item["item_id"], source_root / "paper.md"


def test_the_choice_can_be_changed_before_dispatch_and_travels_with_it(inbox):
    controller, item_id, _ = inbox
    assert controller.set_logic_review(item_id, selected=True)["logic_review_selected"] is True
    assert controller.set_logic_review(item_id, selected=False)["logic_review_selected"] is False
    controller.set_logic_review(item_id, selected=True)
    controller.workflow_snapshot_provider = lambda: {"snapshot": "test"}
    intent = controller.dispatch(item_id, idempotency_key="dispatch-1")["intent"]
    assert intent["params"]["literature_review"]["logic_selected"] is True


@pytest.mark.parametrize("selected", [1, "yes", None])
def test_the_choice_must_be_yes_or_no(inbox, selected):
    controller, item_id, _ = inbox
    with pytest.raises(InboxError, match="INBOX_LOGIC_SELECTION_INVALID"):
        controller.set_logic_review(item_id, selected=selected)


def test_the_choice_is_fixed_once_dispatched(inbox):
    controller, item_id, _ = inbox
    controller.dispatch(item_id, idempotency_key="dispatch-1")
    with pytest.raises(InboxError, match="INBOX_REVIEW_ALREADY_DISPATCHED"):
        controller.set_logic_review(item_id, selected=True)


@pytest.mark.parametrize("change", [
    {"job_id": "job-1"}, {"state": "PROCESSING"}, {"state": "COMPLETED"}, {"item_kind": "SESSION_REFINEMENT"},
])
def test_the_choice_is_fixed_for_items_already_running_or_not_reviewable(inbox, change):
    controller, item_id, _ = inbox
    controller.store.update_item(item_id, "TEST_STATE", lambda row: row.update(change))
    with pytest.raises(InboxError, match="INBOX_REVIEW_ALREADY_DISPATCHED"):
        controller.set_logic_review(item_id, selected=True)


def test_a_failed_item_may_change_its_choice_before_retry(inbox):
    controller, item_id, _ = inbox
    controller.store.update_item(item_id, "TEST_STATE", lambda row: row.update(state="ERROR"))
    assert controller.set_logic_review(item_id, selected=True)["logic_review_selected"] is True


def test_a_duplicate_import_cannot_be_chosen_for_review(inbox):
    controller, _, source = inbox
    duplicate = controller.import_paths([source], request_id="review-duplicate")["items"][0]["item"]
    assert duplicate["state"] == "DEDUPLICATED"
    with pytest.raises(InboxError, match="INBOX_REVIEW_ALREADY_DISPATCHED"):
        controller.set_logic_review(duplicate["item_id"], selected=True)
    with pytest.raises(InboxError, match="INBOX_ITEM_NOT_FOUND"):
        controller.set_logic_review("item_missing", selected=True)
