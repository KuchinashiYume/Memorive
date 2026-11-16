"""Conversation refinement: what its validators accept and reject, and what the builders make.

Pins today's behaviour of memorive.sessions.refinement (graph, redaction,
context capsule, typed refinement items) before the planned removal of the hash envelopes
(make_hashed_payload / verify_hashed_payload) around these records.

Each variant changes one field and seals the record again, so the field checks behind the
envelope are what is recorded. The cases named "*_not_resealed" change a record without
sealing it again: today the envelope rejects them, and after the envelope goes they are
expected to pass or to fail on the field checks instead. 64-character hex values are masked,
since the hashes of these records will change with the envelope.
"""
import re
from copy import deepcopy
from pathlib import Path

from knowledge_feedback.canonical import make_hashed_payload
from memorive.sessions.refinement import (
    make_context_capsule,
    make_model_refinement_items,
    redact_text,
    validate_context_capsule,
    validate_normalized_graph,
    validate_redaction_result,
    validate_typed_refinement_item,
)
from memorive.sessions.refinement.model_channel import _build_graph
from tests.characterize.golden import check, outcome

HERE = Path(__file__).parent
_HEX = re.compile(r"\b[0-9a-fA-F]{64}\b")


def _mask(value):
    if isinstance(value, dict):
        return {key: _mask(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask(item) for item in value]
    if isinstance(value, str):
        return _HEX.sub("<sha256>", value)
    return value


def _run(function, *args, **kwargs):
    return _mask(outcome(function, *args, **kwargs))


def _reseal(record, change):
    payload = {key: deepcopy(value) for key, value in record.items() if key != "content_hash"}
    change(payload)
    return make_hashed_payload(payload)


def _graph():
    request = {
        "task_id": "task-characterize",
        "source": {"kind": "desktop-session", "session_id": "s-1"},
        "source_snapshot_sha256": "A" * 64,
        "messages": [
            {"role": "user", "content": "Please always cite the page number."},
            {"role": "assistant", "content": "Understood, I will cite page numbers."},
            {"role": "user", "content": "My email is someone@example.org, token=abcdef123456"},
        ],
    }
    graph, message_ids = _build_graph(request)
    return graph, message_ids


def _node(change):
    def apply(payload):
        change(payload["nodes"][0])
    return apply


def test_normalized_graph():
    graph, ids = _graph()
    second = ids[1]
    variants = {
        "extra_key": lambda p: p.update(extra=1),
        "other_schema": lambda p: p.update(schema_version="CONVERSATION_REFINEMENT_NORMALIZED_CONVERSATION_GRAPH_V2"),
        "empty_graph_id": lambda p: p.update(graph_id=""),
        "revision_2": lambda p: p.update(revision=2),
        "empty_snapshot_ref": lambda p: p.update(snapshot_ref=""),
        "snapshot_hash_not_descriptor": lambda p: p.update(snapshot_hash="A" * 64),
        "node_extra_key": _node(lambda n: n.update(extra=1)),
        "node_empty_message_id": _node(lambda n: n.update(message_id="")),
        "node_empty_parent": _node(lambda n: n.update(parent_id="")),
        "node_role_unknown_word": _node(lambda n: n.update(role="moderator")),
        "node_role_unknown": _node(lambda n: n.update(role="unknown")),
        "node_sequence_negative": _node(lambda n: n.update(provider_sequence=-1)),
        "node_sequence_bool": _node(lambda n: n.update(provider_sequence=True)),
        "node_no_blocks": _node(lambda n: n.update(blocks=[])),
        "node_blocks_text": _node(lambda n: n.update(blocks="text")),
        "block_extra_key": _node(lambda n: n["blocks"][0].update(extra=1)),
        "block_type_unknown": _node(lambda n: n["blocks"][0].update(type="video")),
        "block_text_number": _node(lambda n: n["blocks"][0].update(text=3)),
        "block_text_none": _node(lambda n: n["blocks"][0].update(text=None)),
        "block_metadata_list": _node(lambda n: n["blocks"][0].update(metadata=[])),
        "nodes_unsorted": lambda p: p.update(nodes=list(reversed(p["nodes"]))),
        "nodes_duplicate": lambda p: p.update(nodes=[p["nodes"][0], p["nodes"][0]]),
        "cycle": _node(lambda n: n.update(parent_id=second)),
        "orphan_not_listed": _node(lambda n: n.update(parent_id="message-missing")),
        "conversation_ids_drift": lambda p: p.update(conversation_ids=["conversation:other"]),
        "edge_policy_other": lambda p: p.update(edge_policy="TIMESTAMP_INFERENCE"),
        "not_deterministic": lambda p: p.update(deterministic=False),
    }
    cases = {"as_built": _mask(outcome(validate_normalized_graph, graph))}
    for name, change in variants.items():
        cases[name] = _run(validate_normalized_graph, _reseal(graph, change))

    def orphan_listed(payload):
        payload["nodes"][0]["parent_id"] = "message-missing"
        payload["orphan_message_ids"] = [payload["nodes"][0]["message_id"]]
    cases["orphan_listed"] = _run(validate_normalized_graph, _reseal(graph, orphan_listed))
    tampered = deepcopy(graph)
    tampered["nodes"][0]["blocks"][0]["text"] = "changed afterwards"
    cases["text_changed_not_resealed"] = _run(validate_normalized_graph, tampered)
    cases["no_envelope_not_resealed"] = _run(validate_normalized_graph, {k: v for k, v in graph.items() if k != "content_hash"})
    check("conversation_refinement_graph", cases, HERE)


def test_redaction():
    texts = {
        "plain": "Nothing secret here.",
        "openai_key": "use sk-abcdefghijklmnop1234 please",
        "short_key": "sk-short",
        "bearer": "Authorization: Bearer abcdefghijklmnopqrstuvwx",
        "email": "write to someone@example.org or Other.Name+x@mail.example.co.jp",
        "cookie": "cookie=abcdef123456; session: zyxwvu987654",
        "several": "sk-abcdefghijklmnop1234 and someone@example.org and token=abcdef123456",
        "empty": "",
    }
    cases = {name: _run(redact_text, text) for name, text in texts.items()}
    cases["not_text"] = _run(redact_text, b"bytes")
    record = redact_text("someone@example.org")
    variants = {
        "extra_key": lambda p: p.update(extra=1),
        "other_schema": lambda p: p.update(schema_version="CONVERSATION_REFINEMENT_REDACTION_RESULT_V2"),
        "text_not_string": lambda p: p.update(redacted_text=None),
        "count_negative": lambda p: p.update(redaction_count=-1),
        "count_bool": lambda p: p.update(redaction_count=True),
        "categories_unsorted": lambda p: p.update(categories=["EMAIL", "BEARER_TOKEN"]),
        "categories_duplicate": lambda p: p.update(categories=["EMAIL", "EMAIL"]),
        "original_retained": lambda p: p.update(original_text_retained=True),
        "credentials_retained": lambda p: p.update(credential_cookie_storage_value_retained=True),
    }
    for name, change in variants.items():
        cases[f"validate_{name}"] = _run(validate_redaction_result, _reseal(record, change))
    tampered = dict(record, redacted_text="someone@example.org")
    cases["validate_text_changed_not_resealed"] = _run(validate_redaction_result, tampered)
    check("conversation_refinement_redaction", cases, HERE)


def test_context_capsule():
    cases = {
        "minimal": _run(make_context_capsule, capsule_id="capsule-1", required_message_refs=["m-2", "m-1", "m-1"], scope="SELECTED_CONVERSATIONS"),
        "full": _run(make_context_capsule, capsule_id="capsule-2", required_message_refs=["m-1"], scope="ALL_HISTORY_BOUNDED",
                     limitations=["B", "A"], condition_refs=["m-3"], negation_refs=["m-4"], correction_refs=["m-5"],
                     failure_refs=["m-6"], context_complete=False),
        "no_required_refs": _run(make_context_capsule, capsule_id="capsule-3", required_message_refs=[], scope="SELECTED_CONVERSATIONS"),
        "empty_ref": _run(make_context_capsule, capsule_id="capsule-4", required_message_refs=[""], scope="SELECTED_CONVERSATIONS"),
        "empty_scope": _run(make_context_capsule, capsule_id="capsule-5", required_message_refs=["m-1"], scope=""),
        "empty_id": _run(make_context_capsule, capsule_id="", required_message_refs=["m-1"], scope="SELECTED_CONVERSATIONS"),
        "complete_not_bool": _run(make_context_capsule, capsule_id="capsule-6", required_message_refs=["m-1"], scope="S", context_complete="yes"),
    }
    record = make_context_capsule(capsule_id="capsule-1", required_message_refs=["m-1", "m-2"], scope="SELECTED_CONVERSATIONS")
    variants = {
        "extra_key": lambda p: p.update(extra=1),
        "missing_key": lambda p: p.pop("failure_refs"),
        "other_schema": lambda p: p.update(schema_version="CONVERSATION_REFINEMENT_CONTEXT_CAPSULE_V2"),
        "revision_0": lambda p: p.update(revision=0),
        "revision_bool": lambda p: p.update(revision=True),
        "revision_3": lambda p: p.update(revision=3),
        "refs_unsorted": lambda p: p.update(required_message_refs=["m-2", "m-1"]),
        "refs_duplicate": lambda p: p.update(required_message_refs=["m-1", "m-1"]),
        "refs_text": lambda p: p.update(condition_refs="m-1"),
        "limitations_text": lambda p: p.update(limitations="none"),
        "limitations_unsorted": lambda p: p.update(limitations=["B", "A"]),
    }
    for name, change in variants.items():
        cases[f"validate_{name}"] = _run(validate_context_capsule, _reseal(record, change))
    cases["validate_scope_changed_not_resealed"] = _run(validate_context_capsule, dict(record, scope="ALL_HISTORY_BOUNDED"))
    check("conversation_refinement_capsule", cases, HERE)


def test_typed_refinement_items():
    graph, ids = _graph()

    def proposal(message_id, text="Cite page numbers in every answer.", kind="CONSTRAINT_OR_PREFERENCE"):
        return {"message_id": message_id, "candidate_text": text, "refinement_type": kind}

    cases = {
        "one": _run(make_model_refinement_items, graph=graph, proposals=[proposal(ids[0])], scope="SELECTED_CONVERSATIONS"),
        "two_sorted": _run(make_model_refinement_items, graph=graph, proposals=[proposal(ids[0]), proposal(ids[2], "Reach me at someone@example.org", "AI_SUGGESTION")],
                           scope="SELECTED_CONVERSATIONS"),
        "provenance_only": _run(make_model_refinement_items, graph=graph, proposals=[proposal(ids[1], kind="PROVENANCE_ONLY")], scope="ALL_HISTORY_BOUNDED"),
        "unsorted": _run(make_model_refinement_items, graph=graph, proposals=[proposal(ids[2]), proposal(ids[0])], scope="SELECTED_CONVERSATIONS"),
        "duplicate": _run(make_model_refinement_items, graph=graph, proposals=[proposal(ids[0]), proposal(ids[0])], scope="SELECTED_CONVERSATIONS"),
        "unknown_message": _run(make_model_refinement_items, graph=graph, proposals=[proposal("message-99999999")], scope="SELECTED_CONVERSATIONS"),
        "extra_field": _run(make_model_refinement_items, graph=graph, proposals=[dict(proposal(ids[0]), note="x")], scope="SELECTED_CONVERSATIONS"),
        "none": _run(make_model_refinement_items, graph=graph, proposals=[], scope="SELECTED_CONVERSATIONS"),
        "unknown_type": _run(make_model_refinement_items, graph=graph, proposals=[proposal(ids[0], kind="RUMOUR")], scope="SELECTED_CONVERSATIONS"),
        "blank_text": _run(make_model_refinement_items, graph=graph, proposals=[proposal(ids[0], text="   ")], scope="SELECTED_CONVERSATIONS"),
        "text_only_secret": _run(make_model_refinement_items, graph=graph, proposals=[proposal(ids[0], text="sk-abcdefghijklmnop1234")], scope="SELECTED_CONVERSATIONS"),
        "graph_not_resealed": _run(make_model_refinement_items, graph=dict(graph, revision=1, graph_id="graph:other"),
                                   proposals=[proposal(ids[0])], scope="SELECTED_CONVERSATIONS"),
    }
    item = make_model_refinement_items(graph=graph, proposals=[proposal(ids[0])], scope="SELECTED_CONVERSATIONS")[0]
    variants = {
        "extra_key": lambda p: p.update(extra=1),
        "other_schema": lambda p: p.update(schema_version="CONVERSATION_REFINEMENT_TYPED_REFINEMENT_ITEM_V2"),
        "empty_item_id": lambda p: p.update(item_id=""),
        "empty_candidate": lambda p: p.update(candidate_text=""),
        "revision_2": lambda p: p.update(revision=2),
        "graph_hash_text": lambda p: p.update(graph_hash="A" * 64),
        "message_refs_empty": lambda p: p.update(message_refs=[]),
        "message_refs_unsorted": lambda p: p.update(message_refs=["message-2", "message-1"]),
        "type_unknown": lambda p: p.update(refinement_type="RUMOUR"),
        "limitations_unsorted": lambda p: p.update(limitations=["B", "A"]),
        "evidence_state_other": lambda p: p.update(evidence_state="TRUE"),
        "truth_decided": lambda p: p.update(truth_decided_by_refiner=True),
        "scope_not_verified": lambda p: p.update(selected_scope_verified=False),
    }
    for name, change in variants.items():
        cases[f"validate_{name}"] = _run(validate_typed_refinement_item, _reseal(item, change))
    cases["validate_text_changed_not_resealed"] = _run(validate_typed_refinement_item, dict(item, candidate_text="changed"))
    check("conversation_refinement_items", cases, HERE)
