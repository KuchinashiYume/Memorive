"""Research opportunities: public API."""

from .canonical import (
    canonical_json_bytes,
    typed_payload_hash,
)
from .core import (
    CONCEPT_ORDER,
    SOURCE_PROFILE,
    normalize_text,
)
from .errors import (
    ContractRejected,
    EvidenceRejected,
    OpportunityAnalysisError,
)

__all__ = [
    "CONCEPT_ORDER",
    "SOURCE_PROFILE",
    "ContractRejected",
    "EvidenceRejected",
    "OpportunityAnalysisError",
    "canonical_json_bytes",
    "normalize_text",
    "typed_payload_hash",
]
