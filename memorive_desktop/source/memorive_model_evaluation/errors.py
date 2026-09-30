"""Errors raised by the deterministic MODEL_EVALUATION quality-sentinel slice."""

from __future__ import annotations


class ModelEvaluationError(Exception):
    """Base error for the MODEL_EVALUATION quality sentinel."""


class ModelEvaluationSchemaError(ModelEvaluationError, ValueError):
    """Raised when a request or report violates the frozen public contract."""


class ModelEvaluationPolicyError(ModelEvaluationError, ValueError):
    """Raised when a referenced policy is unknown or internally inconsistent."""


class ModelEvaluationAdapterError(ModelEvaluationError):
    """Raised when the ARTIFACT_REGISTRY v1 boundary cannot be constructed."""


class ModelEvaluationAnalysisExamError(ModelEvaluationError, ValueError):
    """Raised when an analysis exam audit/repair contract cannot be closed."""


class ModelEvaluationCardReviewerExamError(ModelEvaluationError, ValueError):
    """Raised when a card-reviewer exam contract cannot be closed."""


class ModelEvaluationReferenceExamPackError(ModelEvaluationError, ValueError):
    """Raised when a formal reference exam pack is absent or not trustworthy."""
