"""Memorive 证据复核：卡片的转写核对、判断复核、评分入库和修复。"""
from .errors import CardParseError, CardUnreadable, ChunkStoreError, EvidenceReviewError, OwnershipFailClosed
from .parser import parse_violations
from .problems import MechanicalResult, Problem
from .report import EvidenceReviewReport, resolve_report, write_report
from .transcription import transcription_verify
from .grading import AdmitResult, grade_and_admit as legacy_grade_and_admit, grade_from_report
from .repair_flow import RepairAdmitResult, repair_and_admit
from .judgment import (Conclusion, build_gpt_payload, conclusion_from_claim, judgment_verify,
                       parse_gpt_disputes, select_judgment_candidates, should_gpt_verify,
                       VerifierCandidate, verify_research_analysis)
from .claim_evidence import (
    validate_claim_evidence_review,
)
from .quality_axes import (
    needs_review,
)
from .atomic_repair import (
    ATOMIC_REPAIR_MAX_TOKENS,
    ATOMIC_REPAIR_TIMEOUT_SECONDS,
    DELTA_PASS,
    HUMAN_REQUIRED,
    PASS_WITH_TOMBSTONES,
    TARGETED_REPAIR,
    AtomicRepairContractError,
)
from .ownership_binding import (
    OwnershipReviewTask,
    build_ownership_review_task,
)

# The production default is the user-approved single repair round.  Keep the
# former two-whole-card implementation under an explicit legacy name only.
grade_and_admit = repair_and_admit

__all__ = [
    "MechanicalResult",
    "Problem",
    "EvidenceReviewError",
    "CardUnreadable",
    "CardParseError",
    "ChunkStoreError",
    "OwnershipFailClosed",
    "transcription_verify",
    "parse_violations",
    "EvidenceReviewReport",
    "write_report",
    "resolve_report",
    "grade_and_admit",
    "repair_and_admit",
    "RepairAdmitResult",
    "legacy_grade_and_admit",
    "grade_from_report",
    "AdmitResult",
    "judgment_verify",
    "should_gpt_verify",
    "build_gpt_payload",
    "parse_gpt_disputes",
    "Conclusion",
    "verify_research_analysis",
    "conclusion_from_claim",
    "select_judgment_candidates",
    "VerifierCandidate",
    "OwnershipReviewTask",
    "build_ownership_review_task",
    "validate_claim_evidence_review",
    "needs_review",
    "TARGETED_REPAIR",
    "DELTA_PASS",
    "PASS_WITH_TOMBSTONES",
    "HUMAN_REQUIRED",
    "ATOMIC_REPAIR_MAX_TOKENS",
    "ATOMIC_REPAIR_TIMEOUT_SECONDS",
    "AtomicRepairContractError",
]

# Names the package offered before; restored with the code behind them.
from .mechanical import mechanical_check
from .report import read_report
from .transcription import make_evidence_review_source
from .transcription import verify_and_drive
from .claim_evidence import apply_fact_verdicts
from .claim_evidence import build_claim_evidence_view
from .claim_evidence import field_credibility_summaries
from .claim_evidence import repair_evidence_occurrence
from .claim_evidence import render_reviewed_field
from .quality_axes import assess_numeric_plausibility
from .quality_axes import build_quality_axes
from .quality_axes import compare_numeric_facts
from .atomic_repair import apply_atomic_repairs
from .atomic_repair import build_atomic_delta_view
from .atomic_repair import build_atomic_repair_bundle
from .atomic_repair import build_atomic_repair_prompt
from .atomic_repair import build_atomic_repair_request
from .atomic_repair import classify_delta_resolution
from .atomic_repair import governed_omission_precheck
from .atomic_repair import prune_unresolved_targets
from .atomic_repair import route_full_review_to_closure
from .atomic_repair import validate_atomic_repair_gateway_response
from .atomic_repair import validate_atomic_repairs
from .opportunity_verification import verify_opportunity_candidate
from .ownership_binding import compute_analysis_binding_hash
__all__ = [*__all__, 'mechanical_check', 'verify_and_drive', 'make_evidence_review_source', 'read_report', 'compute_analysis_binding_hash', 'build_claim_evidence_view', 'apply_fact_verdicts', 'field_credibility_summaries', 'repair_evidence_occurrence', 'render_reviewed_field', 'build_quality_axes', 'compare_numeric_facts', 'assess_numeric_plausibility', 'route_full_review_to_closure', 'build_atomic_repair_bundle', 'build_atomic_repair_prompt', 'build_atomic_repair_request', 'validate_atomic_repair_gateway_response', 'validate_atomic_repairs', 'apply_atomic_repairs', 'build_atomic_delta_view', 'classify_delta_resolution', 'governed_omission_precheck', 'prune_unresolved_targets', 'verify_opportunity_candidate']
