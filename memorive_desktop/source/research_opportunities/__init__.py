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

# Names the package offered before; restored with the code behind them.
from .canonical import content_hash_matches
from .canonical import external_hash_is_valid
from .canonical import with_content_hash
from .core import assess_comparability
from .core import build_opportunity_candidate
from .core import classify_tension
from .core import evidence_set_hash
from .core import extract_author_stated_gaps
from .core import finite_number_or_none
from .core import make_ignore_decision
from .core import make_anchor_resolution_receipt
from .core import make_pair_ledger
from .core import make_source_snapshot
from .core import make_trigger_scope_receipt
from .core import project_comparison_concepts
from .core import validate_ignore_decision
from .core import validate_anchor_resolution_receipt
from .core import validate_opportunity_candidate
from .core import validate_source_card
from .core import validate_source_snapshot
from .core import validate_trigger_scope_receipt
from .errors import CandidateRejected
from .errors import SourceRejected
__all__ = [*__all__, 'CandidateRejected', 'SourceRejected', 'assess_comparability', 'build_opportunity_candidate', 'classify_tension', 'content_hash_matches', 'evidence_set_hash', 'external_hash_is_valid', 'extract_author_stated_gaps', 'finite_number_or_none', 'make_ignore_decision', 'make_anchor_resolution_receipt', 'make_pair_ledger', 'make_source_snapshot', 'make_trigger_scope_receipt', 'project_comparison_concepts', 'validate_ignore_decision', 'validate_anchor_resolution_receipt', 'validate_opportunity_candidate', 'validate_source_card', 'validate_source_snapshot', 'validate_trigger_scope_receipt', 'with_content_hash']
