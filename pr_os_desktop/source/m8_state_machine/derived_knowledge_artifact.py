"""M08-owned dual-layer state contract for P03/T06.

Construction A can create only unmaterialized projections.  No helper in this
module calls the M08 machine, a writer, M13, an index, or a production store.
An approved candidate remains distinct from a future derived artifact, and a
pending artifact would still require a separately authorized exact activation
receipt before any real ``pending -> active`` transition.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from m7_weighting.contracts import (
    make_hashed_payload,
    validate_hash_descriptor,
    verify_hashed_payload,
)
from m10_knowledge_feedback.contracts import (
    candidate_version_ref,
    validate_derived_knowledge_candidate,
)

from .errors import AdmissionBlocked, RealStateTransitionForbidden


DERIVED_ARTIFACT_STATES = {"pending", "active", "quarantined"}
LEGAL_DERIVED_ARTIFACT_TRANSITIONS = {
    "pending": {"active", "quarantined"},
    "active": {"quarantined"},
    "quarantined": set(),
}

_ARTIFACT_KEYS = {
    "schema_version",
    "artifact_id",
    "artifact_revision",
    "candidate_ref",
    "candidate_hash",
    "source_snapshot_hashes",
    "state",
    "supersedes",
    "activation_receipt_ref",
    "state_owner",
    "m13_is_state_owner",
    "materialized",
    "creation_authorized",
    "test_only",
    "content_hash",
}
_ACTIVATION_PREVIEW_KEYS = {
    "schema_version",
    "preview_id",
    "artifact_id",
    "artifact_revision",
    "artifact_hash",
    "current_state",
    "requested_state",
    "legal_transition",
    "exact_activation_receipt_present",
    "production_activation_authorized",
    "transition_executed",
    "state_after",
    "state_owner",
    "m13_is_state_owner",
    "side_effect_counters",
    "test_only",
    "content_hash",
}
_SIDE_EFFECT_KEYS = {
    "artifact_materializations",
    "artifact_state_transitions",
    "external_calls",
    "index_mutations",
    "m13_registry_mutations",
    "pointer_mutations",
    "production_mutations",
}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AdmissionBlocked(f"{field} must be non-empty exact text")
    return value


def validate_derived_knowledge_artifact_projection(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "derived_knowledge_artifact_projection")
    except Exception as exc:
        raise AdmissionBlocked("DERIVED_ARTIFACT_PROJECTION_HASH_INVALID") from exc
    if set(result) != _ARTIFACT_KEYS:
        raise AdmissionBlocked("DERIVED_ARTIFACT_PROJECTION_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "P03_T06_DERIVED_KNOWLEDGE_ARTIFACT_PROJECTION_V1":
        raise AdmissionBlocked("DERIVED_ARTIFACT_PROJECTION_SCHEMA_UNSUPPORTED")
    _text(result["artifact_id"], "artifact_id")
    revision = result["artifact_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise AdmissionBlocked("DERIVED_ARTIFACT_REVISION_INVALID")
    _text(result["candidate_ref"], "candidate_ref")
    validate_hash_descriptor(result["candidate_hash"], "candidate_hash")
    snapshots = result["source_snapshot_hashes"]
    if not isinstance(snapshots, Mapping) or not snapshots:
        raise AdmissionBlocked("DERIVED_ARTIFACT_SOURCE_SNAPSHOTS_REQUIRED")
    for snapshot_ref, descriptor in snapshots.items():
        _text(snapshot_ref, "source_snapshot_ref")
        validate_hash_descriptor(descriptor, f"source_snapshot_hashes.{snapshot_ref}")
    if result["state"] != "pending":
        raise RealStateTransitionForbidden(
            "CONSTRUCTION_A_ARTIFACT_PROJECTION_STATE_MUST_BE_PENDING"
        )
    if revision == 1 and result["supersedes"] is not None:
        raise AdmissionBlocked("DERIVED_ARTIFACT_REVISION_ONE_SUPERSEDES_MUST_BE_NULL")
    if revision > 1:
        _text(result["supersedes"], "supersedes")
    if result["activation_receipt_ref"] is not None:
        raise AdmissionBlocked("NON_ACTIVE_ARTIFACT_CANNOT_BIND_ACTIVATION_RECEIPT")
    if result["state_owner"] != "M08" or result["m13_is_state_owner"] is not False:
        raise AdmissionBlocked("DERIVED_ARTIFACT_STATE_OWNER_MUST_REMAIN_M08")
    if result["materialized"] is not False or result["creation_authorized"] is not False:
        raise RealStateTransitionForbidden("CONSTRUCTION_A_ARTIFACT_MATERIALIZATION_FORBIDDEN")
    if result["test_only"] is not True:
        raise AdmissionBlocked("DERIVED_ARTIFACT_PROJECTION_MUST_REMAIN_TEST_ONLY")
    return result


def make_pending_artifact_projection(
    *, artifact_id: str, candidate: Mapping[str, Any]
) -> dict[str, Any]:
    checked = validate_derived_knowledge_candidate(candidate)
    if checked["common_object"]["state"] != "approved_for_controlled_ingest":
        raise AdmissionBlocked("PENDING_ARTIFACT_REQUIRES_APPROVED_CANDIDATE")
    return validate_derived_knowledge_artifact_projection(
        make_hashed_payload(
            {
                "schema_version": "P03_T06_DERIVED_KNOWLEDGE_ARTIFACT_PROJECTION_V1",
                "artifact_id": artifact_id,
                "artifact_revision": 1,
                "candidate_ref": candidate_version_ref(checked),
                "candidate_hash": deepcopy(checked["content_hash"]),
                "source_snapshot_hashes": deepcopy(checked["source_snapshot_hashes"]),
                "state": "pending",
                "supersedes": None,
                "activation_receipt_ref": None,
                "state_owner": "M08",
                "m13_is_state_owner": False,
                "materialized": False,
                "creation_authorized": False,
                "test_only": True,
            }
        )
    )


def validate_controlled_activation_preview(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "controlled_activation_preview")
    except Exception as exc:
        raise AdmissionBlocked("CONTROLLED_ACTIVATION_PREVIEW_HASH_INVALID") from exc
    if set(result) != _ACTIVATION_PREVIEW_KEYS:
        raise AdmissionBlocked("CONTROLLED_ACTIVATION_PREVIEW_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "P03_T06_CONTROLLED_ACTIVATION_PREVIEW_V1":
        raise AdmissionBlocked("CONTROLLED_ACTIVATION_PREVIEW_SCHEMA_UNSUPPORTED")
    for field in ("preview_id", "artifact_id"):
        _text(result[field], field)
    revision = result["artifact_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise AdmissionBlocked("CONTROLLED_ACTIVATION_ARTIFACT_REVISION_INVALID")
    validate_hash_descriptor(result["artifact_hash"], "artifact_hash")
    if result["current_state"] != "pending" or result["requested_state"] != "active":
        raise AdmissionBlocked("CONTROLLED_ACTIVATION_PREVIEW_STATE_DRIFT")
    if result["legal_transition"] is not True:
        raise AdmissionBlocked("CONTROLLED_ACTIVATION_PREVIEW_LEGALITY_DRIFT")
    for field in (
        "exact_activation_receipt_present",
        "production_activation_authorized",
        "transition_executed",
    ):
        if result[field] is not False:
            raise RealStateTransitionForbidden(f"{field.upper()}_MUST_REMAIN_FALSE")
    if result["state_after"] != "pending":
        raise RealStateTransitionForbidden("CONTROLLED_ACTIVATION_PREVIEW_CHANGED_STATE")
    if result["state_owner"] != "M08" or result["m13_is_state_owner"] is not False:
        raise AdmissionBlocked("CONTROLLED_ACTIVATION_STATE_OWNER_MUST_REMAIN_M08")
    counters = result["side_effect_counters"]
    if not isinstance(counters, Mapping) or set(counters) != _SIDE_EFFECT_KEYS:
        raise AdmissionBlocked("CONTROLLED_ACTIVATION_SIDE_EFFECT_COUNTERS_INVALID")
    if any(
        isinstance(counter, bool) or not isinstance(counter, int) or counter != 0
        for counter in counters.values()
    ):
        raise RealStateTransitionForbidden("CONTROLLED_ACTIVATION_SIDE_EFFECT_NONZERO")
    if result["test_only"] is not True:
        raise AdmissionBlocked("CONTROLLED_ACTIVATION_PREVIEW_MUST_REMAIN_TEST_ONLY")
    return result


def make_controlled_activation_preview(
    *, preview_id: str, artifact: Mapping[str, Any]
) -> dict[str, Any]:
    checked = validate_derived_knowledge_artifact_projection(artifact)
    if checked["state"] != "pending":
        raise AdmissionBlocked("CONTROLLED_ACTIVATION_PREVIEW_REQUIRES_PENDING")
    return validate_controlled_activation_preview(
        make_hashed_payload(
            {
                "schema_version": "P03_T06_CONTROLLED_ACTIVATION_PREVIEW_V1",
                "preview_id": preview_id,
                "artifact_id": checked["artifact_id"],
                "artifact_revision": checked["artifact_revision"],
                "artifact_hash": deepcopy(checked["content_hash"]),
                "current_state": "pending",
                "requested_state": "active",
                "legal_transition": True,
                "exact_activation_receipt_present": False,
                "production_activation_authorized": False,
                "transition_executed": False,
                "state_after": "pending",
                "state_owner": "M08",
                "m13_is_state_owner": False,
                "side_effect_counters": {key: 0 for key in sorted(_SIDE_EFFECT_KEYS)},
                "test_only": True,
            }
        )
    )


__all__ = [
    "DERIVED_ARTIFACT_STATES",
    "LEGAL_DERIVED_ARTIFACT_TRANSITIONS",
    "make_controlled_activation_preview",
    "make_pending_artifact_projection",
    "validate_controlled_activation_preview",
    "validate_derived_knowledge_artifact_projection",
]
