"""Strict runtime contracts for the isolated FIELD-VECTORS field-vector candidate."""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from .canonical import canonical_json_bytes, sha256_bytes, typed_payload_hash
from .errors import (
    ContractViolation,
    EmbeddingProfileError,
    SourceEligibilityError,
    SourceSnapshotDriftError,
)


_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
_STORAGE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_VECTOR_ID_RE = re.compile(r"^fv1_[a-f0-9]{64}$")
_HASH_KEYS = {"algorithm", "hash_kind", "scope", "value"}
_CARD_AUTHORITY_KEYS = {"card_artifact_ref", "card_id", "revision", "content_hash"}
_PROFILE_KEYS = {
    "embedding_profile_id",
    "embedding_model_id",
    "embedding_dimension",
    "embedding_normalization",
    "index_version",
}
_INDEX_KEYS = {
    "index_id",
    "version",
    "collection_id",
    "build_cutoff_at",
    "vector_type",
    "field_registry_id",
    "field_registry_version",
    "field_registry_hash",
    "production_eligible",
    "content_hash",
}


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractViolation(f"{field_name} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], field_name: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ContractViolation(
            f"{field_name} keys must match the frozen exact set",
            context={
                "missing_keys": sorted(expected - actual),
                "unexpected_keys": sorted(actual - expected),
            },
        )


def require_non_empty_string(value: Any, field_name: str) -> str:
    """Validate an exact non-empty string without normalizing its identity bytes."""

    if not isinstance(value, str) or not value:
        raise ContractViolation(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise ContractViolation(f"{field_name} must not have edge whitespace")
    for char in value:
        if unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            raise ContractViolation(f"{field_name} contains a forbidden Unicode character")
    return value


def require_storage_token(value: Any, field_name: str) -> str:
    """Validate a path-independent token allowed by the normative storage-key grammar."""

    token = require_non_empty_string(value, field_name)
    if len(token) > 256 or not _STORAGE_TOKEN_RE.fullmatch(token):
        raise ContractViolation(f"{field_name} is not a valid storage identity token")
    return token


def require_sha256(value: Any, field_name: str) -> str:
    """Validate one uppercase 64-character SHA-256 hexadecimal digest."""

    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ContractViolation(f"{field_name} must be uppercase SHA-256 hex")
    return value


def require_vector_id(value: Any, field_name: str = "vector_id") -> str:
    """Validate the normative ``fv1_`` plus lowercase SHA-256 vector identity."""

    if not isinstance(value, str) or not _VECTOR_ID_RE.fullmatch(value):
        raise ContractViolation(f"{field_name} must match ^fv1_[a-f0-9]{{64}}$")
    return value


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


def require_non_empty_refs(value: Any, field_name: str) -> list[str]:
    """Validate, deduplicate and deterministically sort explicit lineage references."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ContractViolation(f"{field_name} must be a non-empty reference list")
    refs = [require_non_empty_string(item, f"{field_name}[]") for item in value]
    if len(set(refs)) != len(refs):
        raise ContractViolation(f"{field_name} must not contain duplicate references")
    return sorted(refs)


def validate_hash_descriptor(value: Any, field_name: str) -> dict[str, str]:
    """Validate and copy the accepted Initialization typed RFC8785 SHA-256 descriptor."""

    descriptor = deepcopy(dict(_mapping(value, field_name)))
    _exact_keys(descriptor, _HASH_KEYS, field_name)
    if descriptor["algorithm"] != "sha256":
        raise ContractViolation(f"{field_name}.algorithm must be sha256")
    if descriptor["hash_kind"] != "rfc8785_jcs_sha256":
        raise ContractViolation(f"{field_name}.hash_kind must be rfc8785_jcs_sha256")
    if descriptor["scope"] != "PAYLOAD_EXCLUDING_CONTENT_HASH":
        raise ContractViolation(
            f"{field_name}.scope must be PAYLOAD_EXCLUDING_CONTENT_HASH"
        )
    require_sha256(descriptor["value"], f"{field_name}.value")
    return descriptor


def make_stable_id(prefix: str, identity_payload: Mapping[str, Any]) -> str:
    """Derive a general stable identifier from an RFC8785 identity payload."""

    require_non_empty_string(prefix, "prefix")
    payload = deepcopy(dict(_mapping(identity_payload, "identity_payload")))
    return prefix + sha256_bytes(canonical_json_bytes(payload))


def make_vector_id(identity_payload: Mapping[str, Any]) -> str:
    """Derive the normative lowercase ``fv1_`` vector ID from exact identity fields."""

    payload = deepcopy(dict(_mapping(identity_payload, "identity_payload")))
    return "fv1_" + sha256_bytes(canonical_json_bytes(payload)).lower()


def make_value_hash(canonical_value: str) -> dict[str, str]:
    """Hash one canonical field value as a typed JCS payload."""

    require_non_empty_string(canonical_value, "canonical_value")
    return typed_payload_hash({"canonical_value": canonical_value})


def make_hashed_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a payload and add its accepted-parity typed content hash."""

    result = deepcopy(dict(_mapping(payload, "payload")))
    if "content_hash" in result:
        raise ContractViolation("payload already contains content_hash")
    result["content_hash"] = typed_payload_hash(result)
    return result


def verify_hashed_payload(
    payload: Mapping[str, Any], field_name: str = "payload"
) -> dict[str, Any]:
    """Recompute and verify a runtime object's exact typed payload hash."""

    value = deepcopy(dict(_mapping(payload, field_name)))
    if "content_hash" not in value:
        raise ContractViolation(f"{field_name}.content_hash is required")
    supplied = validate_hash_descriptor(value["content_hash"], f"{field_name}.content_hash")
    expected = typed_payload_hash(value)
    if supplied != expected:
        raise SourceSnapshotDriftError(
            f"{field_name}.content_hash does not match the RFC8785 payload",
            context={"expected": expected["value"], "observed": supplied["value"]},
        )
    return value


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


def validate_embedding_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the normative offline fixture profile without accepting provider routes."""

    result = deepcopy(dict(_mapping(profile, "profile")))
    _exact_keys(result, _PROFILE_KEYS, "profile")
    require_storage_token(result["embedding_profile_id"], "profile.embedding_profile_id")
    require_storage_token(result["embedding_model_id"], "profile.embedding_model_id")
    if result["embedding_model_id"] != "fixture-hash-vector-v2":
        raise EmbeddingProfileError("only fixture-hash-vector-v2 is allowed; regenerate older offline fixture vectors")
    dimension = result["embedding_dimension"]
    if isinstance(dimension, bool) or not isinstance(dimension, int) or not 1 <= dimension <= 65536:
        raise EmbeddingProfileError("embedding_dimension must be an integer in [1, 65536]")
    if result["embedding_normalization"] not in {"none", "l2"}:
        raise EmbeddingProfileError("embedding_normalization must be none or l2")
    version = result["index_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise EmbeddingProfileError("profile.index_version must be a positive integer")
    profile_id = result["embedding_profile_id"]
    if not (profile_id.startswith("offline:") or profile_id.startswith("local-only:")):
        raise EmbeddingProfileError("fixture profile must be offline: or local-only:")
    return result


def validate_ownership_partition(
    ownership: str,
    profile: Mapping[str, Any],
    index_id: str,
    collection_id: str,
) -> None:
    """Enforce self versus entrusted profile/index/collection separation."""

    if ownership not in {"self", "entrusted"}:
        raise SourceEligibilityError("DATA_OWNERSHIP_UNKNOWN")
    checked_profile = validate_embedding_profile(profile)
    checked_index_id = require_storage_token(index_id, "index_id")
    checked_collection = require_storage_token(collection_id, "collection_id")
    profile_local = checked_profile["embedding_profile_id"].startswith("local-only:")
    index_local = checked_index_id.startswith("local-only:")
    collection_local = checked_collection.startswith("local-only:")
    if ownership == "entrusted" and not (profile_local and index_local and collection_local):
        raise EmbeddingProfileError(
            "entrusted requires local-only: profile, index and collection"
        )
    if ownership == "self" and (profile_local or index_local or collection_local):
        raise EmbeddingProfileError("self data must not enter a local-only: partition")


def validate_index_identity(index_identity: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a typed, non-production card-field candidate index identity."""

    result = deepcopy(dict(_mapping(index_identity, "index_identity")))
    _exact_keys(result, _INDEX_KEYS, "index_identity")
    verify_hashed_payload(result, "index_identity")
    require_storage_token(result["index_id"], "index_identity.index_id")
    require_storage_token(result["collection_id"], "index_identity.collection_id")
    version = result["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractViolation("index_identity.version must be a positive integer")
    require_utc_timestamp(result["build_cutoff_at"], "index_identity.build_cutoff_at")
    if result["vector_type"] != "card_field":
        raise ContractViolation("index_identity.vector_type must be card_field")
    require_non_empty_string(result["field_registry_id"], "index_identity.field_registry_id")
    registry_version = result["field_registry_version"]
    if isinstance(registry_version, bool) or not isinstance(registry_version, int) or registry_version < 1:
        raise ContractViolation("field_registry_version must be a positive integer")
    require_sha256(result["field_registry_hash"], "index_identity.field_registry_hash")
    if result["production_eligible"] is not False:
        raise ContractViolation("candidate index must have production_eligible=false")
    return result
