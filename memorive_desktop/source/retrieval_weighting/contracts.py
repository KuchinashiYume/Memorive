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


def require_sha256(value: Any, field_name: str) -> str:
    """Validate one uppercase 64-character SHA-256 hexadecimal digest."""

    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ContractViolation(f"{field_name} must be uppercase SHA-256 hex")
    return value


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
    """Copy a runtime object and check that it carries a well-formed content hash.

    The hash is not compared with the content: the object is the app\'s own data,
    and a value corrected by hand must stay readable. make_hashed_payload writes a
    correct hash whenever an object is made.
    """

    value = deepcopy(dict(_mapping(payload, field_name)))
    if "content_hash" not in value:
        raise ContractViolation(f"{field_name}.content_hash is required")
    validate_hash_descriptor(value["content_hash"], f"{field_name}.content_hash")
    return value
