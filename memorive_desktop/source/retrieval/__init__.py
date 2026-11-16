"""Retrieval for analysis and review: understanding the query, finding candidate chunks, and building
the context pack a model is shown.

query_understanding.py, candidate_retrieval.py and context_pack.py are the three steps;
structured_context.py and document_context.py build context from a paper's structure. The workflow
uses them, research analysis uses context_pack.py, and research Q&A uses coverage_artifacts.py for
its coverage report. retrieval_calibration/ (with its own cli.py) and shadow_seam.py are not reached
from the app's own code; tests call them.
"""
from .candidate_retrieval import retrieve_candidates
from .context_pack import build_context_pack, validate_context_pack_binding
from .ownership import (
    ATOMIC_OWNERSHIP_VALUES,
    OWNERSHIP_AGGREGATION_POLICY,
    OWNERSHIP_CONTRACT_VERSION,
    LegacyAdapterResult,
    OwnershipAmendment,
    OwnershipAssertion,
    OwnershipContractError,
    OwnershipContributor,
    OwnershipGuard,
    OwnershipPreflight,
    OwnershipPropagationReceipt,
    OwnershipSnapshot,
)
from .coverage_artifacts import (
    ArtifactPointer,
    ChunkSnapshot,
    FacetCoverageInput,
    FacetSpec,
    FrozenArtifactRecord,
    MemberRole,
    SourceIdDisposition,
    EvidenceArtifactValidationError,
    derive_facet_coverage_inputs,
)
from .query_understanding import understand_query
from .structured_context import (
    build_card_anchor_candidates,
    build_logical_spans,
    build_structured_context_pack,
    detect_query_facets,
    infer_source_role,
)
from .types import (Candidate, CandidateSet, ContextBlock, ContextPack, LogicalSpan,
                    QueryFacet, QueryType, QueryUnderstanding, RetrievalStrategy)

__all__ = [
    "understand_query",
    "QueryType",
    "RetrievalStrategy",
    "QueryUnderstanding",
    "detect_query_facets",
    "QueryFacet",
    "retrieve_candidates",
    "Candidate",
    "CandidateSet",
    "build_context_pack",
    "validate_context_pack_binding",
    "build_structured_context_pack",
    "build_card_anchor_candidates",
    "build_logical_spans",
    "infer_source_role",
    "ContextBlock",
    "ContextPack",
    "LogicalSpan",
    "OWNERSHIP_CONTRACT_VERSION",
    "OWNERSHIP_AGGREGATION_POLICY",
    "ATOMIC_OWNERSHIP_VALUES",
    "OwnershipContractError",
    "OwnershipAssertion",
    "OwnershipContributor",
    "OwnershipSnapshot",
    "OwnershipPreflight",
    "OwnershipAmendment",
    "OwnershipPropagationReceipt",
    "LegacyAdapterResult",
    "OwnershipGuard",
    "ArtifactPointer",
    "ChunkSnapshot",
    "FacetCoverageInput",
    "FacetSpec",
    "FrozenArtifactRecord",
    "MemberRole",
    "EvidenceArtifactValidationError",
    "SourceIdDisposition",
    "derive_facet_coverage_inputs",
]

# Names the package offered before; restored with the code behind them.
from .coverage_artifacts import admit_chunk_snapshots
from .coverage_artifacts import build_facet_definition_artifact
from .coverage_artifacts import build_logical_span_artifact
from .coverage_artifacts import build_references_bias_policy
from .coverage_artifacts import build_retrieval_coverage_assessment
from .coverage_artifacts import build_source_role_assessment
from .coverage_artifacts import build_source_role_policy
from .coverage_artifacts import build_span_assembly_policy
from .coverage_artifacts import validate_artifact_graph
from .coverage_artifacts import validate_holdout_public_manifest
from .structured_context import retrieve_faceted_candidates
__all__ = [*__all__, 'retrieve_faceted_candidates', 'admit_chunk_snapshots', 'build_facet_definition_artifact', 'build_logical_span_artifact', 'build_references_bias_policy', 'build_retrieval_coverage_assessment', 'build_source_role_assessment', 'build_source_role_policy', 'build_span_assembly_policy', 'validate_artifact_graph', 'validate_holdout_public_manifest']
