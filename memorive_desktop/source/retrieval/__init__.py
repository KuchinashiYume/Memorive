"""Memorive RETRIEVAL/RETRIEVAL · RETRIEVAL 检索加权。

Retrieval 第 1 步:QueryUnderstanding Query Understanding(问题 → 主类 + 检索策略参数)。

    from retrieval import understand_query
    qu = understand_query("有没有人反对这个结论?")   # → QueryUnderstanding{qtype, strategy, ...}

QueryUnderstanding **只出策略参数、不召回 / 不打包**(召回=CandidateRetrieval、打包=ContextAssembly,均后续步骤,本步不建)。
分类 Core 用规则/关键词、零额度;换 MODEL_GATEWAY 模型后端的 seam 已预留(经 MODEL_GATEWAY、绝不直连 SDK)。
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
