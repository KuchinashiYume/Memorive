"""Analysis of one paper: a model answers a fixed set of analysis questions from the paper's context pack.

The workflow's analysis node calls analysis.py; revision.py applies review findings to an analysis
(the workflow and the card review call it); types.py holds the analysis records. The card review
imports review_orchestration_events.py, but from code the app does not reach. The cross-topic
runner and the quality artifacts module are not reached from the app's own code; tests call them.
"""
from memorive.research.analysis.analysis import (
    AnalysisError,
    OwnershipFailClosed,
    RegionBlocked,
    analyze,
    build_analysis_request_envelope,
    build_payload,
    parse_analysis,
)
from memorive.research.analysis.cost_query import RED_LINE_CNY
from memorive.research.analysis.types import (
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
    "analyze",
    "build_payload",
    "parse_analysis",
    "build_analysis_request_envelope",
    "AnalysisError",
    "OwnershipFailClosed",
    "RegionBlocked",
    "RED_LINE_CNY",
    "AnalysisRequestEnvelope",
    "AnalysisResult",
    "LiteratureClaim",
    "FlaggedClaim",
    "DISCLAIMER",
    "disclaimer_for",
    "LABEL_LITERATURE",
    "LABEL_AI_GENERAL",
    "LABEL_RISKS",
    "LABEL_RECHECK",
    "LABEL_FLAGGED",
    "LABEL_RETRIEVAL_LIMITATIONS",
    "LABEL_SOURCE_INCONSISTENCIES",
]

# Names the package offered before; restored with the code behind them.
from memorive.research.analysis.cost_query import cost_summary
from memorive.research.analysis.retrieval_quality_research_analysis_quality_artifacts import AnalysisQualityValidationError
from memorive.research.analysis.retrieval_quality_research_analysis_quality_artifacts import build_analysis_quality_assessment
from memorive.research.analysis.retrieval_quality_research_analysis_quality_artifacts import project_coverage_onto_analysis
from memorive.research.analysis.retrieval_quality_research_analysis_quality_artifacts import validate_analysis_source_ids
__all__ = [*__all__, 'cost_summary', 'AnalysisQualityValidationError', 'build_analysis_quality_assessment', 'project_coverage_onto_analysis', 'validate_analysis_source_ids']
