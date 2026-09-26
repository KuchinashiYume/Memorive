"""Destination compatibility and zero-side-effect projection for Extensions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from knowledge_feedback.canonical import make_hashed_payload, validate_hash_descriptor, verify_hashed_payload

from .contracts import DESTINATIONS, ConversationContractError, object_ref
from .refinement import validate_typed_refinement_item


TYPE_DESTINATION = {
    "USER_DECISION_CLAIM": "project_decision_candidate",
    "CONSTRAINT_OR_PREFERENCE": "project_decision_candidate",
    "REPOSITORY_VERIFIABLE_FACT_CLAIM": "blocked_no_ingest",
    "REUSABLE_METHOD": "knowledge_feedback_derived_knowledge_candidate",
    "AI_SUGGESTION": "backlog_candidate",
    "HYPOTHESIS": "backlog_candidate",
    "OPEN_QUESTION": "backlog_candidate",
    "FAILED_ATTEMPT": "provenance_only",
    "OVERTURNED_CONCLUSION": "provenance_only",
    "PROVENANCE_ONLY": "provenance_only",
    "FORBIDDEN_REUSE": "blocked_no_ingest",
}
SIDE_EFFECT_KEYS = {
    "project_decision_writes",
    "knowledge_feedback_knowledge_writes",
    "backlog_writes",
    "knowledge_admission_state_transitions",
    "artifact_registry_registry_writes",
    "index_mutations",
    "external_calls",
    "production_mutations",
}
_KEYS = {
    "schema_version",
    "projection_id",
    "revision",
    "item_ref",
    "item_hash",
    "decision_ref",
    "decision_hash",
    "destination",
    "destination_compatible",
    "preview_only",
    "formal_ingest_authorized",
    "state_owner",
    "side_effect_counters",
    "content_hash",
}


def validate_destination_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "destination_projection")
    except Exception as exc:
        raise ConversationContractError("DESTINATION_PROJECTION_HASH_INVALID") from exc
    if set(result) != _KEYS:
        raise ConversationContractError("DESTINATION_PROJECTION_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "CONVERSATION_REFINEMENT_DESTINATION_PROJECTION_V1":
        raise ConversationContractError("DESTINATION_PROJECTION_SCHEMA_UNSUPPORTED")
    if result["destination"] not in DESTINATIONS:
        raise ConversationContractError("DESTINATION_INVALID")
    for field in ("projection_id", "item_ref", "decision_ref", "state_owner"):
        if not isinstance(result[field], str) or not result[field]:
            raise ConversationContractError(f"DESTINATION_{field.upper()}_INVALID")
    if result["revision"] != 1:
        raise ConversationContractError("DESTINATION_REVISION_INVALID")
    validate_hash_descriptor(result["item_hash"], "item_hash")
    validate_hash_descriptor(result["decision_hash"], "decision_hash")
    if result["destination_compatible"] is not True:
        raise ConversationContractError("DESTINATION_MUST_BE_COMPATIBLE")
    if result["preview_only"] is not True or result["formal_ingest_authorized"] is not False:
        raise ConversationContractError("DESTINATION_PREVIEW_BOUNDARY_INVALID")
    if result["state_owner"] != "KNOWLEDGE_ADMISSION_FUTURE_APPLY":
        raise ConversationContractError("DESTINATION_STATE_OWNER_INVALID")
    counters = result["side_effect_counters"]
    if not isinstance(counters, Mapping) or set(counters) != SIDE_EFFECT_KEYS:
        raise ConversationContractError("DESTINATION_SIDE_EFFECT_KEYS_INVALID")
    if any(isinstance(item, bool) or not isinstance(item, int) or item != 0 for item in counters.values()):
        raise ConversationContractError("DESTINATION_SIDE_EFFECT_NONZERO")
    return result


def make_destination_projection(
    *, projection_id: str, item: Mapping[str, Any], decision_receipt: Mapping[str, Any]
) -> dict[str, Any]:
    checked_item = validate_typed_refinement_item(item)
    from decision_log.conversation_refinement_decision import validate_refinement_decision_receipt

    decision = validate_refinement_decision_receipt(decision_receipt)
    if decision["item_hash"] != checked_item["content_hash"]:
        raise ConversationContractError("DESTINATION_DECISION_ITEM_HASH_MISMATCH")
    if decision["decision"] != "ACCEPT" or decision["accepted_for_projection"] is not True:
        raise ConversationContractError("DESTINATION_REQUIRES_ACCEPT")
    destination = TYPE_DESTINATION[checked_item["refinement_type"]]
    if "CONTEXT_INSUFFICIENT" in checked_item["limitations"]:
        destination = "blocked_no_ingest"
    if checked_item["refinement_type"] == "REPOSITORY_VERIFIABLE_FACT_CLAIM" and checked_item["evidence_state"] == "CORROBORATED":
        destination = "knowledge_feedback_derived_knowledge_candidate"
    return validate_destination_projection(
        make_hashed_payload(
            {
                "schema_version": "CONVERSATION_REFINEMENT_DESTINATION_PROJECTION_V1",
                "projection_id": projection_id,
                "revision": 1,
                "item_ref": object_ref(checked_item, id_field="item_id"),
                "item_hash": deepcopy(checked_item["content_hash"]),
                "decision_ref": object_ref(decision, id_field="decision_id"),
                "decision_hash": deepcopy(decision["content_hash"]),
                "destination": destination,
                "destination_compatible": True,
                "preview_only": True,
                "formal_ingest_authorized": False,
                "state_owner": "KNOWLEDGE_ADMISSION_FUTURE_APPLY",
                "side_effect_counters": {key: 0 for key in sorted(SIDE_EFFECT_KEYS)},
            }
        )
    )


__all__ = [
    "SIDE_EFFECT_KEYS",
    "TYPE_DESTINATION",
    "make_destination_projection",
    "validate_destination_projection",
]
