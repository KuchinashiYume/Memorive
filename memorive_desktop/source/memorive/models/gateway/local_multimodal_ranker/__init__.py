"""Local multimodal model as a generative listwise ranking capability."""

from memorive.models.gateway.local_multimodal_ranker.adapter import LocalMultimodalRankerAdapter, LocalMultimodalRankerOutcome
from memorive.models.gateway.local_multimodal_ranker.contracts import (
    LocalMultimodalRankerViolation,
    build_request,
    render_document_for_listwise_rank,
    validate_binding,
    validate_rankings,
)

__all__ = [
    "LocalMultimodalRankerAdapter",
    "LocalMultimodalRankerOutcome",
    "LocalMultimodalRankerViolation",
    "build_request",
    "render_document_for_listwise_rank",
    "validate_binding",
    "validate_rankings",
]
