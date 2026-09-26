"""Create-only batch, replay, and rollback receipts for Extensions previews."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from knowledge_feedback.canonical import make_hashed_payload, typed_payload_hash, verify_hashed_payload

from .contracts import ConversationContractError, object_ref
from .destination import validate_destination_projection


_BATCH_KEYS = {
    "schema_version",
    "batch_id",
    "revision",
    "projection_refs",
    "projection_hashes",
    "idempotency_key",
    "preflight_passed",
    "preview_only",
    "formal_apply_authorized",
    "side_effect_total",
    "content_hash",
}


def validate_batch_preview(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "conversation_refinement_batch")
    except Exception as exc:
        raise ConversationContractError("BATCH_HASH_INVALID") from exc
    if set(result) != _BATCH_KEYS:
        raise ConversationContractError("BATCH_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "CONVERSATION_REFINEMENT_CONVERSATION_REFINEMENT_BATCH_V1":
        raise ConversationContractError("BATCH_SCHEMA_UNSUPPORTED")
    if not isinstance(result["batch_id"], str) or not result["batch_id"]:
        raise ConversationContractError("BATCH_ID_INVALID")
    if result["revision"] != 1:
        raise ConversationContractError("BATCH_REVISION_INVALID")
    if result["projection_refs"] != sorted(set(result["projection_refs"])) or not result["projection_refs"]:
        raise ConversationContractError("BATCH_PROJECTION_REFS_INVALID")
    if sorted(result["projection_hashes"]) != result["projection_refs"]:
        raise ConversationContractError("BATCH_PROJECTION_HASH_SET_MISMATCH")
    if result["idempotency_key"] != typed_payload_hash(
        {"projection_refs": result["projection_refs"], "projection_hashes": result["projection_hashes"]}
    )["value"]:
        raise ConversationContractError("BATCH_IDEMPOTENCY_KEY_MISMATCH")
    if result["preflight_passed"] is not True or result["preview_only"] is not True:
        raise ConversationContractError("BATCH_PREFLIGHT_PREVIEW_REQUIRED")
    if result["formal_apply_authorized"] is not False or result["side_effect_total"] != 0:
        raise ConversationContractError("BATCH_SIDE_EFFECT_BOUNDARY_INVALID")
    return result


def make_batch_preview(*, batch_id: str, projections: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    checked = [validate_destination_projection(item) for item in projections]
    refs = sorted(object_ref(item, id_field="projection_id") for item in checked)
    if len(refs) != len(set(refs)) or not refs:
        raise ConversationContractError("BATCH_PROJECTION_IDENTITY_INVALID")
    hashes = {
        object_ref(item, id_field="projection_id"): deepcopy(item["content_hash"])
        for item in checked
    }
    identity = typed_payload_hash({"projection_refs": refs, "projection_hashes": hashes})["value"]
    return validate_batch_preview(
        make_hashed_payload(
            {
                "schema_version": "CONVERSATION_REFINEMENT_CONVERSATION_REFINEMENT_BATCH_V1",
                "batch_id": batch_id,
                "revision": 1,
                "projection_refs": refs,
                "projection_hashes": hashes,
                "idempotency_key": identity,
                "preflight_passed": True,
                "preview_only": True,
                "formal_apply_authorized": False,
                "side_effect_total": 0,
            }
        )
    )


def compare_replay(previous: Mapping[str, Any], current: Mapping[str, Any]) -> str:
    before = validate_batch_preview(previous)
    after = validate_batch_preview(current)
    if before["idempotency_key"] != after["idempotency_key"]:
        return "NEW_BATCH_IDENTITY"
    if before["content_hash"] == after["content_hash"]:
        return "REPLAY_NO_OP"
    return "IDENTITY_CONFLICT"


def make_rollback_receipt(*, rollback_id: str, batch: Mapping[str, Any], reason: str) -> dict[str, Any]:
    checked = validate_batch_preview(batch)
    if not isinstance(reason, str) or not reason.strip():
        raise ConversationContractError("ROLLBACK_REASON_REQUIRED")
    return make_hashed_payload(
        {
            "schema_version": "CONVERSATION_REFINEMENT_ROLLBACK_RECEIPT_V1",
            "rollback_id": rollback_id,
            "batch_ref": object_ref(checked, id_field="batch_id"),
            "batch_hash": deepcopy(checked["content_hash"]),
            "reason": reason,
            "prior_bytes_mutated": False,
            "formal_state_mutations": 0,
            "production_mutations": 0,
        }
    )


__all__ = ["compare_replay", "make_batch_preview", "make_rollback_receipt", "validate_batch_preview"]
