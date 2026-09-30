"""Zero-side-effect DECISION_LOG decision preview for COST-ROUTING routing advice."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from retrieval_weighting.contracts import make_hashed_payload, validate_hash_descriptor, verify_hashed_payload


_PREVIEW_KEYS = {
    "schema_version",
    "preview_id",
    "routing_advice_hash",
    "advice_outcome",
    "proposed_candidate_ids",
    "decision_status",
    "human_decision_required",
    "decision_write_authorized",
    "decision_log_mutation_count",
    "production_route_write_authorized",
    "production_route_mutation_count",
    "test_only",
    "content_hash",
}


class RoutingDecisionPreviewError(ValueError):
    """An DECISION_LOG preview is malformed or crosses the no-write boundary."""


def make_routing_decision_preview(*, preview_id: str, routing_advice: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(routing_advice, Mapping) or not isinstance(routing_advice.get("content_hash"), Mapping):
        raise RoutingDecisionPreviewError("ROUTING_ADVICE_HASH_REQUIRED")
    payload = {
        "schema_version": "COST_ROUTING_DECISION_LOG_ROUTING_DECISION_PREVIEW_V1",
        "preview_id": preview_id,
        "routing_advice_hash": deepcopy(routing_advice["content_hash"]),
        "advice_outcome": routing_advice.get("outcome"),
        "proposed_candidate_ids": deepcopy(routing_advice.get("selected_candidate_ids", [])),
        "decision_status": "PREVIEW_NOT_DECIDED",
        "human_decision_required": True,
        "decision_write_authorized": False,
        "decision_log_mutation_count": 0,
        "production_route_write_authorized": False,
        "production_route_mutation_count": 0,
        "test_only": True,
    }
    return validate_routing_decision_preview(make_hashed_payload(payload))


def validate_routing_decision_preview(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        preview = verify_hashed_payload(value, "routing_decision_preview")
    except Exception as exc:
        raise RoutingDecisionPreviewError("DECISION_LOG_ROUTING_PREVIEW_HASH_INVALID") from exc
    if set(preview) != _PREVIEW_KEYS:
        raise RoutingDecisionPreviewError("DECISION_LOG_ROUTING_PREVIEW_EXACT_KEYS_MISMATCH")
    if preview["schema_version"] != "COST_ROUTING_DECISION_LOG_ROUTING_DECISION_PREVIEW_V1":
        raise RoutingDecisionPreviewError("DECISION_LOG_ROUTING_PREVIEW_SCHEMA_UNSUPPORTED")
    if not isinstance(preview["preview_id"], str) or not preview["preview_id"]:
        raise RoutingDecisionPreviewError("DECISION_LOG_ROUTING_PREVIEW_ID_INVALID")
    try:
        validate_hash_descriptor(preview["routing_advice_hash"], "routing_advice_hash")
    except Exception as exc:
        raise RoutingDecisionPreviewError("DECISION_LOG_ROUTING_PREVIEW_ADVICE_HASH_INVALID") from exc
    if not isinstance(preview["advice_outcome"], str) or not preview["advice_outcome"]:
        raise RoutingDecisionPreviewError("DECISION_LOG_ROUTING_PREVIEW_OUTCOME_INVALID")
    candidate_ids = preview["proposed_candidate_ids"]
    if not isinstance(candidate_ids, list) or any(not isinstance(item, str) or not item for item in candidate_ids) or candidate_ids != sorted(set(candidate_ids)):
        raise RoutingDecisionPreviewError("DECISION_LOG_ROUTING_PREVIEW_CANDIDATE_IDS_INVALID")
    if preview["decision_status"] != "PREVIEW_NOT_DECIDED" or preview["human_decision_required"] is not True:
        raise RoutingDecisionPreviewError("DECISION_LOG_ROUTING_PREVIEW_DECISION_BOUNDARY_INVALID")
    if preview["decision_write_authorized"] is not False or preview["production_route_write_authorized"] is not False:
        raise RoutingDecisionPreviewError("DECISION_LOG_ROUTING_PREVIEW_WRITE_AUTHORIZED")
    if any(
        isinstance(preview[field], bool) or not isinstance(preview[field], int) or preview[field] != 0
        for field in ("decision_log_mutation_count", "production_route_mutation_count")
    ):
        raise RoutingDecisionPreviewError("DECISION_LOG_ROUTING_PREVIEW_MUTATION_NONZERO")
    if preview["test_only"] is not True:
        raise RoutingDecisionPreviewError("DECISION_LOG_ROUTING_PREVIEW_NOT_TEST_ONLY")
    return preview


__all__ = [
    "RoutingDecisionPreviewError",
    "make_routing_decision_preview",
    "validate_routing_decision_preview",
]

