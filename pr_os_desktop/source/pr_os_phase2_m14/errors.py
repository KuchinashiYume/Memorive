"""Errors raised by the deterministic M14 quality-sentinel slice."""

from __future__ import annotations


class M14Error(Exception):
    """Base error for the M14 quality sentinel."""


class M14SchemaError(M14Error, ValueError):
    """Raised when a request or report violates the frozen public contract."""


class M14PolicyError(M14Error, ValueError):
    """Raised when a referenced policy is unknown or internally inconsistent."""


class M14AdapterError(M14Error):
    """Raised when the M13 v1 boundary cannot be constructed."""


class M14AnalysisExamError(M14Error, ValueError):
    """Raised when an analysis exam audit/repair contract cannot be closed."""


class M14CardReviewerExamError(M14Error, ValueError):
    """Raised when a card-reviewer exam contract cannot be closed."""


class M14ReferenceExamPackError(M14Error, ValueError):
    """Raised when a formal reference exam pack is absent or not trustworthy."""
