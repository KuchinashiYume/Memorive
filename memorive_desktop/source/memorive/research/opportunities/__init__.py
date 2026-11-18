"""Research opportunities: divergences, differences in conditions and gaps across the processed papers,
and the questions an answer proposes from cited evidence.

Finding opportunities across papers (the opportunities page, opportunities.* methods):
  facts.py     a fact sheet per processed paper, from its card and chunks in the library
  finder.py    papers grouped, one model request per group, each opportunity checked against the facts
  runs.py      the run (research runtime), the ledger of opportunities found and what the user did
               with them, and the tensions the weekly report lists
  groups.py    comparison groups: the papers the user chose to search among, and files picked from
               disk on their way through the inbox

Research Q&A uses research_opportunities.py and imports core.py for CONCEPT_ORDER, which lives in
canonical.py together with the hashing shared with the verifier
(memorive/documents/card_review/opportunity_verification.py). The rest of core.py is a pipeline that builds
source, gap, comparison and opportunity objects; only tests call it.
"""

from memorive.research.opportunities.canonical import (
    canonical_json_bytes,
    typed_payload_hash,
)
from memorive.research.opportunities.core import (
    CONCEPT_ORDER,
    SOURCE_PROFILE,
    normalize_text,
)
from memorive.research.opportunities.errors import (
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
from memorive.research.opportunities.canonical import content_hash_matches
from memorive.research.opportunities.canonical import external_hash_is_valid
from memorive.research.opportunities.canonical import with_content_hash
from memorive.research.opportunities.core import assess_comparability
from memorive.research.opportunities.core import build_opportunity_candidate
from memorive.research.opportunities.core import classify_tension
from memorive.research.opportunities.core import evidence_set_hash
from memorive.research.opportunities.core import extract_author_stated_gaps
from memorive.research.opportunities.core import finite_number_or_none
from memorive.research.opportunities.core import make_ignore_decision
from memorive.research.opportunities.core import make_anchor_resolution_receipt
from memorive.research.opportunities.core import make_pair_ledger
from memorive.research.opportunities.core import make_source_snapshot
from memorive.research.opportunities.core import make_trigger_scope_receipt
from memorive.research.opportunities.core import project_comparison_concepts
from memorive.research.opportunities.core import validate_ignore_decision
from memorive.research.opportunities.core import validate_anchor_resolution_receipt
from memorive.research.opportunities.core import validate_opportunity_candidate
from memorive.research.opportunities.core import validate_source_card
from memorive.research.opportunities.core import validate_source_snapshot
from memorive.research.opportunities.core import validate_trigger_scope_receipt
from memorive.research.opportunities.errors import CandidateRejected
from memorive.research.opportunities.errors import SourceRejected
__all__ = [*__all__, 'CandidateRejected', 'SourceRejected', 'assess_comparability', 'build_opportunity_candidate', 'classify_tension', 'content_hash_matches', 'evidence_set_hash', 'external_hash_is_valid', 'extract_author_stated_gaps', 'finite_number_or_none', 'make_ignore_decision', 'make_anchor_resolution_receipt', 'make_pair_ledger', 'make_source_snapshot', 'make_trigger_scope_receipt', 'project_comparison_concepts', 'validate_ignore_decision', 'validate_anchor_resolution_receipt', 'validate_opportunity_candidate', 'validate_source_card', 'validate_source_snapshot', 'validate_trigger_scope_receipt', 'with_content_hash']
