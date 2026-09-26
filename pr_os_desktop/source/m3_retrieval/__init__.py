"""PR-OS P01/T07/M03 · M3 检索加权。

T7 第 1 步:M3a Query Understanding(问题 → 主类 + 检索策略参数)。

    from m3_retrieval import understand_query
    qu = understand_query("有没有人反对这个结论?")   # → QueryUnderstanding{qtype, strategy, ...}

M3a **只出策略参数、不召回 / 不打包**(召回=M3b、打包=M3c,均后续步骤,本步不建)。
分类 Phase 1 用规则/关键词、零额度;换 M9 模型后端的 seam 已预留(经 M9、绝不直连 SDK)。
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
from .p02_t06_m03_coverage_artifacts import (
    ArtifactPointer,
    ChunkSnapshot,
    FacetCoverageInput,
    FacetSpec,
    FrozenArtifactRecord,
    MemberRole,
    SourceIdDisposition,
    T6ArtifactValidationError,
    admit_chunk_snapshots,
    build_facet_definition_artifact,
    build_logical_span_artifact,
    build_references_bias_policy,
    build_retrieval_coverage_assessment,
    build_source_role_assessment,
    build_source_role_policy,
    build_span_assembly_policy,
    derive_facet_coverage_inputs,
    validate_artifact_graph,
    validate_holdout_public_manifest,
)
from .query_understanding import understand_query
from .structured_context import (
    build_card_anchor_candidates,
    build_logical_spans,
    build_structured_context_pack,
    detect_query_facets,
    infer_source_role,
    retrieve_faceted_candidates,
)
from .types import (Candidate, CandidateSet, ContextBlock, ContextPack, LogicalSpan,
                    QueryFacet, QueryType, QueryUnderstanding, RetrievalStrategy)

__all__ = [
    # T7 第 1 步 M3a
    "understand_query", "QueryType", "RetrievalStrategy", "QueryUnderstanding",
    "detect_query_facets", "QueryFacet",
    "retrieve_faceted_candidates",
    # T7 第 2 步 M3b
    "retrieve_candidates", "Candidate", "CandidateSet",
    # T7 第 3 步 M3c
    "build_context_pack", "validate_context_pack_binding", "build_structured_context_pack",
    "build_card_anchor_candidates", "build_logical_spans",
    "infer_source_role",
    "ContextBlock", "ContextPack", "LogicalSpan",
    # P06/T08 ownership contract
    "OWNERSHIP_CONTRACT_VERSION", "OWNERSHIP_AGGREGATION_POLICY",
    "ATOMIC_OWNERSHIP_VALUES", "OwnershipContractError", "OwnershipAssertion",
    "OwnershipContributor", "OwnershipSnapshot", "OwnershipPreflight",
    "OwnershipAmendment", "OwnershipPropagationReceipt", "LegacyAdapterResult",
    "OwnershipGuard",
    # P02/T06 immutable governance layer
    "ArtifactPointer", "ChunkSnapshot", "FacetCoverageInput", "FacetSpec",
    "FrozenArtifactRecord", "MemberRole", "T6ArtifactValidationError",
    "SourceIdDisposition", "admit_chunk_snapshots",
    "build_facet_definition_artifact", "build_logical_span_artifact",
    "build_references_bias_policy", "build_retrieval_coverage_assessment",
    "build_source_role_assessment", "build_source_role_policy",
    "build_span_assembly_policy", "validate_artifact_graph",
    "derive_facet_coverage_inputs",
    "validate_holdout_public_manifest",
]
