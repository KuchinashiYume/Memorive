"""M08-owned create-only T12 destination preview."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from m10_knowledge_feedback.canonical import make_hashed_payload, verify_hashed_payload
from m10_knowledge_feedback.conversation_refinement.contracts import ConversationContractError, object_ref
from m10_knowledge_feedback.conversation_refinement.destination import validate_destination_projection


SIDE_EFFECT_KEYS = {
    "candidate_writes",
    "artifact_creations",
    "state_transitions",
    "m13_registry_writes",
    "index_mutations",
    "knowledge_writes",
    "external_calls",
    "production_mutations",
}
_KEYS = {
    "schema_version",
    "preview_id",
    "revision",
    "projection_ref",
    "projection_hash",
    "destination",
    "state_before",
    "state_after",
    "state_owner",
    "m13_is_state_owner",
    "real_writer_enabled",
    "formal_ingest_authorized",
    "side_effect_counters",
    "result",
    "content_hash",
}


def validate_conversation_ingest_preview(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "conversation_ingest_preview")
    except Exception as exc:
        raise ConversationContractError("CONVERSATION_INGEST_PREVIEW_HASH_INVALID") from exc
    if set(result) != _KEYS:
        raise ConversationContractError("CONVERSATION_INGEST_PREVIEW_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "P06_T12_CONVERSATION_INGEST_PREVIEW_V1":
        raise ConversationContractError("CONVERSATION_INGEST_PREVIEW_SCHEMA_UNSUPPORTED")
    if result["revision"] != 1:
        raise ConversationContractError("CONVERSATION_INGEST_PREVIEW_REVISION_INVALID")
    if result["state_before"] != "not_created" or result["state_after"] != "not_created":
        raise ConversationContractError("CONVERSATION_INGEST_PREVIEW_STATE_MUTATION")
    if result["state_owner"] != "M08" or result["m13_is_state_owner"] is not False:
        raise ConversationContractError("CONVERSATION_INGEST_PREVIEW_STATE_OWNER_INVALID")
    if result["real_writer_enabled"] is not False or result["formal_ingest_authorized"] is not False:
        raise ConversationContractError("CONVERSATION_INGEST_PREVIEW_AUTHORITY_DRIFT")
    counters = result["side_effect_counters"]
    if not isinstance(counters, Mapping) or set(counters) != SIDE_EFFECT_KEYS or any(type(item) is not int or item != 0 for item in counters.values()):
        raise ConversationContractError("CONVERSATION_INGEST_PREVIEW_SIDE_EFFECT_NONZERO")
    if result["result"] != "PASS_NO_SIDE_EFFECT":
        raise ConversationContractError("CONVERSATION_INGEST_PREVIEW_RESULT_INVALID")
    return result


def make_conversation_ingest_preview(
    *, preview_id: str, projection: Mapping[str, Any]
) -> dict[str, Any]:
    checked = validate_destination_projection(projection)
    return validate_conversation_ingest_preview(
        make_hashed_payload(
            {
                "schema_version": "P06_T12_CONVERSATION_INGEST_PREVIEW_V1",
                "preview_id": preview_id,
                "revision": 1,
                "projection_ref": object_ref(checked, id_field="projection_id"),
                "projection_hash": deepcopy(checked["content_hash"]),
                "destination": checked["destination"],
                "state_before": "not_created",
                "state_after": "not_created",
                "state_owner": "M08",
                "m13_is_state_owner": False,
                "real_writer_enabled": False,
                "formal_ingest_authorized": False,
                "side_effect_counters": {key: 0 for key in sorted(SIDE_EFFECT_KEYS)},
                "result": "PASS_NO_SIDE_EFFECT",
            }
        )
    )


__all__ = ["SIDE_EFFECT_KEYS", "make_conversation_ingest_preview", "validate_conversation_ingest_preview"]
