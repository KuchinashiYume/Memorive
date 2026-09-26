"""Memorive RESEARCH-ANALYSIS/RESEARCH_ANALYSIS · RESEARCH_ANALYSIS 分析。

拿 RETRIEVAL Context Pack 出固定四段分析建议(建议性、非结论)。

融合铁律(文献支持 ⊥ AI 通识)+ source_id 强制 + 越界防线 + 免责语;经 MODEL_GATEWAY、绝不直连 SDK。
"""
from .analysis import (
    AnalysisError,
    OwnershipFailClosed,
    RegionBlocked,
    analyze,
    build_analysis_request_envelope,
    build_payload,
    parse_analysis,
)
from .cost_query import RED_LINE_CNY, cost_summary
from .retrieval_quality_research_analysis_quality_artifacts import (
    AnalysisQualityValidationError,
    build_analysis_quality_assessment,
    project_coverage_onto_analysis,
    validate_analysis_source_ids,
)
from .types import (
    DISCLAIMER,
    LABEL_AI_GENERAL,
    LABEL_FLAGGED,
    LABEL_LITERATURE,
    LABEL_RECHECK,
    LABEL_RETRIEVAL_LIMITATIONS,
    LABEL_RISKS,
    LABEL_SOURCE_INCONSISTENCIES,
    AnalysisResult,
    AnalysisRequestEnvelope,
    FlaggedClaim,
    LiteratureClaim,
    disclaimer_for,
)

__all__ = [
    "analyze", "build_payload", "parse_analysis", "build_analysis_request_envelope",
    "AnalysisError", "OwnershipFailClosed", "RegionBlocked",
    "cost_summary", "RED_LINE_CNY",
    "AnalysisRequestEnvelope", "AnalysisResult", "LiteratureClaim", "FlaggedClaim", "DISCLAIMER",
    "disclaimer_for",
    "LABEL_LITERATURE", "LABEL_AI_GENERAL", "LABEL_RISKS", "LABEL_RECHECK", "LABEL_FLAGGED",
    "LABEL_RETRIEVAL_LIMITATIONS", "LABEL_SOURCE_INCONSISTENCIES",
    # RETRIEVAL-QUALITY immutable governance layer
    "AnalysisQualityValidationError", "build_analysis_quality_assessment",
    "project_coverage_onto_analysis", "validate_analysis_source_ids",
]
