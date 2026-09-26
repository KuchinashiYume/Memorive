"""KNOWLEDGE_FEEDBACK create-only adapter for an accepted Extensions knowledge destination."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from knowledge_feedback.canonical import make_hashed_payload

from .contracts import ConversationContractError, object_ref
from .destination import validate_destination_projection
from .refinement import validate_typed_refinement_item


def make_knowledge_feedback_candidate_preview(
    *, preview_id: str, item: Mapping[str, Any], projection: Mapping[str, Any]
) -> dict[str, Any]:
    checked_item = validate_typed_refinement_item(item)
    checked_projection = validate_destination_projection(projection)
    if checked_projection["item_hash"] != checked_item["content_hash"]:
        raise ConversationContractError("KNOWLEDGE_FEEDBACK_PREVIEW_ITEM_HASH_MISMATCH")
    if checked_projection["destination"] != "knowledge_feedback_derived_knowledge_candidate":
        raise ConversationContractError("KNOWLEDGE_FEEDBACK_PREVIEW_DESTINATION_INELIGIBLE")
    return make_hashed_payload(
        {
            "schema_version": "CONVERSATION_REFINEMENT_KNOWLEDGE_FEEDBACK_DERIVED_KNOWLEDGE_CANDIDATE_PREVIEW_V1",
            "preview_id": preview_id,
            "item_ref": object_ref(checked_item, id_field="item_id"),
            "item_hash": deepcopy(checked_item["content_hash"]),
            "projection_ref": object_ref(checked_projection, id_field="projection_id"),
            "projection_hash": deepcopy(checked_projection["content_hash"]),
            "candidate_text": checked_item["candidate_text"],
            "scope": checked_item["scope"],
            "limitations": deepcopy(checked_item["limitations"]),
            "derived": True,
            "not_literature_card": True,
            "scientific_judgment_status": "NOT_ASSESSED",
            "materialized": False,
            "formal_ingest_authorized": False,
            "side_effect_total": 0,
        }
    )


__all__ = ["make_knowledge_feedback_candidate_preview"]
