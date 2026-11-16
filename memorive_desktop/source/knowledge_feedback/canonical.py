"""Canonical hashing primitives inherited from the accepted RETRIEVAL_WEIGHTING contract."""

from memorive.platform.jcs import canonical_json_bytes, sha256_bytes, typed_payload_hash
from retrieval_weighting.contracts import (
    make_hashed_payload,
    validate_hash_descriptor,
    verify_hashed_payload,
)

__all__ = [
    "canonical_json_bytes",
    "make_hashed_payload",
    "sha256_bytes",
    "typed_payload_hash",
    "validate_hash_descriptor",
    "verify_hashed_payload",
]
