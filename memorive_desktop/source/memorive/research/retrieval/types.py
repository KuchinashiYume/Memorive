"""The records retrieval passes along: what the question asks, the candidate chunks, the context pack.

Query understanding only classifies the question and sets strategy parameters; candidate retrieval
recalls chunks; context assembly packs them for the analysis.
- QueryType: the five question types (every question gets one).
- RetrievalStrategy: the strategy parameters, including counterexample_boost for context assembly.
- QueryUnderstanding: the whole output of query understanding (parameters only, no chunks, no pack).
- Candidate, CandidateSet: what candidate retrieval recalls.
- ContextBlock, ContextPack: what context assembly hands to the analysis. Job folders pickle the
  pack (memorive/platform/pickle_compat.py reads old ones).

limitation_interest is a side signal next to the question type (the question is about limitations
the authors state); it is not a sixth type and does not set counterexample_boost.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from memorive.research.retrieval.ownership import OwnershipContributor


class QueryType(str, Enum):
    """The question type. Every question gets one; with no signal at all it is LOOKUP_CONCEPT with
    is_fallback set."""
    FIND_DATA = "find_data"                      # a number or measure
    FIND_METHOD = "find_method"                  # how something is done: a procedure, a technique
    FIND_COUNTEREXAMPLE = "find_counterexample"  # counter-evidence (sets counterexample_boost)
    REVIEW_SYNTHESIS = "review_synthesis"        # an overview: development, comparison
    LOOKUP_CONCEPT = "lookup_concept"            # what something is; also the fallback


@dataclass(frozen=True)
class RetrievalStrategy:
    """The strategy parameters query understanding sets; candidate retrieval and context assembly read them.

    counterexample_boost: more room for counterexamples in the pack. Set only when the question type
      is FIND_COUNTEREXAMPLE; limitation_interest never sets it.
    limitation_interest: the question is about limitations the authors state. It changes only
      recall_emphasis, not the room for counterexamples (looking for limitations is not looking for
      counter-evidence).
    recall_emphasis: a coarse hint, recorded in the log; there are no per-field vectors, so it
      changes retrieval little, and no field weights are built from it.
    """
    qtype: QueryType
    counterexample_boost: bool = False           # True only for FIND_COUNTEREXAMPLE
    limitation_interest: bool = False            # about stated limitations; no more counterexample room
    recall_emphasis: str | None = None           # 'key_data', 'method', 'diversity', 'counterexample', 'limitations' or None


@dataclass(frozen=True)
class QueryUnderstanding:
    """The whole output of query understanding: strategy parameters only, no chunks and no pack
    (recall is candidate retrieval's job, packing context assembly's)."""
    question: str
    qtype: QueryType
    strategy: RetrievalStrategy
    matched_signals: tuple = ()                  # the signal words that matched, in order, once each; () for the fallback
    is_fallback: bool = False                    # no signal matched: a neutral strategy
    confidence: str = "rule"                     # 'rule' (a rule matched) or 'low' (fallback)


@dataclass(frozen=True)
class QueryFacet:
    """Core source-structure question facet used for independent retrieval coverage."""
    id: str
    required: bool = True
    query_terms: tuple[str, ...] = ()
    preferred_card_paths: tuple[str, ...] = ()
    preferred_sections: tuple[str, ...] = ()
    preferred_block_types: tuple[str, ...] = ()
    min_evidence_blocks: int = 1


@dataclass(frozen=True)
class Candidate:
    """One recalled chunk. Recall only: no weight, tokens or counterexample fields (those are packing).

    distance is Chroma's raw query distance, smaller is closer; candidates are sorted by it ascending,
      never treated as a similarity. Context assembly converts it if it needs a similarity.
    admission is always "active" (the card is admitted), which is not the same as reviewed.
    field is filled when the card says which field the chunk belongs to, else None; the chunk is
      kept either way. Required: the first four fields and admission.
    verified_by_evidence_review is None when require_verified is off; True only when the card state
      log admitted the card on a card review verdict (compared by the enum value
      Trigger.EVIDENCE_REVIEW_VERDICT.value, not by a label text).
    """
    chunk_id: str                                # the vector's key (metadata.chunk_id)
    paper_id: str                                # the paper (metadata.paper_id)
    text: str                                    # the chunk's text (Chroma document)
    distance: float                              # raw distance, smaller is closer
    admission: str = "active"                    # always active; not the same as reviewed
    field: str | None = None                     # the card field, when known
    verified_by_evidence_review: bool | None = None           # None when require_verified is off
    credibility: str | None = None               # the card review's credibility of the field (verified,
                                                 # anchor_uncertain, flagged, unknown), read from the card's
                                                 # field_credibility; passed on only, it changes no weight
    rerank_score: float | None = None            # the cross-encoder's relevance score; None with reranking
                                                 # off (the weight is then 1/(1+distance)). With reranking on,
                                                 # context assembly uses it as the weight; larger is more relevant.
    ranking_rank: int | None = None              # the place in a model-generated ranking; not a score
    section_path: str | None = None               # source-structure: DocumentChunk structure metadata; missing stays unknown
    block_type: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    metadata_status: str = "unknown"              # vector/chunk_sidecar/unknown; never inferred silently
    facet_ids: tuple[str, ...] = ()
    retrieval_reasons: tuple[str, ...] = ()
    source_role: str | None = None
    member_chunk_ids: tuple[str, ...] = ()
    logical_span_id: str | None = None
    logical_role: str | None = None
    # DATA-OWNERSHIP: one authoritative ownership fact and its immutable derived binding.
    ownership_subject_ref: str | None = None
    ownership_contract_version: str | None = None
    ownership_aggregation_policy: str | None = None
    data_ownership: str | None = None
    ownership_assertion_ref: str | None = None
    ownership_revision: int | None = None
    ownership_basis_ref: str | None = None
    ownership_projection_value: str | None = None
    ownership_projection_revision: int | None = None
    ownership_mix_state: str | None = None
    ownership_contributors: tuple[OwnershipContributor, ...] = ()
    ownership_escalated_by: tuple[str, ...] = ()
    ownership_digest: str | None = None
    ownership_resolution_status: str = "legacy_missing"
    ownership_error_codes: tuple[str, ...] = ()
    ownership_warnings: tuple[str, ...] = ()
    legacy_bucket: str = "L3_insufficient_authority"


@dataclass(frozen=True)
class CandidateSet:
    """What candidate retrieval returns: the candidates and counts (the log records the same counts).
    No weights, tokens or counterexample slots; those are packing."""
    candidates: tuple                            # tuple[Candidate], by distance, within the per-paper limit, at most target
    target_active_count: int                     # the fixed number to recall (set by environment, not by library size)
    raw_recall_count: int                        # what Chroma returned (with overfetch)
    filtered_non_active_count: int               # dropped: not active, or not reviewed when required
    final_active_count: int                      # active candidates kept (at most target)
    reached_overfetch_cap: bool                  # MAX_OVERFETCH was reached with fewer than target
    require_verified: bool = False
    scope_mode: str = "shared"                   # shared or paper_id; shared by default
    scope_paper_id: str | None = None            # the paper when scope_mode is paper_id, else None


@dataclass(frozen=True)
class ContextBlock:
    """One chunk in the context pack. Packing fields only; nothing of the analysis or opportunities
    (answer, analysis, claim, tension, judgement, final_conclusion ...)."""
    content: str                                 # the chunk's text
    source: dict                                 # where it comes from: {"chunk_id", "paper_id", "field"}; field may be None
    weight: float                                # the weight: 1/(1+max(distance, 0)) times the rules
    is_counterexample: bool                      # a likely counterexample (strong signals only; the analysis judges it)
    tokens: int                                  # ceil(len(content) x ratio) + overhead, an upper estimate
    admission: str = "active"                    # always active; not the same as reviewed
    verified_by_evidence_review: bool | None = None           # as in the Candidate
    credibility: str | None = None               # the Candidate's credibility, passed on
    rerank_score: float | None = None            # the Candidate's rerank_score, for records and diagnosis
    member_chunk_ids: tuple[str, ...] = ()
    logical_span_id: str | None = None
    logical_role: str | None = None
    facet_ids: tuple[str, ...] = ()
    retrieval_reasons: tuple[str, ...] = ()
    source_role: str | None = None
    metadata_status: str = "unknown"
    section_path: str | None = None
    block_type: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    # DATA-OWNERSHIP: block binding is derived from Candidate/member contributors.
    block_ref: str | None = None
    ownership_subject_ref: str | None = None
    ownership_contract_version: str | None = None
    ownership_aggregation_policy: str | None = None
    data_ownership: str | None = None
    ownership_assertion_ref: str | None = None
    ownership_revision: int | None = None
    ownership_basis_ref: str | None = None
    ownership_mix_state: str | None = None
    ownership_contributors: tuple[OwnershipContributor, ...] = ()
    ownership_escalated_by: tuple[str, ...] = ()
    ownership_digest: str | None = None
    ownership_resolution_status: str = "legacy_missing"
    ownership_error_codes: tuple[str, ...] = ()
    ownership_warnings: tuple[str, ...] = ()
    legacy_bucket: str = "L3_insufficient_authority"


@dataclass(frozen=True)
class ContextPack:
    """What context assembly hands to the analysis: the only literature context it gets. Packing fields
    only; candidate retrieval's counts are passed through as they are, packing adds its own.
    estimated_tokens <= token_hard_limit always holds: whole blocks are kept or dropped, never cut;
    an empty pack is allowed."""
    blocks: tuple                                # tuple[ContextBlock], by weight, counterexamples kept, within the token limit
    estimated_tokens: int                        # the estimate with overhead, at most token_hard_limit
    token_hard_limit: int                        # the hard limit, set by environment
    counterexample_slots: dict                   # {"target": room for counterexamples, "filled": how many}; fewer are not padded
    truncated: bool                              # blocks were dropped for the token limit (details in the log)
    # ── candidate retrieval's counts, passed through unchanged ──
    raw_recall_count: int
    filtered_non_active_count: int
    final_active_count: int
    require_verified: bool = False
    scope_mode: str = "shared"                   # from the CandidateSet, so the analysis knows the scope
    scope_paper_id: str | None = None
    retrieval_mode: str = "multi_paper_synthesis"
    query_facets: tuple[QueryFacet, ...] = ()
    coverage_status: str = "not_assessed"
    missing_facets: tuple[str, ...] = ()
    coverage_by_facet: dict | None = None
    retry_count: int = 0
    fallback_used: bool = False
    answer_complete: bool | None = None
    bibliography_share: float = 0.0
    excluded_bibliography_count: int = 0
    # DATA-OWNERSHIP: only selected blocks contribute to this immutable pack snapshot.
    context_pack_ref: str | None = None
    context_pack_hash: str | None = None
    ownership_subject_ref: str | None = None
    ownership_contract_version: str | None = None
    ownership_aggregation_policy: str | None = None
    effective_data_ownership: str | None = None
    ownership_mix_state: str | None = None
    ownership_contributors: tuple[OwnershipContributor, ...] = ()
    ownership_escalated_by: tuple[str, ...] = ()
    ownership_digest: str | None = None
    ownership_resolution_status: str = "legacy_missing"
    ownership_error_codes: tuple[str, ...] = ()
    ownership_warnings: tuple[str, ...] = ()
    legacy_bucket: str = "L3_insufficient_authority"
    original_estimated_tokens: int = 0
    dropped_evidence_block_count: int = 0
    dropped_reason_counts: dict | None = None
    ownership_propagation_receipt: dict | None = None
    card_review_qualifications: tuple[dict, ...] = ()


@dataclass(frozen=True)
class LogicalSpan:
    """Runtime-only group; original member IDs remain the citation boundary."""
    logical_span_id: str
    member_chunk_ids: tuple[str, ...]
    content: str
    paper_id: str
    section_path: str | None
    logical_role: str
    block_type: str | None
    page_start: int | None
    page_end: int | None
    distance: float
    facet_ids: tuple[str, ...] = ()
    retrieval_reasons: tuple[str, ...] = ()
    source_role: str | None = None
    metadata_status: str = "unknown"
    field: str | None = None
    admission: str = "active"
    verified_by_evidence_review: bool | None = None
    credibility: str | None = None
    rerank_score: float | None = None
    # DATA-OWNERSHIP: aggregate every member; never inherit only members[0].
    ownership_subject_ref: str | None = None
    ownership_contract_version: str | None = None
    ownership_aggregation_policy: str | None = None
    data_ownership: str | None = None
    ownership_assertion_ref: str | None = None
    ownership_revision: int | None = None
    ownership_basis_ref: str | None = None
    ownership_mix_state: str | None = None
    ownership_contributors: tuple[OwnershipContributor, ...] = ()
    ownership_escalated_by: tuple[str, ...] = ()
    ownership_digest: str | None = None
    ownership_resolution_status: str = "legacy_missing"
    ownership_error_codes: tuple[str, ...] = ()
    ownership_warnings: tuple[str, ...] = ()
    legacy_bucket: str = "L3_insufficient_authority"
