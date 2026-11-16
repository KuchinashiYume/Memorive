"""Local reranker models for retrieval calibration: requests, result checks and the doctor
(reranker_doctor, used by memorive/research/retrieval/retrieval_calibration/cli.py).
"""

from memorive.models.gateway.local_reranker.adapter import LocalRerankerAdapter, LocalRerankerOutcome
from memorive.models.gateway.local_reranker.contracts import (
    LocalRerankerViolation,
    build_request,
    render_document_for_reranker,
    validate_compatibility_manifest,
)

__all__ = [
    "LocalRerankerAdapter",
    "LocalRerankerOutcome",
    "LocalRerankerViolation",
    "build_request",
    "render_document_for_reranker",
    "validate_compatibility_manifest",
]
