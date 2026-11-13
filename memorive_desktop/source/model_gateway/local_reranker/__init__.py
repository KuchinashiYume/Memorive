"""RETRIEVAL-CALIBRATION local-only reranker qualification seam."""

from .adapter import LocalRerankerOutcome
from .contracts import (
    LocalRerankerViolation,
    build_request,
    render_document_for_reranker,
    validate_compatibility_manifest,
)

__all__ = [
    "LocalRerankerOutcome",
    "LocalRerankerViolation",
    "build_request",
    "render_document_for_reranker",
    "validate_compatibility_manifest",
]
