"""Append-only human decision receipts for P06/T12 refinement items."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping, Sequence

from m10_knowledge_feedback.canonical import make_hashed_payload, typed_payload_hash, validate_hash_descriptor, verify_hashed_payload
from m10_knowledge_feedback.conversation_refinement.contracts import DECISION_STATES, ConversationContractError
from m10_knowledge_feedback.conversation_refinement.refinement import validate_typed_refinement_item


_KEYS = {
    "schema_version",
    "decision_id",
    "revision",
    "item_id",
    "item_revision",
    "item_hash",
    "decision",
    "decided_by",
    "decided_at",
    "reason",
    "evidence_refs",
    "edited_text_hash",
    "supersedes",
    "accepted_for_projection",
    "real_ingest_authorized",
    "content_hash",
}


def _timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise ConversationContractError("DECIDED_AT_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConversationContractError("DECIDED_AT_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConversationContractError("DECIDED_AT_OFFSET_REQUIRED")


def validate_refinement_decision_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "refinement_decision_receipt")
    except Exception as exc:
        raise ConversationContractError("REFINEMENT_DECISION_HASH_INVALID") from exc
    if set(result) != _KEYS:
        raise ConversationContractError("REFINEMENT_DECISION_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "P06_T12_REFINEMENT_DECISION_RECEIPT_V1":
        raise ConversationContractError("REFINEMENT_DECISION_SCHEMA_UNSUPPORTED")
    for field in ("decision_id", "item_id", "decided_by", "reason"):
        if not isinstance(result[field], str) or not result[field].strip():
            raise ConversationContractError(f"REFINEMENT_DECISION_{field.upper()}_INVALID")
    if result["revision"] < 1 or isinstance(result["revision"], bool):
        raise ConversationContractError("REFINEMENT_DECISION_REVISION_INVALID")
    if result["item_revision"] < 1 or isinstance(result["item_revision"], bool):
        raise ConversationContractError("REFINEMENT_DECISION_ITEM_REVISION_INVALID")
    validate_hash_descriptor(result["item_hash"], "item_hash")
    if result["decision"] not in DECISION_STATES:
        raise ConversationContractError("REFINEMENT_DECISION_STATE_INVALID")
    _timestamp(result["decided_at"])
    if result["evidence_refs"] != sorted(set(result["evidence_refs"])):
        raise ConversationContractError("REFINEMENT_DECISION_EVIDENCE_REFS_INVALID")
    if result["decision"] == "EDIT":
        validate_hash_descriptor(result["edited_text_hash"], "edited_text_hash")
        if result["accepted_for_projection"] is not False:
            raise ConversationContractError("EDIT_MUST_NOT_AUTO_ACCEPT")
    elif result["edited_text_hash"] is not None:
        raise ConversationContractError("NON_EDIT_TEXT_HASH_FORBIDDEN")
    if result["accepted_for_projection"] is not (result["decision"] == "ACCEPT"):
        raise ConversationContractError("ACCEPTED_PROJECTION_STATE_DRIFT")
    if result["real_ingest_authorized"] is not False:
        raise ConversationContractError("REAL_INGEST_MUST_REMAIN_UNAUTHORIZED")
    if result["supersedes"] is not None and (not isinstance(result["supersedes"], str) or not result["supersedes"]):
        raise ConversationContractError("REFINEMENT_DECISION_SUPERSEDES_INVALID")
    return result


def make_refinement_decision_receipt(
    *,
    decision_id: str,
    item: Mapping[str, Any],
    decision: str,
    decided_by: str,
    decided_at: str,
    reason: str,
    evidence_refs: Sequence[str] = (),
    edited_text: str | None = None,
    supersedes: str | None = None,
    revision: int = 1,
) -> dict[str, Any]:
    checked_item = validate_typed_refinement_item(item)
    if decision == "EDIT" and (not isinstance(edited_text, str) or not edited_text.strip()):
        raise ConversationContractError("EDIT_TEXT_REQUIRED")
    if decision != "EDIT" and edited_text is not None:
        raise ConversationContractError("EDIT_TEXT_ONLY_ALLOWED_FOR_EDIT")
    return validate_refinement_decision_receipt(
        make_hashed_payload(
            {
                "schema_version": "P06_T12_REFINEMENT_DECISION_RECEIPT_V1",
                "decision_id": decision_id,
                "revision": revision,
                "item_id": checked_item["item_id"],
                "item_revision": checked_item["revision"],
                "item_hash": deepcopy(checked_item["content_hash"]),
                "decision": decision,
                "decided_by": decided_by,
                "decided_at": decided_at,
                "reason": reason,
                "evidence_refs": sorted(set(evidence_refs)),
                "edited_text_hash": typed_payload_hash({"edited_text": edited_text}) if edited_text is not None else None,
                "supersedes": supersedes,
                "accepted_for_projection": decision == "ACCEPT",
                "real_ingest_authorized": False,
            }
        )
    )


def assert_decision_binds_item(
    receipt: Mapping[str, Any], item: Mapping[str, Any]
) -> dict[str, Any]:
    checked = validate_refinement_decision_receipt(receipt)
    target = validate_typed_refinement_item(item)
    if (
        checked["item_id"] != target["item_id"]
        or checked["item_revision"] != target["revision"]
        or checked["item_hash"] != target["content_hash"]
    ):
        raise ConversationContractError("REFINEMENT_DECISION_ITEM_BINDING_MISMATCH")
    return checked


__all__ = [
    "assert_decision_binds_item",
    "make_refinement_decision_receipt",
    "validate_refinement_decision_receipt",
]

