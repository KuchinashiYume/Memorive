"""Zero-side-effect M13 envelope preview for P03/T07."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from m7_weighting.contracts import make_hashed_payload, validate_hash_descriptor, verify_hashed_payload

from .errors import ContractValidationError


_ENVELOPE_KEYS = {
    "schema_version",
    "preview_id",
    "routing_advice_hash",
    "m12_preview_hash",
    "proposed_artifact_type",
    "proposed_candidate_ids",
    "state_owner",
    "m13_is_state_owner",
    "registry_write_authorized",
    "registry_mutation_count",
    "index_write_authorized",
    "index_mutation_count",
    "artifact_materialized",
    "locator_materialized",
    "test_only",
    "content_hash",
}


def make_routing_evidence_envelope_preview(
    *, preview_id: str, routing_advice: Mapping[str, Any], m12_preview: Mapping[str, Any]
) -> dict[str, Any]:
    for name, value in (("routing_advice", routing_advice), ("m12_preview", m12_preview)):
        if not isinstance(value, Mapping) or not isinstance(value.get("content_hash"), Mapping):
            raise ContractValidationError(f"{name.upper()}_HASH_REQUIRED")
    return validate_routing_evidence_envelope_preview(
        make_hashed_payload(
            {
                "schema_version": "P03_T07_M13_ROUTING_EVIDENCE_ENVELOPE_PREVIEW_V1",
                "preview_id": preview_id,
                "routing_advice_hash": deepcopy(routing_advice["content_hash"]),
                "m12_preview_hash": deepcopy(m12_preview["content_hash"]),
                "proposed_artifact_type": "routing_advice_evidence",
                "proposed_candidate_ids": deepcopy(routing_advice.get("selected_candidate_ids", [])),
                "state_owner": "M12_OR_DESIGNATED_HUMAN",
                "m13_is_state_owner": False,
                "registry_write_authorized": False,
                "registry_mutation_count": 0,
                "index_write_authorized": False,
                "index_mutation_count": 0,
                "artifact_materialized": False,
                "locator_materialized": False,
                "test_only": True,
            }
        )
    )


def validate_routing_evidence_envelope_preview(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        preview = verify_hashed_payload(value, "routing_evidence_envelope_preview")
    except Exception as exc:
        raise ContractValidationError("M13_ROUTING_PREVIEW_HASH_INVALID") from exc
    if set(preview) != _ENVELOPE_KEYS:
        raise ContractValidationError("M13_ROUTING_PREVIEW_EXACT_KEYS_MISMATCH")
    if preview["schema_version"] != "P03_T07_M13_ROUTING_EVIDENCE_ENVELOPE_PREVIEW_V1":
        raise ContractValidationError("M13_ROUTING_PREVIEW_SCHEMA_UNSUPPORTED")
    if not isinstance(preview["preview_id"], str) or not preview["preview_id"]:
        raise ContractValidationError("M13_ROUTING_PREVIEW_ID_INVALID")
    for field in ("routing_advice_hash", "m12_preview_hash"):
        try:
            validate_hash_descriptor(preview[field], field)
        except Exception as exc:
            raise ContractValidationError(f"M13_ROUTING_PREVIEW_{field.upper()}_INVALID") from exc
    if preview["proposed_artifact_type"] != "routing_advice_evidence":
        raise ContractValidationError("M13_ROUTING_PREVIEW_ARTIFACT_TYPE_INVALID")
    candidate_ids = preview["proposed_candidate_ids"]
    if not isinstance(candidate_ids, list) or any(not isinstance(item, str) or not item for item in candidate_ids) or candidate_ids != sorted(set(candidate_ids)):
        raise ContractValidationError("M13_ROUTING_PREVIEW_CANDIDATE_IDS_INVALID")
    if preview["state_owner"] != "M12_OR_DESIGNATED_HUMAN" or preview["m13_is_state_owner"] is not False:
        raise ContractValidationError("M13_ROUTING_PREVIEW_STATE_OWNERSHIP_VIOLATION")
    for field in ("registry_write_authorized", "index_write_authorized", "artifact_materialized", "locator_materialized"):
        if preview[field] is not False:
            raise ContractValidationError("M13_ROUTING_PREVIEW_WRITE_BOUNDARY_VIOLATION")
    if any(
        isinstance(preview[field], bool) or not isinstance(preview[field], int) or preview[field] != 0
        for field in ("registry_mutation_count", "index_mutation_count")
    ):
        raise ContractValidationError("M13_ROUTING_PREVIEW_MUTATION_NONZERO")
    if preview["test_only"] is not True:
        raise ContractValidationError("M13_ROUTING_PREVIEW_NOT_TEST_ONLY")
    return preview


__all__ = [
    "make_routing_evidence_envelope_preview",
    "validate_routing_evidence_envelope_preview",
]
