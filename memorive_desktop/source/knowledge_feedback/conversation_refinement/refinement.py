"""Deterministic Construction-A refiner for Extensions synthetic and canary data."""

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

from .contracts import (
    REFINEMENT_TYPES,
    ConversationContractError,
    make_context_capsule,
    object_ref,
    validate_context_capsule,
)
from .graph import message_text, validate_normalized_graph
from .redaction import redact_text


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
    """Bind a model proposal to the existing typed-item authority.

    The model may propose only candidate text and one of the frozen refinement
    types. Evidence state, limitations, provenance, hashes, and the no-truth-
    decision boundary remain host-owned and are validated by the same Capabilities
    contract as deterministic refinement items.
    """

    checked_graph = validate_normalized_graph(graph)
    checked_capsule = validate_context_capsule(capsule)
    if node not in checked_graph["nodes"]:
        raise ConversationContractError("TYPED_ITEM_NODE_NOT_IN_GRAPH")
    if refinement_type not in REFINEMENT_TYPES:
        raise ConversationContractError("TYPED_ITEM_TYPE_INVALID")
    if not isinstance(candidate_text, str) or not candidate_text.strip():
        raise ConversationContractError("TYPED_ITEM_CANDIDATE_TEXT_REQUIRED")
    redaction = redact_text(candidate_text.strip())
    accepted_text = redaction["redacted_text"].strip()
    if not accepted_text:
        raise ConversationContractError("TYPED_ITEM_CANDIDATE_TEXT_REQUIRED")
    evidence_state = {
        "PROVENANCE_ONLY": "PROVENANCE_ONLY",
        "FORBIDDEN_REUSE": "FORBIDDEN",
    }.get(refinement_type, "UNVERIFIED_CLAIM")
    limitations = []
    if refinement_type in {
        "AI_SUGGESTION",
        "HYPOTHESIS",
        "REPOSITORY_VERIFIABLE_FACT_CLAIM",
    }:
        limitations.append("REQUIRES_INDEPENDENT_EVIDENCE_OR_USER_DECISION")
    if not checked_capsule["context_complete"]:
        limitations.append("CONTEXT_INSUFFICIENT")
    return validate_typed_refinement_item(
        make_hashed_payload(
            {
                "schema_version": "CONVERSATION_REFINEMENT_TYPED_REFINEMENT_ITEM_V1",
                "item_id": item_id,
                "revision": 1,
                "graph_ref": object_ref(checked_graph, id_field="graph_id"),
                "graph_hash": deepcopy(checked_graph["content_hash"]),
                "conversation_id": node["conversation_id"],
                "message_refs": [node["message_id"]],
                "context_capsule_ref": object_ref(
                    checked_capsule, id_field="capsule_id"
                ),
                "context_capsule_hash": deepcopy(checked_capsule["content_hash"]),
                "candidate_text": accepted_text,
                "candidate_text_hash": typed_payload_hash(
                    {"candidate_text": accepted_text}
                ),
                "refinement_type": refinement_type,
                "scope": scope,
                "limitations": sorted(limitations),
                "evidence_state": evidence_state,
                "ownership": ownership,
                "classification": classification,
                "redaction_hash": deepcopy(redaction["content_hash"]),
                "producer": "CONVERSATION_REFINEMENT_MODEL_ASSISTED_REFINER_V1",
                "truth_decided_by_refiner": False,
                "selected_scope_verified": True,
            }
        )
    )


def _context_capsule_for_node(
    *,
    graph: Mapping[str, Any],
    node: Mapping[str, Any],
    selected: set[str],
    scope: str,
) -> dict[str, Any]:
    parent_refs = [node["parent_id"]] if node["parent_id"] in selected else []
    lowered = message_text(node).lower()
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
    """Project bounded model proposals into formal typed refinement items."""

    checked_graph = validate_normalized_graph(graph)
    nodes_by_id = {node["message_id"]: node for node in checked_graph["nodes"]}
    accepted: list[dict[str, Any]] = []
    message_ids: list[str] = []
    for proposal in proposals:
        if not isinstance(proposal, Mapping) or set(proposal) != {
            "message_id",
            "candidate_text",
            "refinement_type",
        }:
            raise ConversationContractError("MODEL_REFINEMENT_PROPOSAL_FIELDS_INVALID")
        row = deepcopy(dict(proposal))
        message_id = row["message_id"]
        if not isinstance(message_id, str) or message_id not in nodes_by_id:
            raise ConversationContractError("MODEL_REFINEMENT_MESSAGE_REF_INVALID")
        message_ids.append(message_id)
        accepted.append(row)
    if not accepted or message_ids != sorted(set(message_ids)):
        raise ConversationContractError("MODEL_REFINEMENT_MESSAGE_REFS_INVALID")
    selected = set(message_ids)
    return [
        make_model_typed_refinement_item(
            item_id=f"item:{checked_graph['graph_id']}:{row['message_id']}",
            graph=checked_graph,
            node=nodes_by_id[row["message_id"]],
            capsule=_context_capsule_for_node(
                graph=checked_graph,
                node=nodes_by_id[row["message_id"]],
                selected=selected,
                scope=scope,
            ),
            candidate_text=row["candidate_text"],
            refinement_type=row["refinement_type"],
            scope=scope,
        )
        for row in accepted
    ]


_CONFLICT_KEYS = {
    "schema_version",
    "conflict_id",
    "revision",
    "item_refs",
    "dimension",
    "resolution",
    "resolver",
    "evidence_refs",
    "resolved",
    "history_overwritten",
    "content_hash",
}


__all__ = [
    "PREFIX_TYPES",
    "REFINEMENT_SUBJECT_SLOT",
    "make_model_refinement_items",
    "make_model_typed_refinement_item",
    "validate_typed_refinement_item",
]
