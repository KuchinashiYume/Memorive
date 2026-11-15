# Restored from retrieval_weighting/contracts.py (removed as dead code) to build test data only.
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from retrieval_weighting.contracts import Any, ContractViolation, Mapping, SourceSnapshotDriftError, _CARD_AUTHORITY_KEYS, _exact_keys, _mapping, datetime, deepcopy, require_non_empty_refs, require_non_empty_string, timedelta, typed_payload_hash, validate_hash_descriptor


def require_utc_timestamp(value: Any, field_name: str) -> str:
    """Validate a timezone-aware ISO-8601 timestamp whose explicit offset is UTC."""

    text = require_non_empty_string(value, field_name)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ContractViolation(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ContractViolation(f"{field_name} must carry an explicit UTC offset")
    return text


def validate_card_authority(
    authority: Mapping[str, Any], card_payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate explicit Card authority and bind it to the exact Card v6 payload."""

    result = deepcopy(dict(_mapping(authority, "authority")))
    _exact_keys(result, _CARD_AUTHORITY_KEYS, "authority")
    require_non_empty_string(result["card_artifact_ref"], "authority.card_artifact_ref")
    require_non_empty_string(result["card_id"], "authority.card_id")
    revision = result["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ContractViolation("authority.revision must be a positive integer")
    supplied = validate_hash_descriptor(result["content_hash"], "authority.content_hash")
    card = deepcopy(dict(_mapping(card_payload, "card_payload")))
    expected = typed_payload_hash(card)
    if supplied != expected:
        raise SourceSnapshotDriftError(
            "authority.content_hash does not bind the supplied Card payload",
            context={"expected": expected["value"], "observed": supplied["value"]},
        )
    return result


def validate_doc_type_metadata(
    metadata: Mapping[str, Any], authority: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate one explicit DocTypeMetadata sidecar and its Card binding."""

    result = deepcopy(dict(_mapping(metadata, "doc_type_metadata")))
    required = {
        "schema_version",
        "metadata_id",
        "artifact_ref",
        "artifact_id",
        "artifact_revision",
        "artifact_content_hash",
        "source_refs",
        "card_id",
        "card_revision",
        "card_content_hash",
        "observed_value",
        "canonical_value",
        "resolution_status",
        "source_ref",
        "source_hash",
        "recorded_at",
        "observed_at",
        "normalization_contract_version",
    }
    _exact_keys(result, required, "doc_type_metadata")
    if result["schema_version"] != "1.0":
        raise ContractViolation("doc_type_metadata.schema_version must be 1.0")
    require_non_empty_string(result["metadata_id"], "doc_type_metadata.metadata_id")
    require_non_empty_string(result["artifact_ref"], "doc_type_metadata.artifact_ref")
    require_non_empty_string(result["artifact_id"], "doc_type_metadata.artifact_id")
    require_non_empty_refs(result["source_refs"], "doc_type_metadata.source_refs")
    require_non_empty_string(result["source_ref"], "doc_type_metadata.source_ref")
    if result["source_ref"] not in result["source_refs"]:
        raise ContractViolation("doc_type_metadata.source_ref must be in source_refs")
    require_utc_timestamp(result["recorded_at"], "doc_type_metadata.recorded_at")
    require_utc_timestamp(result["observed_at"], "doc_type_metadata.observed_at")
    if result["normalization_contract_version"] != 1:
        raise ContractViolation("doc_type normalization contract version must be 1")

    card_authority = _mapping(authority, "authority")
    if (
        result["artifact_ref"] != card_authority["card_artifact_ref"]
        or result["card_id"] != card_authority["card_id"]
        or result["card_revision"] != card_authority["revision"]
        or result["card_content_hash"] != card_authority["content_hash"]
    ):
        raise SourceEligibilityError("doc_type sidecar and Card authority conflict")
    if result["artifact_revision"] != card_authority["revision"]:
        raise SourceEligibilityError("doc_type artifact revision conflicts with Card revision")
    validate_hash_descriptor(
        result["artifact_content_hash"], "doc_type_metadata.artifact_content_hash"
    )
    validate_hash_descriptor(result["card_content_hash"], "doc_type_metadata.card_content_hash")
    supplied_source_hash = validate_hash_descriptor(
        result["source_hash"], "doc_type_metadata.source_hash"
    )
    expected_source_hash = typed_payload_hash(
        {
            "source_ref": result["source_ref"],
            "observed_value": result["observed_value"],
            "observed_at": result["observed_at"],
        }
    )
    if supplied_source_hash != expected_source_hash:
        raise SourceSnapshotDriftError("doc_type source_hash does not bind observed metadata")

    status = result["resolution_status"]
    if status != "resolved":
        reason = {
            "missing": "DOC_TYPE_MISSING",
            "unknown": "DOC_TYPE_UNKNOWN",
            "conflicting": "DOC_TYPE_CONFLICT",
        }.get(status, "DOC_TYPE_UNKNOWN")
        raise SourceEligibilityError(reason, context={"reason": reason})
    if result["observed_value"] not in {"literature", "note"}:
        raise SourceEligibilityError("DOC_TYPE_UNKNOWN", context={"reason": "DOC_TYPE_UNKNOWN"})
    if result["canonical_value"] != result["observed_value"]:
        raise SourceEligibilityError("DOC_TYPE_CONFLICT", context={"reason": "DOC_TYPE_CONFLICT"})
    return result
