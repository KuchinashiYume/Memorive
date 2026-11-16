"""The errors of the field-vector tools (FieldVectorError and its subclasses); each carries a fixed
code and context that is safe to show.
"""

from __future__ import annotations

# The base error and ContractViolation live in platform/hashed_payloads.py; they are offered here
# too, where the ranking code and its callers have always imported them.
from memorive.platform.hashed_payloads import ContractViolation, FieldVectorError


class SourceEligibilityError(FieldVectorError):
    """Raised when a Card or source metadata is ineligible for a snapshot."""

    code = "BLOCKED_SOURCE_INELIGIBLE"


class SourceSnapshotDriftError(FieldVectorError):
    """Raised when supplied authority hashes do not match frozen source bytes."""

    code = "SOURCE_SNAPSHOT_DRIFT"


class IdentityConflict(FieldVectorError):
    """Raised for duplicate or contradictory field/vector identities."""

    code = "FIELD_VECTOR_IDENTITY_CONFLICT"


class EmbeddingProfileError(FieldVectorError):
    """Raised when a profile could call a model or masquerade as production."""

    code = "BLOCKED_EMBEDDING_PROFILE"
