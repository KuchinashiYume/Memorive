"""KNOWLEDGE_ADMISSION-owned, create-only dry-run adapter for KNOWLEDGE-FEEDBACK.

This adapter never calls ``transition()``, card I/O, a real writer, ARTIFACT_REGISTRY, or an
index.  It records the candidate state and the distinct derived-artifact state
without conflating ``approved_for_controlled_ingest`` with ``active``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from retrieval_weighting.contracts import (
    make_hashed_payload,
    validate_hash_descriptor,
    verify_hashed_payload,
)
from knowledge_feedback.contracts import validate_derived_knowledge_candidate
from knowledge_feedback.eligibility import validate_eligibility_report

from .errors import AdmissionBlocked, RealStateTransitionForbidden


SIDE_EFFECT_KEYS = {
    "card_mutations",
    "candidate_mutations",
    "artifact_creations",
    "artifact_state_transitions",
    "artifact_registry_registry_mutations",
    "index_mutations",
    "pointer_mutations",
    "production_mutations",
    "external_calls",
    "credential_reads",
    "git_mutations",
}
_RECEIPT_KEYS = {
    "schema_version",
    "receipt_id",
    "candidate_id",
    "candidate_revision",
    "candidate_hash",
    "eligibility_report_hash",
    "requested_operation",
    "dry_run_only",
    "candidate_state_before",
    "candidate_state_after",
    "derived_artifact_state_before",
    "derived_artifact_state_after",
    "state_owner_candidate",
    "state_owner_artifact",
    "artifact_registry_is_state_owner",
    "real_writer_enabled",
    "side_effect_counters",
    "execution_result",
    "test_only",
    "content_hash",
}


def validate_controlled_ingest_dry_run_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "controlled_ingest_dry_run_receipt")
    except Exception as exc:
        raise AdmissionBlocked("DRY_RUN_RECEIPT_HASH_INVALID") from exc
    if set(result) != _RECEIPT_KEYS:
        raise AdmissionBlocked("DRY_RUN_RECEIPT_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "KNOWLEDGE_FEEDBACK_CONTROLLED_INGEST_DRY_RUN_RECEIPT_V1":
        raise AdmissionBlocked("DRY_RUN_RECEIPT_SCHEMA_UNSUPPORTED")
    for field in ("receipt_id", "candidate_id", "requested_operation", "execution_result"):
        if not isinstance(result[field], str) or not result[field]:
            raise AdmissionBlocked(f"DRY_RUN_RECEIPT_{field.upper()}_INVALID")
    revision = result["candidate_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise AdmissionBlocked("DRY_RUN_RECEIPT_CANDIDATE_REVISION_INVALID")
    for field in ("candidate_hash", "eligibility_report_hash"):
        try:
            validate_hash_descriptor(result[field], field)
        except Exception as exc:
            raise AdmissionBlocked(f"DRY_RUN_RECEIPT_{field.upper()}_INVALID") from exc
    if result["requested_operation"] != "CONTROLLED_INGEST_PREVIEW":
        raise AdmissionBlocked("DRY_RUN_OPERATION_DRIFT")
    if result["dry_run_only"] is not True or result["test_only"] is not True:
        raise AdmissionBlocked("DRY_RUN_BOUNDARY_DRIFT")
    if result["state_owner_candidate"] != "M08":
        raise AdmissionBlocked("CANDIDATE_STATE_OWNER_MUST_BE_KNOWLEDGE_ADMISSION")
    if result["state_owner_artifact"] != "KNOWLEDGE_ADMISSION_CONTROLLED_ACTIVATION_FUTURE":
        raise AdmissionBlocked("ARTIFACT_STATE_OWNER_MUST_REMAIN_KNOWLEDGE_ADMISSION")
    if result["artifact_registry_is_state_owner"] is not False:
        raise AdmissionBlocked("ARTIFACT_REGISTRY_MUST_NOT_OWN_STATE")
    if result["real_writer_enabled"] is not False:
        raise RealStateTransitionForbidden("REAL_WRITER_MUST_REMAIN_DISABLED")
    if result["candidate_state_before"] != result["candidate_state_after"]:
        raise RealStateTransitionForbidden("DRY_RUN_MUTATED_CANDIDATE_STATE")
    if result["derived_artifact_state_before"] != "not_created":
        raise AdmissionBlocked("ARTIFACT_STATE_BEFORE_MUST_BE_NOT_CREATED")
    if result["derived_artifact_state_after"] != "not_created":
        raise RealStateTransitionForbidden("DRY_RUN_CREATED_OR_ACTIVATED_ARTIFACT")
    counters = result["side_effect_counters"]
    if not isinstance(counters, Mapping) or set(counters) != SIDE_EFFECT_KEYS:
        raise AdmissionBlocked("DRY_RUN_SIDE_EFFECT_COUNTERS_INVALID")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value != 0
        for value in counters.values()
    ):
        raise RealStateTransitionForbidden("DRY_RUN_SIDE_EFFECT_NONZERO")
    if result["execution_result"] != "PASS_NO_SIDE_EFFECT":
        raise AdmissionBlocked("DRY_RUN_EXECUTION_RESULT_INVALID")
    return result


def make_controlled_ingest_dry_run_receipt(
    *,
    receipt_id: str,
    candidate: Mapping[str, Any],
    eligibility_report: Mapping[str, Any],
) -> dict[str, Any]:
    checked_candidate = validate_derived_knowledge_candidate(candidate)
    checked_report = validate_eligibility_report(eligibility_report)
    common = checked_candidate["common_object"]
    if checked_report["candidate_hash"] != checked_candidate["content_hash"]:
        raise AdmissionBlocked("DRY_RUN_ELIGIBILITY_CANDIDATE_HASH_MISMATCH")
    if checked_report["candidate_id"] != common["object_id"]:
        raise AdmissionBlocked("DRY_RUN_ELIGIBILITY_CANDIDATE_ID_MISMATCH")
    if checked_report["candidate_revision"] != common["revision"]:
        raise AdmissionBlocked("DRY_RUN_ELIGIBILITY_CANDIDATE_REVISION_MISMATCH")
    if checked_report["dry_run_eligible"] is not True:
        raise AdmissionBlocked("DRY_RUN_CANDIDATE_NOT_ELIGIBLE")
    if checked_report["run_execution_authorized"] is not False:
        raise RealStateTransitionForbidden("REAL_INGEST_AUTHORIZATION_MUST_BE_FALSE")
    return validate_controlled_ingest_dry_run_receipt(
        make_hashed_payload(
            {
                "schema_version": "KNOWLEDGE_FEEDBACK_CONTROLLED_INGEST_DRY_RUN_RECEIPT_V1",
                "receipt_id": receipt_id,
                "candidate_id": common["object_id"],
                "candidate_revision": common["revision"],
                "candidate_hash": deepcopy(checked_candidate["content_hash"]),
                "eligibility_report_hash": deepcopy(checked_report["content_hash"]),
                "requested_operation": "CONTROLLED_INGEST_PREVIEW",
                "dry_run_only": True,
                "candidate_state_before": common["state"],
                "candidate_state_after": common["state"],
                "derived_artifact_state_before": "not_created",
                "derived_artifact_state_after": "not_created",
                "state_owner_candidate": "M08",
                "state_owner_artifact": "KNOWLEDGE_ADMISSION_CONTROLLED_ACTIVATION_FUTURE",
                "artifact_registry_is_state_owner": False,
                "real_writer_enabled": False,
                "side_effect_counters": {key: 0 for key in sorted(SIDE_EFFECT_KEYS)},
                "execution_result": "PASS_NO_SIDE_EFFECT",
                "test_only": True,
            }
        )
    )


__all__ = [
    "SIDE_EFFECT_KEYS",
    "make_controlled_ingest_dry_run_receipt",
    "validate_controlled_ingest_dry_run_receipt",
]
