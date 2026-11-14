"""Fail-closed errors for the LOCAL-INFERENCE local capability broker."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class LocalCapabilityError(RuntimeError):
    """A structured error that can be projected into a public-safe receipt."""

    def __init__(
        self,
        code: str,
        *,
        plane: str,
        message: str | None = None,
        details: Mapping[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.plane = plane
        self.details = dict(details or {})
        self.retryable = retryable
        super().__init__(message or code)


class ContractViolation(LocalCapabilityError):
    def __init__(self, code: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(code, plane="OUTPUT", details=details)


class IdentityBlocked(LocalCapabilityError):
    def __init__(self, code: str = "IDENTITY_NOT_ADMITTED", *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(code, plane="IDENTITY", details=details)


class OwnershipBlocked(LocalCapabilityError):
    def __init__(self, code: str = "OWNERSHIP_FAIL_CLOSED", *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(code, plane="IDENTITY", details=details)


class ResourceBlocked(LocalCapabilityError):
    def __init__(self, code: str = "RESOURCE_ENVELOPE_EXCEEDED", *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(code, plane="OBSERVATION", details=details)


class EgressBlocked(LocalCapabilityError):
    def __init__(self, code: str = "ZERO_EGRESS_NOT_PROVEN", *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(code, plane="OBSERVATION", details=details)


class ReceiptConflict(LocalCapabilityError):
    def __init__(self, receipt_id: str) -> None:
        super().__init__(
            "IMMUTABLE_RECEIPT_CONFLICT",
            plane="OUTPUT",
            details={"receipt_id": receipt_id},
        )
