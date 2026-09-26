"""Strict additive contracts for the SEMANTIC-AGGREGATION semantic field successor.

The predecessor ``field_vectors`` module deliberately accepts only the ServiceContracts
fixture-hash profile.  This module is a separate successor surface for
precomputed embeddings from an explicitly authorized loopback Ollama run.  It
performs no embedding, model, network or index operation; it only verifies
immutable JSON-domain profile and overlay artifacts.

``semantic_shadow_eligible`` remains false on the preterminal profile.  The
qualification becomes effective only when
``semantic_shadow_contracts.validate_semantic_retrieval_policy_snapshot``
binds the profile and overlay to a successful terminal qualification receipt.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .canonical import canonical_json_bytes, sha256_bytes, typed_payload_hash
from .contracts import (
    require_non_empty_string,
    require_sha256,
    require_storage_token,
    validate_hash_descriptor,
    verify_hashed_payload,
)
from .errors import ContractViolation, EmbeddingProfileError, IdentityConflict


PROFILE_SCHEMA_VERSION = "1.0"
OVERLAY_SCHEMA_VERSION = "1.0"
REPRESENTATION_KIND = "precomputed_semantic_embedding"
ORIGIN_KIND = "AUTHORIZED_LOOPBACK_OLLAMA"
PROVIDER = "ollama"
MODEL_ID = "bge-m3"

_PROFILE_KEYS = {
    "object_type",
    "schema_version",
    "profile_id",
    "revision",
    "representation_kind",
    "returned_model_identity",
    "vector_origin",
    "embedding_dimension",
    "embedding_normalization",
    "similarity_metric",
    "field_registry_hash",
    "source_pack_raw_sha256",
    "rights",
    "execution_boundary",
    "semantic_shadow_eligible",
    "qualification_state",
    "eligibility_effective_only_with_terminal_receipt",
    "production_eligible",
    "hash_kind",
    "content_hash",
}
_RETURNED_IDENTITY_KEYS = {
    "identity_kind",
    "provider",
    "model_id",
    "returned_identity",
    "endpoint_scope",
    "model_manifest_raw_sha256",
    "model_blob_hashes",
}
_VECTOR_ORIGIN_KEYS = {
    "origin_kind",
    "generator_id",
    "generator_hash",
    "embedding_input_contract_hash",
    "generated_by_model",
    "hash_derived",
    "random_derived",
}
_RIGHTS_KEYS = {
    "data_classification",
    "data_ownership",
    "rights_status",
    "redistribution_allowed",
    "entrusted_data_included",
    "restricted_source_included",
}
_EXECUTION_KEYS = {
    "generation_mode",
    "provider",
    "model",
    "endpoint_scope",
    "generation_authorization_required",
    "runtime_mode",
    "runtime_model_calls_allowed",
    "runtime_network_calls_allowed",
}
_OVERLAY_KEYS = {
    "object_type",
    "schema_version",
    "overlay_id",
    "revision",
    "profile_id",
    "profile_content_hash",
    "source_pack_raw_sha256",
    "field_registry_hash",
    "embedding_dimension",
    "embedding_normalization",
    "similarity_metric",
    "record_count",
    "records",
    "rights",
    "production_eligible",
    "hash_kind",
    "content_hash",
}
_OVERLAY_RECORD_KEYS = {
    "overlay_vector_id",
    "field_id",
    "paper_id",
    "card_id",
    "field_path",
    "source_record_hash",
    "source_text_hash",
    "provenance_state",
    "embedding",
}
_SOURCE_FIELD_KEYS = {
    "field_id",
    "paper_id",
    "card_id",
    "field_path",
    "text",
    "provenance_state",
}
_SOURCE_CARD_KEYS = {
    "card_id",
    "paper_id",
    "title",
    "language",
    "active",
    "rights_eligible",
    "fields",
}


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractViolation(f"{field_name} must be an object")
    return deepcopy(dict(value))


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


def _bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ContractViolation(f"{field_name} must be a boolean")
    return value


def _positive_int(value: Any, field_name: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractViolation(f"{field_name} must be a positive integer")
    if maximum is not None and value > maximum:
        raise ContractViolation(f"{field_name} must be <= {maximum}")
    return value


def _validated_rights(value: Any, field_name: str = "rights") -> dict[str, Any]:
    rights = _mapping(value, field_name)
    _exact_keys(rights, _RIGHTS_KEYS, field_name)
    if rights["data_classification"] != "PROJECT_AUTHORED_SYNTHETIC_PUBLIC_SAFE_SELF_OWNED":
        raise ContractViolation(f"{field_name}.data_classification is not admitted")
    if rights["data_ownership"] != "self":
        raise ContractViolation(f"{field_name}.data_ownership must be self")
    if rights["rights_status"] != "CLEARED_FOR_ISOLATED_SHADOW_EVALUATION":
        raise ContractViolation(f"{field_name}.rights_status is not cleared")
    if not _bool(rights["redistribution_allowed"], f"{field_name}.redistribution_allowed"):
        raise ContractViolation(f"{field_name}.redistribution_allowed must be true")
    for name in ("entrusted_data_included", "restricted_source_included"):
        if _bool(rights[name], f"{field_name}.{name}"):
            raise ContractViolation(f"{field_name}.{name} must be false")
    return rights


def _validated_execution_boundary(value: Any) -> dict[str, Any]:
    boundary = _mapping(value, "execution_boundary")
    _exact_keys(boundary, _EXECUTION_KEYS, "execution_boundary")
    if boundary["generation_mode"] != "authorized_loopback_precompute":
        raise EmbeddingProfileError("semantic profile generation mode is not admitted")
    if boundary["provider"] != PROVIDER or boundary["model"] != MODEL_ID:
        raise EmbeddingProfileError("semantic profile provider/model identity mismatch")
    if boundary["endpoint_scope"] != "LOOPBACK_ONLY":
        raise EmbeddingProfileError("semantic profile endpoint must remain loopback-only")
    if not _bool(
        boundary["generation_authorization_required"],
        "execution_boundary.generation_authorization_required",
    ):
        raise ContractViolation("semantic generation must remain separately authorized")
    if boundary["runtime_mode"] != "precomputed_read_only":
        raise ContractViolation("semantic runtime must consume only precomputed vectors")
    for name in ("runtime_model_calls_allowed", "runtime_network_calls_allowed"):
        if _bool(boundary[name], f"execution_boundary.{name}"):
            raise ContractViolation(f"execution_boundary.{name} must remain false")
    return boundary


def validate_semantic_field_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Verify one non-production authorized-loopback semantic profile.

    The function validates a preterminal candidate and intentionally accepts
    no receipt argument or positive eligibility claim;
    only the r0.2 retrieval-policy validator can join it with the post-run
    terminal receipt.
    """

    result = verify_hashed_payload(profile, "semantic_field_profile")
    _exact_keys(result, _PROFILE_KEYS, "semantic_field_profile")
    if result["object_type"] != "SemanticFieldProfile":
        raise ContractViolation("semantic_field_profile.object_type mismatch")
    if result["schema_version"] != PROFILE_SCHEMA_VERSION:
        raise ContractViolation("semantic_field_profile.schema_version mismatch")
    require_storage_token(result["profile_id"], "semantic_field_profile.profile_id")
    _positive_int(result["revision"], "semantic_field_profile.revision")
    if result["representation_kind"] != REPRESENTATION_KIND:
        raise EmbeddingProfileError("semantic profile is not a precomputed semantic embedding")

    identity = _mapping(result["returned_model_identity"], "returned_model_identity")
    _exact_keys(identity, _RETURNED_IDENTITY_KEYS, "returned_model_identity")
    if identity["identity_kind"] != "authorized_loopback_ollama":
        raise EmbeddingProfileError("returned identity kind is not admitted")
    if identity["provider"] != PROVIDER or identity["model_id"] != MODEL_ID:
        raise EmbeddingProfileError("returned provider/model identity mismatch")
    returned_identity = require_non_empty_string(
        identity["returned_identity"], "returned_model_identity.returned_identity"
    )
    if not returned_identity.casefold().startswith(MODEL_ID):
        raise EmbeddingProfileError("returned identity does not bind bge-m3")
    if identity["endpoint_scope"] != "LOOPBACK_ONLY":
        raise EmbeddingProfileError("returned model endpoint must remain loopback-only")
    require_sha256(
        identity["model_manifest_raw_sha256"],
        "returned_model_identity.model_manifest_raw_sha256",
    )
    blob_hashes = identity["model_blob_hashes"]
    if isinstance(blob_hashes, (str, bytes)) or not isinstance(blob_hashes, Sequence) or not blob_hashes:
        raise EmbeddingProfileError("returned_model_identity.model_blob_hashes must be non-empty")
    for ordinal, digest in enumerate(blob_hashes):
        require_sha256(digest, f"returned_model_identity.model_blob_hashes[{ordinal}]")
    if list(blob_hashes) != sorted(set(blob_hashes)):
        raise EmbeddingProfileError("model blob hashes must be sorted and unique")

    origin = _mapping(result["vector_origin"], "vector_origin")
    _exact_keys(origin, _VECTOR_ORIGIN_KEYS, "vector_origin")
    if origin["origin_kind"] != ORIGIN_KIND:
        raise EmbeddingProfileError("semantic vector origin is not authorized loopback Ollama")
    require_storage_token(origin["generator_id"], "vector_origin.generator_id")
    validate_hash_descriptor(origin["generator_hash"], "vector_origin.generator_hash")
    validate_hash_descriptor(
        origin["embedding_input_contract_hash"],
        "vector_origin.embedding_input_contract_hash",
    )
    if not _bool(origin["generated_by_model"], "vector_origin.generated_by_model"):
        raise EmbeddingProfileError("semantic vectors must truthfully record model generation")
    for name in ("hash_derived", "random_derived"):
        if _bool(origin[name], f"vector_origin.{name}"):
            raise EmbeddingProfileError(f"vector_origin.{name} must be false")

    _positive_int(
        result["embedding_dimension"],
        "semantic_field_profile.embedding_dimension",
        maximum=4096,
    )
    if result["embedding_normalization"] != "l2":
        raise EmbeddingProfileError("semantic profile requires L2 normalization")
    if result["similarity_metric"] != "dot_product":
        raise EmbeddingProfileError("semantic profile requires dot_product over L2 vectors")
    validate_hash_descriptor(result["field_registry_hash"], "field_registry_hash")
    require_sha256(result["source_pack_raw_sha256"], "source_pack_raw_sha256")
    _validated_rights(result["rights"])
    _validated_execution_boundary(result["execution_boundary"])
    if _bool(result["semantic_shadow_eligible"], "semantic_shadow_eligible"):
        raise EmbeddingProfileError("preterminal semantic profile eligibility must remain false")
    if result["qualification_state"] != "PENDING_TERMINAL_RECEIPT":
        raise ContractViolation("preterminal semantic profile qualification_state mismatch")
    if not _bool(
        result["eligibility_effective_only_with_terminal_receipt"],
        "eligibility_effective_only_with_terminal_receipt",
    ):
        raise ContractViolation("semantic eligibility must remain terminal-receipt gated")
    if _bool(result["production_eligible"], "production_eligible"):
        raise ContractViolation("semantic profile must remain non-production")
    if result["hash_kind"] != "rfc8785_jcs_sha256":
        raise ContractViolation("semantic_field_profile.hash_kind mismatch")
    return result


def _source_fields(source_records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(source_records, (str, bytes)) or not isinstance(source_records, Sequence):
        raise ContractViolation("source_records must be an array")
    fields: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(source_records):
        item = _mapping(raw, f"source_records[{ordinal}]")
        if set(item) == _SOURCE_FIELD_KEYS:
            fields.append(item)
            continue
        _exact_keys(item, _SOURCE_CARD_KEYS, f"source_records[{ordinal}]")
        if not _bool(item["active"], f"source_records[{ordinal}].active"):
            raise ContractViolation("semantic source card must be active")
        if not _bool(item["rights_eligible"], f"source_records[{ordinal}].rights_eligible"):
            raise ContractViolation("semantic source card must be rights eligible")
        require_non_empty_string(item["title"], f"source_records[{ordinal}].title")
        require_non_empty_string(item["language"], f"source_records[{ordinal}].language")
        nested = item["fields"]
        if isinstance(nested, (str, bytes)) or not isinstance(nested, Sequence) or not nested:
            raise ContractViolation("semantic source card fields must be a non-empty array")
        for field_ordinal, raw_field in enumerate(nested):
            field = _mapping(raw_field, f"source_records[{ordinal}].fields[{field_ordinal}]")
            _exact_keys(field, _SOURCE_FIELD_KEYS, "semantic_source_field")
            if field["card_id"] != item["card_id"] or field["paper_id"] != item["paper_id"]:
                raise IdentityConflict("nested semantic source field differs from parent card")
            fields.append(field)
    if not fields:
        raise ContractViolation("source_records must contain at least one semantic field")

    seen: set[str] = set()
    for field in fields:
        field_id = require_storage_token(field["field_id"], "source_field.field_id")
        if field_id in seen:
            raise IdentityConflict("semantic source field_id must be unique")
        seen.add(field_id)
        for name in ("paper_id", "card_id", "field_path", "text"):
            require_non_empty_string(field[name], f"source_field.{name}")
        if field["provenance_state"] not in {"original", "derived", "unknown"}:
            raise ContractViolation("semantic source provenance_state is invalid")
    return fields


def _validated_vector(value: Any, dimension: int, field_name: str) -> list[float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise EmbeddingProfileError(f"{field_name} must be an array")
    if len(value) != dimension:
        raise EmbeddingProfileError(f"{field_name} dimension mismatch")
    result: list[float] = []
    for ordinal, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise EmbeddingProfileError(f"{field_name}[{ordinal}] must be numeric")
        number = float(item)
        if not math.isfinite(number):
            raise EmbeddingProfileError(f"{field_name}[{ordinal}] must be finite")
        result.append(number)
    norm = math.sqrt(math.fsum(number * number for number in result))
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise EmbeddingProfileError(f"{field_name} must be L2-normalized")
    return result


def semantic_overlay_vector_id(
    *,
    profile_id: str,
    profile_content_hash: Mapping[str, Any],
    field_id: str,
    source_record_hash: Mapping[str, Any],
    embedding: Sequence[float],
) -> str:
    """Derive the exact stable identity for one semantic overlay vector."""

    identity = {
        "embedding": list(embedding),
        "field_id": field_id,
        "profile_content_hash": deepcopy(dict(profile_content_hash)),
        "profile_id": profile_id,
        "source_record_hash": deepcopy(dict(source_record_hash)),
    }
    return "sfv1_" + sha256_bytes(canonical_json_bytes(identity)).lower()


def validate_semantic_field_embedding_overlay(
    overlay: Mapping[str, Any],
    *,
    profile: Mapping[str, Any],
    source_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Verify an exact semantic overlay against its profile and source fields."""

    checked_profile = validate_semantic_field_profile(profile)
    result = verify_hashed_payload(overlay, "semantic_field_embedding_overlay")
    _exact_keys(result, _OVERLAY_KEYS, "semantic_field_embedding_overlay")
    if result["object_type"] != "SemanticFieldEmbeddingOverlay":
        raise ContractViolation("semantic overlay object_type mismatch")
    if result["schema_version"] != OVERLAY_SCHEMA_VERSION:
        raise ContractViolation("semantic overlay schema_version mismatch")
    require_storage_token(result["overlay_id"], "semantic_overlay.overlay_id")
    _positive_int(result["revision"], "semantic_overlay.revision")
    if result["profile_id"] != checked_profile["profile_id"]:
        raise IdentityConflict("semantic overlay profile_id mismatch")
    if result["profile_content_hash"] != checked_profile["content_hash"]:
        raise IdentityConflict("semantic overlay profile_content_hash mismatch")
    validate_hash_descriptor(result["profile_content_hash"], "profile_content_hash")
    if result["source_pack_raw_sha256"] != checked_profile["source_pack_raw_sha256"]:
        raise IdentityConflict("semantic overlay source pack hash mismatch")
    require_sha256(result["source_pack_raw_sha256"], "source_pack_raw_sha256")
    if result["field_registry_hash"] != checked_profile["field_registry_hash"]:
        raise IdentityConflict("semantic overlay field registry hash mismatch")
    validate_hash_descriptor(result["field_registry_hash"], "field_registry_hash")
    for name in ("embedding_dimension", "embedding_normalization", "similarity_metric"):
        if result[name] != checked_profile[name]:
            raise IdentityConflict(f"semantic overlay {name} differs from profile")

    source_fields = _source_fields(source_records)
    source_by_id = {field["field_id"]: field for field in source_fields}
    records = result["records"]
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence) or not records:
        raise ContractViolation("semantic overlay records must be a non-empty array")
    if result["record_count"] != _positive_int(result["record_count"], "record_count"):
        raise ContractViolation("semantic overlay record_count is invalid")
    if result["record_count"] != len(records):
        raise ContractViolation("semantic overlay record_count mismatch")

    observed_ids: list[str] = []
    checked_records: list[dict[str, Any]] = []
    for ordinal, raw_record in enumerate(records):
        record = _mapping(raw_record, f"records[{ordinal}]")
        _exact_keys(record, _OVERLAY_RECORD_KEYS, f"records[{ordinal}]")
        field_id = require_storage_token(record["field_id"], f"records[{ordinal}].field_id")
        if field_id not in source_by_id:
            raise IdentityConflict("semantic overlay references an unknown source field")
        source = source_by_id[field_id]
        for name in ("paper_id", "card_id", "field_path", "provenance_state"):
            if record[name] != source[name]:
                raise IdentityConflict(f"semantic overlay {name} differs from source field")
        expected_record_hash = typed_payload_hash(source)
        if record["source_record_hash"] != expected_record_hash:
            raise IdentityConflict("semantic overlay source_record_hash mismatch")
        validate_hash_descriptor(record["source_record_hash"], "source_record_hash")
        expected_text_hash = typed_payload_hash({"text": source["text"]})
        if record["source_text_hash"] != expected_text_hash:
            raise IdentityConflict("semantic overlay source_text_hash mismatch")
        validate_hash_descriptor(record["source_text_hash"], "source_text_hash")
        vector = _validated_vector(
            record["embedding"],
            checked_profile["embedding_dimension"],
            f"records[{ordinal}].embedding",
        )
        expected_vector_id = semantic_overlay_vector_id(
            profile_id=checked_profile["profile_id"],
            profile_content_hash=checked_profile["content_hash"],
            field_id=field_id,
            source_record_hash=expected_record_hash,
            embedding=vector,
        )
        if record["overlay_vector_id"] != expected_vector_id:
            raise IdentityConflict("semantic overlay vector identity mismatch")
        observed_ids.append(field_id)
        checked_records.append(record)

    if observed_ids != sorted(observed_ids) or len(observed_ids) != len(set(observed_ids)):
        raise ContractViolation("semantic overlay records must be field_id-sorted and unique")
    if set(observed_ids) != set(source_by_id):
        raise ContractViolation("semantic overlay must cover the exact source field set")
    if _validated_rights(result["rights"], "semantic_overlay.rights") != checked_profile["rights"]:
        raise IdentityConflict("semantic overlay rights differ from profile")
    if _bool(result["production_eligible"], "semantic_overlay.production_eligible"):
        raise ContractViolation("semantic overlay must remain non-production")
    if result["hash_kind"] != "rfc8785_jcs_sha256":
        raise ContractViolation("semantic overlay hash_kind mismatch")
    return result


__all__ = [
    "MODEL_ID",
    "ORIGIN_KIND",
    "OVERLAY_SCHEMA_VERSION",
    "PROFILE_SCHEMA_VERSION",
    "PROVIDER",
    "REPRESENTATION_KIND",
    "semantic_overlay_vector_id",
    "validate_semantic_field_embedding_overlay",
    "validate_semantic_field_profile",
]
