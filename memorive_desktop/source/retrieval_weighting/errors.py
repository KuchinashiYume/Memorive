"""Fail-closed exception types for the isolated ServiceContracts candidate."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


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
