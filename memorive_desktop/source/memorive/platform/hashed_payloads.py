"""Payloads that carry their own content hash, and the checks they use.

make_hashed_payload copies a payload and adds content_hash, the typed RFC 8785 SHA-256 descriptor
that platform/jcs.py's typed_payload_hash computes; verify_hashed_payload checks that a payload
carries a well-formed one (it does not recompute it); validate_hash_descriptor checks a descriptor's
exact keys and values; make_stable_id derives an id from an identity payload. The ranking package,
knowledge feedback, session refinement, the document registry and card states, the exam scoring
and the models' cost routing and routing advice use them.

FieldVectorError and ContractViolation keep the names the field-vector code wrote into its receipts;
this ContractViolation is not platform.errors.ContractViolation. The ranking package's other errors
subclass this FieldVectorError, and research/ranking/errors.py and contracts.py import these names
back, so their callers and tests see the same classes.
"""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from typing import Any, Mapping

from memorive.platform.jcs import canonical_json_bytes, sha256_bytes, typed_payload_hash


_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
_HASH_KEYS = {"algorithm", "hash_kind", "scope", "value"}


class FieldVectorError(RuntimeError):
    """Base error carrying a stable machine code and public-safe context."""

    code = "FIELD_VECTOR_ERROR"

    def __init__(self, message: str, *, context: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.context = deepcopy(dict(context or {}))

    def to_issue(self) -> dict[str, Any]:
        """Return a serializable issue without a traceback or source payload."""

        return {
            "code": self.code,
            "message": str(self),
            "context": deepcopy(self.context),
        }


class ContractViolation(FieldVectorError):
    """Raised when an input violates a frozen candidate contract."""

    code = "BLOCKED_FIELD_CONTRACT_CONFLICT"


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
