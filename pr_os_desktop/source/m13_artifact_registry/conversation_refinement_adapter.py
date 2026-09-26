"""M13 read-only registration/query preview for a T12 destination."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from m10_knowledge_feedback.canonical import make_hashed_payload, verify_hashed_payload
from m10_knowledge_feedback.conversation_refinement.contracts import ConversationContractError, object_ref
from m10_knowledge_feedback.conversation_refinement.destination import validate_destination_projection
from m8_state_machine.conversation_ingest_preview import validate_conversation_ingest_preview


_KEYS = {
    "schema_version",
    "preview_id",
    "revision",
    "projection_ref",
    "projection_hash",
    "ingest_preview_ref",
    "ingest_preview_hash",
    "artifact_kind",
    "state_owner",
    "m13_is_state_owner",
    "registry_mutation_count",
    "query_projection_write_count",
    "index_mutation_count",
    "production_mutation_count",
    "formal_registration_authorized",
    "content_hash",
}


def validate_conversation_registration_preview(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "conversation_registration_preview")
    except Exception as exc:
        raise ConversationContractError("CONVERSATION_REGISTRATION_PREVIEW_HASH_INVALID") from exc
    if set(result) != _KEYS:
        raise ConversationContractError("CONVERSATION_REGISTRATION_PREVIEW_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "P06_T12_CONVERSATION_REGISTRATION_PREVIEW_V1":
        raise ConversationContractError("CONVERSATION_REGISTRATION_PREVIEW_SCHEMA_UNSUPPORTED")
    if result["revision"] != 1:
        raise ConversationContractError("CONVERSATION_REGISTRATION_PREVIEW_REVISION_INVALID")
    if result["state_owner"] != "M08" or result["m13_is_state_owner"] is not False:
        raise ConversationContractError("CONVERSATION_REGISTRATION_STATE_OWNER_INVALID")
    for field in ("registry_mutation_count", "query_projection_write_count", "index_mutation_count", "production_mutation_count"):
        if type(result[field]) is not int or result[field] != 0:
            raise ConversationContractError("CONVERSATION_REGISTRATION_SIDE_EFFECT_NONZERO")
    if result["formal_registration_authorized"] is not False:
        raise ConversationContractError("FORMAL_REGISTRATION_MUST_REMAIN_UNAUTHORIZED")
    return result


def make_conversation_registration_preview(
    *,
    preview_id: str,
    projection: Mapping[str, Any],
    ingest_preview: Mapping[str, Any],
) -> dict[str, Any]:
    checked_projection = validate_destination_projection(projection)
    checked_ingest = validate_conversation_ingest_preview(ingest_preview)
    if checked_ingest["projection_hash"] != checked_projection["content_hash"]:
        raise ConversationContractError("M13_INGEST_PROJECTION_HASH_MISMATCH")
    return validate_conversation_registration_preview(
        make_hashed_payload(
            {
                "schema_version": "P06_T12_CONVERSATION_REGISTRATION_PREVIEW_V1",
                "preview_id": preview_id,
                "revision": 1,
                "projection_ref": object_ref(checked_projection, id_field="projection_id"),
                "projection_hash": deepcopy(checked_projection["content_hash"]),
                "ingest_preview_ref": object_ref(checked_ingest, id_field="preview_id"),
                "ingest_preview_hash": deepcopy(checked_ingest["content_hash"]),
                "artifact_kind": "CONVERSATION_REFINEMENT_DESTINATION_PREVIEW",
                "state_owner": "M08",
                "m13_is_state_owner": False,
                "registry_mutation_count": 0,
                "query_projection_write_count": 0,
                "index_mutation_count": 0,
                "production_mutation_count": 0,
                "formal_registration_authorized": False,
            }
        )
    )


__all__ = ["make_conversation_registration_preview", "validate_conversation_registration_preview"]
