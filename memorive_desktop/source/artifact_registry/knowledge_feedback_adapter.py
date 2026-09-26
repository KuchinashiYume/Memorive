"""ARTIFACT_REGISTRY dry-run preview for Verification; Registry and state mutation are impossible."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from retrieval_weighting.contracts import (
    make_hashed_payload,
    validate_hash_descriptor,
    verify_hashed_payload,
)
from knowledge_admission.knowledge_submission import validate_controlled_ingest_dry_run_receipt
from knowledge_feedback.contracts import validate_derived_knowledge_candidate

from .errors import ContractValidationError


_PREVIEW_KEYS = {
    "schema_version",
    "preview_id",
    "candidate_id",
    "candidate_revision",
    "candidate_hash",
    "dry_run_receipt_hash",
    "proposed_artifact_type",
    "proposed_parent_refs",
    "proposed_state_observation",
    "state_owner",
    "artifact_registry_is_state_owner",
    "registry_write_authorized",
    "registry_mutation_count",
    "index_write_authorized",
    "index_mutation_count",
    "locator_materialized",
    "artifact_materialized",
    "test_only",
    "content_hash",
}


def validate_knowledge_registration_preview(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "knowledge_registration_preview")
    except Exception as exc:
        raise ContractValidationError("KNOWLEDGE_PREVIEW_HASH_INVALID") from exc
    if set(result) != _PREVIEW_KEYS:
        raise ContractValidationError("KNOWLEDGE_PREVIEW_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "KNOWLEDGE_FEEDBACK_ARTIFACT_REGISTRY_KNOWLEDGE_REGISTRATION_PREVIEW_V1":
        raise ContractValidationError("KNOWLEDGE_PREVIEW_SCHEMA_UNSUPPORTED")
    for field in ("preview_id", "candidate_id"):
        if not isinstance(result[field], str) or not result[field] or result[field] != result[field].strip():
            raise ContractValidationError(f"KNOWLEDGE_PREVIEW_{field.upper()}_INVALID")
    revision = result["candidate_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ContractValidationError("KNOWLEDGE_PREVIEW_CANDIDATE_REVISION_INVALID")
    for field in ("candidate_hash", "dry_run_receipt_hash"):
        try:
            validate_hash_descriptor(result[field], field)
        except Exception as exc:
            raise ContractValidationError(
                f"KNOWLEDGE_PREVIEW_{field.upper()}_INVALID"
            ) from exc
    if result["proposed_artifact_type"] != "derived_knowledge_artifact":
        raise ContractValidationError("KNOWLEDGE_PREVIEW_ARTIFACT_TYPE_DRIFT")
    refs = result["proposed_parent_refs"]
    if (
        not isinstance(refs, list)
        or not refs
        or any(not isinstance(ref, str) or not ref or ref != ref.strip() for ref in refs)
        or refs != sorted(set(refs))
    ):
        raise ContractValidationError("KNOWLEDGE_PREVIEW_PARENT_REFS_INVALID")
    if result["proposed_state_observation"] != "not_created":
        raise ContractValidationError("KNOWLEDGE_PREVIEW_STATE_MUST_BE_NOT_CREATED")
    if result["state_owner"] != "M08" or result["artifact_registry_is_state_owner"] is not False:
        raise ContractValidationError("ARTIFACT_REGISTRY_STATE_OWNERSHIP_VIOLATION")
    expected_false = (
        "registry_write_authorized",
        "index_write_authorized",
        "locator_materialized",
        "artifact_materialized",
    )
    if any(result[field] is not False for field in expected_false):
        raise ContractValidationError("KNOWLEDGE_PREVIEW_WRITE_BOUNDARY_VIOLATION")
    if any(
        isinstance(result[field], bool)
        or not isinstance(result[field], int)
        or result[field] != 0
        for field in ("registry_mutation_count", "index_mutation_count")
    ):
        raise ContractValidationError("KNOWLEDGE_PREVIEW_MUTATION_NONZERO")
    if result["test_only"] is not True:
        raise ContractValidationError("KNOWLEDGE_PREVIEW_MUST_REMAIN_TEST_ONLY")
    return result


def make_knowledge_registration_preview(
    *,
    preview_id: str,
    candidate: Mapping[str, Any],
    dry_run_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    checked_candidate = validate_derived_knowledge_candidate(candidate)
    checked_dry_run = validate_controlled_ingest_dry_run_receipt(dry_run_receipt)
    common = checked_candidate["common_object"]
    if checked_dry_run["candidate_hash"] != checked_candidate["content_hash"]:
        raise ContractValidationError("KNOWLEDGE_PREVIEW_CANDIDATE_HASH_MISMATCH")
    if checked_dry_run["candidate_id"] != common["object_id"]:
        raise ContractValidationError("KNOWLEDGE_PREVIEW_CANDIDATE_ID_MISMATCH")
    if checked_dry_run["candidate_revision"] != common["revision"]:
        raise ContractValidationError("KNOWLEDGE_PREVIEW_CANDIDATE_REVISION_MISMATCH")
    return validate_knowledge_registration_preview(
        make_hashed_payload(
            {
                "schema_version": "KNOWLEDGE_FEEDBACK_ARTIFACT_REGISTRY_KNOWLEDGE_REGISTRATION_PREVIEW_V1",
                "preview_id": preview_id,
                "candidate_id": common["object_id"],
                "candidate_revision": common["revision"],
                "candidate_hash": deepcopy(checked_candidate["content_hash"]),
                "dry_run_receipt_hash": deepcopy(checked_dry_run["content_hash"]),
                "proposed_artifact_type": "derived_knowledge_artifact",
                "proposed_parent_refs": deepcopy(common["parent_refs"]),
                "proposed_state_observation": "not_created",
                "state_owner": "M08",
                "artifact_registry_is_state_owner": False,
                "registry_write_authorized": False,
                "registry_mutation_count": 0,
                "index_write_authorized": False,
                "index_mutation_count": 0,
                "locator_materialized": False,
                "artifact_materialized": False,
                "test_only": True,
            }
        )
    )


__all__ = [
    "make_knowledge_registration_preview",
    "validate_knowledge_registration_preview",
]
