"""Typed refinement items: one reviewable candidate per conversation message.

An item is made by rules (deterministic_refine_graph) or from a model's proposal
(make_model_refinement_items). Either way the refiner only proposes: the evidence state follows
from the type, and a person decides what is kept.
"""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from knowledge_feedback.canonical import (
    make_hashed_payload,
    typed_payload_hash,
    validate_hash_descriptor,
    verify_hashed_payload,
)

from memorive.sessions.refinement.contracts import (
    REFINEMENT_TYPES,
    ConversationContractError,
    make_context_capsule,
    object_ref,
    validate_context_capsule,
)
from memorive.sessions.refinement.graph import node_text, validate_normalized_graph
from memorive.sessions.refinement.redaction import redact_text
from memorive.sessions.refinement.contracts import validate_selection_receipt


PREFIX_TYPES = {
    "DECISION:": "USER_DECISION_CLAIM",
    "CONSTRAINT:": "CONSTRAINT_OR_PREFERENCE",
    "FACT:": "REPOSITORY_VERIFIABLE_FACT_CLAIM",
    "METHOD:": "REUSABLE_METHOD",
    "SUGGESTION:": "AI_SUGGESTION",
    "HYPOTHESIS:": "HYPOTHESIS",
    "QUESTION:": "OPEN_QUESTION",
    "FAILED:": "FAILED_ATTEMPT",
    "OVERTURNED:": "OVERTURNED_CONCLUSION",
    "PROVENANCE:": "PROVENANCE_ONLY",
    "FORBIDDEN:": "FORBIDDEN_REUSE",
}
REFINEMENT_SUBJECT_SLOT = "conversation_refinement"
_PROMPT_VERSION = re.compile(r"^v[1-9][0-9]*$")
_REMOTE_SUBJECT_PROVIDERS = {"anthropic", "deepseek"}
_ITEM_KEYS = {
    "schema_version",
    "item_id",
    "revision",
    "graph_ref",
    "graph_hash",
    "conversation_id",
    "message_refs",
    "context_capsule_ref",
    "context_capsule_hash",
    "candidate_text",
    "candidate_text_hash",
    "refinement_type",
    "scope",
    "limitations",
    "evidence_state",
    "ownership",
    "classification",
    "redaction_hash",
    "producer",
    "truth_decided_by_refiner",
    "selected_scope_verified",
    "content_hash",
}


def make_refinement_subject_binding(
    *,
    slot: Any,
    prompt_body: str,
    prompt_version: str,
) -> dict[str, Any]:
    """Freeze the user-replaceable MODEL_GATEWAY slot without reading its credential.

    The logical slot name is stable while provider/model/request defaults remain
    configuration.  Any configured change therefore produces a different
    content hash and invalidates an older qualification binding.
    """

    if getattr(slot, "task_type", None) != REFINEMENT_SUBJECT_SLOT:
        raise ConversationContractError("REFINEMENT_SUBJECT_SLOT_NAME_MISMATCH")
    if getattr(slot, "enabled", None) is not True:
        raise ConversationContractError("REFINEMENT_SUBJECT_SLOT_DISABLED")

    provider = getattr(slot, "provider", None)
    model_id = getattr(slot, "model_id", None)
    endpoint = getattr(slot, "endpoint", None)
    credential_env_name = getattr(slot, "api_key_env", None)
    for value, code in (
        (provider, "REFINEMENT_SUBJECT_PROVIDER_REQUIRED"),
        (model_id, "REFINEMENT_SUBJECT_MODEL_REQUIRED"),
        (endpoint, "REFINEMENT_SUBJECT_ENDPOINT_REQUIRED"),
        (credential_env_name, "REFINEMENT_SUBJECT_CREDENTIAL_ENV_NAME_REQUIRED"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ConversationContractError(code)
    if provider not in _REMOTE_SUBJECT_PROVIDERS:
        raise ConversationContractError("REFINEMENT_SUBJECT_PROVIDER_UNSUPPORTED")
    parsed_endpoint = urlsplit(endpoint)
    if parsed_endpoint.scheme != "https" or not parsed_endpoint.hostname:
        raise ConversationContractError("REFINEMENT_SUBJECT_ENDPOINT_NOT_HTTPS")

    thinking_type = getattr(slot, "thinking_type", None)
    effort = getattr(slot, "effort", None)
    max_tokens = getattr(slot, "max_tokens", None)
    temperature = getattr(slot, "temperature", None)
    timeout_seconds = getattr(slot, "timeout_seconds", None)
    if thinking_type not in {"adaptive", "disabled", "enabled", "manual"}:
        raise ConversationContractError("REFINEMENT_SUBJECT_THINKING_TYPE_INVALID")
    if effort is not None and effort not in {"low", "medium", "high", "max", "xhigh"}:
        raise ConversationContractError("REFINEMENT_SUBJECT_EFFORT_INVALID")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ConversationContractError("REFINEMENT_SUBJECT_MAX_TOKENS_INVALID")
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not math.isfinite(temperature)
        or not 0 <= float(temperature) <= 2
    ):
        raise ConversationContractError("REFINEMENT_SUBJECT_TEMPERATURE_INVALID")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds <= 0
    ):
        raise ConversationContractError("REFINEMENT_SUBJECT_TIMEOUT_INVALID")
    if not isinstance(prompt_body, str) or not prompt_body.strip():
        raise ConversationContractError("REFINEMENT_SUBJECT_PROMPT_REQUIRED")
    if not isinstance(prompt_version, str) or not _PROMPT_VERSION.fullmatch(prompt_version):
        raise ConversationContractError("REFINEMENT_SUBJECT_PROMPT_VERSION_INVALID")

    return make_hashed_payload(
        {
            "schema_version": "CONVERSATION_REFINEMENT_REFINEMENT_SUBJECT_SLOT_BINDING_V1",
            "slot_name": REFINEMENT_SUBJECT_SLOT,
            "selection_source": "MODEL_GATEWAY_USER_REPLACEABLE_SLOT",
            "user_replaceable": True,
            "enabled": True,
            "provider": provider,
            "requested_model": model_id,
            "endpoint": endpoint,
            "credential_env_name": credential_env_name,
            "credential_value_captured": False,
            "thinking_type": thinking_type,
            "reasoning_effort": effort,
            "max_tokens": max_tokens,
            "temperature": float(temperature),
            "timeout_seconds": timeout_seconds,
            "prompt_version": prompt_version,
            "prompt_hash": typed_payload_hash({"prompt_body": prompt_body}),
            "private_history_allowed": False,
            "gold_expected_allowed": False,
            "retry_ceiling": 0,
            "swap_invalidates_qualification": True,
        }
    )


def validate_typed_refinement_item(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "typed_refinement_item")
    except Exception as exc:
        raise ConversationContractError("TYPED_REFINEMENT_ITEM_HASH_INVALID") from exc
    if set(result) != _ITEM_KEYS:
        raise ConversationContractError("TYPED_REFINEMENT_ITEM_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "CONVERSATION_REFINEMENT_TYPED_REFINEMENT_ITEM_V1":
        raise ConversationContractError("TYPED_REFINEMENT_ITEM_SCHEMA_UNSUPPORTED")
    for field in ("item_id", "graph_ref", "conversation_id", "context_capsule_ref", "candidate_text", "scope", "ownership", "classification", "producer"):
        if not isinstance(result[field], str) or not result[field]:
            raise ConversationContractError(f"TYPED_ITEM_{field.upper()}_INVALID")
    if result["revision"] != 1:
        raise ConversationContractError("TYPED_ITEM_REVISION_INVALID")
    for field in ("graph_hash", "context_capsule_hash", "candidate_text_hash", "redaction_hash"):
        validate_hash_descriptor(result[field], field)
    if result["message_refs"] != sorted(set(result["message_refs"])) or not result["message_refs"]:
        raise ConversationContractError("TYPED_ITEM_MESSAGE_REFS_INVALID")
    if result["refinement_type"] not in REFINEMENT_TYPES:
        raise ConversationContractError("TYPED_ITEM_TYPE_INVALID")
    if result["limitations"] != sorted(set(result["limitations"])):
        raise ConversationContractError("TYPED_ITEM_LIMITATIONS_INVALID")
    if result["evidence_state"] not in {"UNVERIFIED_CLAIM", "CORROBORATED", "PROVENANCE_ONLY", "FORBIDDEN"}:
        raise ConversationContractError("TYPED_ITEM_EVIDENCE_STATE_INVALID")
    if result["truth_decided_by_refiner"] is not False:
        raise ConversationContractError("REFINER_MUST_NOT_DECIDE_TRUTH")
    if result["selected_scope_verified"] is not True:
        raise ConversationContractError("SELECTION_SCOPE_MUST_BE_VERIFIED")
    return result


def _classify(role: str, text: str) -> tuple[str, str]:
    stripped = text.strip()
    upper = stripped.upper()
    for prefix, type_name in PREFIX_TYPES.items():
        if upper.startswith(prefix):
            return type_name, stripped[len(prefix) :].strip()
    if role == "assistant":
        return "AI_SUGGESTION", stripped
    if role in {"system", "tool", "unknown"}:
        return "PROVENANCE_ONLY", stripped
    if stripped.endswith("?"):
        return "OPEN_QUESTION", stripped
    return "PROVENANCE_ONLY", stripped


_NEEDS_EVIDENCE = {"AI_SUGGESTION", "HYPOTHESIS", "REPOSITORY_VERIFIABLE_FACT_CLAIM"}
_RULE_BASED = "CONVERSATION_REFINEMENT_DETERMINISTIC_RULE_BASED_REFINER_V1"
_MODEL_ASSISTED = "CONVERSATION_REFINEMENT_MODEL_ASSISTED_REFINER_V1"


def _item(
    *,
    item_id: str,
    graph: Mapping[str, Any],
    node: Mapping[str, Any],
    capsule: Mapping[str, Any],
    candidate_text: str,
    refinement_type: str,
    redaction: Mapping[str, Any],
    scope: str,
    ownership: str,
    classification: str,
    producer: str,
) -> dict[str, Any]:
    """One typed item from a checked graph, node and capsule. The evidence state follows from the
    type alone: the refiner proposes, and never decides whether a claim is true."""
    for field, value in (("item_id", item_id), ("scope", scope), ("ownership", ownership), ("classification", classification)):
        if not isinstance(value, str) or not value:
            raise ConversationContractError(f"TYPED_ITEM_{field.upper()}_INVALID")
    evidence_state = {"PROVENANCE_ONLY": "PROVENANCE_ONLY", "FORBIDDEN_REUSE": "FORBIDDEN"}.get(refinement_type, "UNVERIFIED_CLAIM")
    limitations = []
    if refinement_type in _NEEDS_EVIDENCE:
        limitations.append("REQUIRES_INDEPENDENT_EVIDENCE_OR_USER_DECISION")
    if not capsule["context_complete"]:
        limitations.append("CONTEXT_INSUFFICIENT")
    return make_hashed_payload(
        {
            "schema_version": "CONVERSATION_REFINEMENT_TYPED_REFINEMENT_ITEM_V1",
            "item_id": item_id,
            "revision": 1,
            "graph_ref": object_ref(graph, id_field="graph_id"),
            "graph_hash": deepcopy(graph["content_hash"]),
            "conversation_id": node["conversation_id"],
            "message_refs": [node["message_id"]],
            "context_capsule_ref": object_ref(capsule, id_field="capsule_id"),
            "context_capsule_hash": deepcopy(capsule["content_hash"]),
            "candidate_text": candidate_text,
            "candidate_text_hash": typed_payload_hash({"candidate_text": candidate_text}),
            "refinement_type": refinement_type,
            "scope": scope,
            "limitations": sorted(limitations),
            "evidence_state": evidence_state,
            "ownership": ownership,
            "classification": classification,
            "redaction_hash": deepcopy(redaction["content_hash"]),
            "producer": producer,
            "truth_decided_by_refiner": False,
            "selected_scope_verified": True,
        }
    )


def _rule_based_item(*, item_id, graph, node, capsule, scope, ownership, classification) -> dict[str, Any]:
    """The type comes from a prefix such as DECISION: or from who wrote the message."""
    text = node_text(node)
    if not text.strip():
        raise ConversationContractError("TYPED_ITEM_TEXT_REQUIRED")
    redaction = redact_text(text)
    refinement_type, candidate_text = _classify(node["role"], redaction["redacted_text"])
    if not candidate_text:
        raise ConversationContractError("TYPED_ITEM_CANDIDATE_TEXT_REQUIRED")
    return _item(item_id=item_id, graph=graph, node=node, capsule=capsule, candidate_text=candidate_text,
                 refinement_type=refinement_type, redaction=redaction, scope=scope, ownership=ownership,
                 classification=classification, producer=_RULE_BASED)


def _model_item(*, item_id, graph, node, capsule, candidate_text, refinement_type, scope, ownership, classification) -> dict[str, Any]:
    """The model chooses only the text and one of the refinement types; the rest is set here."""
    if refinement_type not in REFINEMENT_TYPES:
        raise ConversationContractError("TYPED_ITEM_TYPE_INVALID")
    if not isinstance(candidate_text, str) or not candidate_text.strip():
        raise ConversationContractError("TYPED_ITEM_CANDIDATE_TEXT_REQUIRED")
    redaction = redact_text(candidate_text.strip())
    accepted_text = redaction["redacted_text"].strip()
    if not accepted_text:
        raise ConversationContractError("TYPED_ITEM_CANDIDATE_TEXT_REQUIRED")
    return _item(item_id=item_id, graph=graph, node=node, capsule=capsule, candidate_text=accepted_text,
                 refinement_type=refinement_type, redaction=redaction, scope=scope, ownership=ownership,
                 classification=classification, producer=_MODEL_ASSISTED)


def _checked(graph, node, capsule):
    """A caller's graph and capsule, checked once; the node must be one of the graph's."""
    checked_graph = validate_normalized_graph(graph)
    checked_capsule = validate_context_capsule(capsule)
    if node not in checked_graph["nodes"]:
        raise ConversationContractError("TYPED_ITEM_NODE_NOT_IN_GRAPH")
    return checked_graph, checked_capsule


def make_typed_refinement_item(
    *,
    item_id: str,
    graph: Mapping[str, Any],
    node: Mapping[str, Any],
    capsule: Mapping[str, Any],
    scope: str,
    ownership: str = "self",
    classification: str = "PRIVATE_USER_CONVERSATION",
) -> dict[str, Any]:
    """A rule-based item for one message of a graph."""
    checked_graph, checked_capsule = _checked(graph, node, capsule)
    return _rule_based_item(item_id=item_id, graph=checked_graph, node=node, capsule=checked_capsule,
                            scope=scope, ownership=ownership, classification=classification)


def make_model_typed_refinement_item(
    *,
    item_id: str,
    graph: Mapping[str, Any],
    node: Mapping[str, Any],
    capsule: Mapping[str, Any],
    candidate_text: str,
    refinement_type: str,
    scope: str,
    ownership: str = "self",
    classification: str = "PRIVATE_USER_CONVERSATION",
) -> dict[str, Any]:
    """An item from a model's proposal for one message of a graph. Evidence state, limitations,
    provenance and hashes are set as for rule-based items."""
    checked_graph, checked_capsule = _checked(graph, node, capsule)
    return _model_item(item_id=item_id, graph=checked_graph, node=node, capsule=checked_capsule,
                       candidate_text=candidate_text, refinement_type=refinement_type, scope=scope,
                       ownership=ownership, classification=classification)


def _context_capsule_for_node(
    *,
    graph: Mapping[str, Any],
    node: Mapping[str, Any],
    selected: set[str],
    scope: str,
) -> dict[str, Any]:
    """The context a message is read in: its parent when that is selected too, and whether its
    wording negates, corrects or reports a failure."""
    parent_refs = [node["parent_id"]] if node["parent_id"] in selected else []
    lowered = node_text(node).lower()
    return make_context_capsule(
        capsule_id=f"capsule:{graph['graph_id']}:{node['message_id']}",
        required_message_refs=sorted({node["message_id"], *parent_refs}),
        scope=scope,
        negation_refs=[node["message_id"]]
        if any(word in lowered for word in (" not ", "never", "不得", "不是"))
        else [],
        correction_refs=[node["message_id"]]
        if any(word in lowered for word in ("correct", "纠正", "改为"))
        else [],
        failure_refs=[node["message_id"]]
        if any(word in lowered for word in ("failed", "失败", "error"))
        else [],
        context_complete=node["parent_id"] is None or node["parent_id"] in selected,
    )


def make_model_refinement_items(
    *,
    graph: Mapping[str, Any],
    proposals: Sequence[Mapping[str, Any]],
    scope: str,
) -> list[dict[str, Any]]:
    """Items from a model's proposals: one per message, in message order."""
    checked_graph = validate_normalized_graph(graph)
    nodes_by_id = {node["message_id"]: node for node in checked_graph["nodes"]}
    rows: list[Mapping[str, Any]] = []
    for proposal in proposals:
        if not isinstance(proposal, Mapping) or set(proposal) != {"message_id", "candidate_text", "refinement_type"}:
            raise ConversationContractError("MODEL_REFINEMENT_PROPOSAL_FIELDS_INVALID")
        if not isinstance(proposal["message_id"], str) or proposal["message_id"] not in nodes_by_id:
            raise ConversationContractError("MODEL_REFINEMENT_MESSAGE_REF_INVALID")
        rows.append(proposal)
    message_ids = [row["message_id"] for row in rows]
    if not rows or message_ids != sorted(set(message_ids)):
        raise ConversationContractError("MODEL_REFINEMENT_MESSAGE_REFS_INVALID")
    selected = set(message_ids)
    items = []
    for row in rows:
        node = nodes_by_id[row["message_id"]]
        capsule = _context_capsule_for_node(graph=checked_graph, node=node, selected=selected, scope=scope)
        items.append(_model_item(
            item_id=f"item:{checked_graph['graph_id']}:{node['message_id']}", graph=checked_graph, node=node,
            capsule=capsule, candidate_text=row["candidate_text"], refinement_type=row["refinement_type"],
            scope=scope, ownership="self", classification="PRIVATE_USER_CONVERSATION"))
    return items


def deterministic_refine_graph(
    *,
    graph: Mapping[str, Any],
    selection_receipt: Mapping[str, Any],
    scope: str,
) -> list[dict[str, Any]]:
    """Rule-based items for the messages a selection receipt names."""
    checked_graph = validate_normalized_graph(graph)
    selection = validate_selection_receipt(selection_receipt)
    if selection["snapshot_hash"] != checked_graph["snapshot_hash"]:
        raise ConversationContractError("SELECTION_GRAPH_SNAPSHOT_HASH_MISMATCH")
    selected = set(selection["selected_message_ids"])
    nodes_by_id = {node["message_id"]: node for node in checked_graph["nodes"]}
    missing = sorted(selected - set(nodes_by_id))
    if missing:
        raise ConversationContractError(f"SELECTION_MESSAGE_MISSING:{missing}")
    items: list[dict[str, Any]] = []
    for message_id in sorted(selected):
        node = nodes_by_id[message_id]
        capsule = _context_capsule_for_node(graph=checked_graph, node=node, selected=selected, scope=scope)
        items.append(_rule_based_item(
            item_id=f"item:{checked_graph['graph_id']}:{message_id}", graph=checked_graph, node=node,
            capsule=capsule, scope=scope, ownership="self", classification="PRIVATE_USER_CONVERSATION"))
    return items


def make_conflict_set(
    *,
    conflict_id: str,
    items: Sequence[Mapping[str, Any]],
    dimension: str,
    resolution: str = "BLOCK_UNRESOLVED",
    resolver: str | None = None,
    evidence_refs: Sequence[str] = (),
) -> dict[str, Any]:
    checked = [validate_typed_refinement_item(item) for item in items]
    refs = sorted(object_ref(item, id_field="item_id") for item in checked)
    if len(refs) < 2:
        raise ConversationContractError("CONFLICT_SET_REQUIRES_TWO_ITEMS")
    allowed = {
        "KEEP_BOTH_SCOPED",
        "SELECT_SUCCESSOR",
        "SPLIT_SCOPE",
        "REQUEST_EVIDENCE",
        "PROVENANCE_ONLY",
        "BLOCK_UNRESOLVED",
    }
    if resolution not in allowed:
        raise ConversationContractError("CONFLICT_RESOLUTION_INVALID")
    resolved = resolution not in {"BLOCK_UNRESOLVED", "REQUEST_EVIDENCE"}
    if resolved and not resolver:
        raise ConversationContractError("CONFLICT_RESOLVER_REQUIRED")
    value = make_hashed_payload(
        {
            "schema_version": "CONVERSATION_REFINEMENT_CONFLICT_SET_V1",
            "conflict_id": conflict_id,
            "revision": 1,
            "item_refs": refs,
            "dimension": dimension,
            "resolution": resolution,
            "resolver": resolver,
            "evidence_refs": sorted(set(evidence_refs)),
            "resolved": resolved,
            "history_overwritten": False,
        }
    )
    return value


__all__ = [
    "PREFIX_TYPES",
    "REFINEMENT_SUBJECT_SLOT",
    "make_model_refinement_items",
    "make_model_typed_refinement_item",
    "validate_typed_refinement_item",
]

# Names this module exported before; restored with the code behind them.
__all__ = [*__all__, 'deterministic_refine_graph', 'make_conflict_set', 'make_refinement_subject_binding', 'make_typed_refinement_item']
