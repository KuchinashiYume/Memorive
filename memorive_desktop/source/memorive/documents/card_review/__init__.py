"""Card review: checking a written card against the paper and repairing it.

Used outside the package: judgment.py, analysis_omission.py and analysis_revision.py by the
workflow's review nodes; claim_evidence.py, atomic_review.py, reviewer_routing.py and
segmented_distill_repair.py by research Q&A's claim check; mechanical.py and atomic_review.py by
research_recommendations; ownership_binding.py by research_analysis. transcription.py,
repair_flow.py, grading.py and the other modules are used inside the package by these.

Not reached from the app's own code (tests call them): review_orchestration/, an operator API
(create_or_get_job, pause, resume, cancel, dispatch_once with an execute function the caller
supplies); the two fact_identity_* modules; research_handoff_cross_analysis_review.py.
opportunity_verification.py, which recomputes research opportunities independently of the code
that produced them, has no caller outside the package and its tests.
"""
from memorive.documents.card_review.errors import CardParseError, CardUnreadable, ChunkStoreError, EvidenceReviewError, OwnershipFailClosed
from memorive.documents.card_review.parser import parse_violations
from memorive.documents.card_review.problems import MechanicalResult, Problem
from memorive.documents.card_review.report import EvidenceReviewReport, resolve_report, write_report
from memorive.documents.card_review.transcription import transcription_verify
from memorive.documents.card_review.grading import AdmitResult, grade_and_admit as legacy_grade_and_admit, grade_from_report
from memorive.documents.card_review.repair_flow import RepairAdmitResult, repair_and_admit
from memorive.documents.card_review.judgment import (Conclusion, build_gpt_payload, conclusion_from_claim, judgment_verify,
                       parse_gpt_disputes, select_judgment_candidates, should_gpt_verify,
                       VerifierCandidate, verify_research_analysis)
from memorive.documents.card_review.claim_evidence import (
    validate_claim_evidence_review,
)
from memorive.documents.card_review.quality_axes import (
    needs_review,
)
from memorive.documents.card_review.atomic_repair import (
    ATOMIC_REPAIR_MAX_TOKENS,
    ATOMIC_REPAIR_TIMEOUT_SECONDS,
    DELTA_PASS,
    HUMAN_REQUIRED,
    PASS_WITH_TOMBSTONES,
    TARGETED_REPAIR,
    AtomicRepairContractError,
)
from memorive.documents.card_review.ownership_binding import (
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
from memorive.documents.card_review.mechanical import mechanical_check
from memorive.documents.card_review.report import read_report
from memorive.documents.card_review.transcription import make_evidence_review_source
from memorive.documents.card_review.transcription import verify_and_drive
from memorive.documents.card_review.claim_evidence import apply_fact_verdicts
from memorive.documents.card_review.claim_evidence import build_claim_evidence_view
from memorive.documents.card_review.claim_evidence import field_credibility_summaries
from memorive.documents.card_review.claim_evidence import repair_evidence_occurrence
from memorive.documents.card_review.claim_evidence import render_reviewed_field
from memorive.documents.card_review.quality_axes import assess_numeric_plausibility
from memorive.documents.card_review.quality_axes import build_quality_axes
from memorive.documents.card_review.quality_axes import compare_numeric_facts
from memorive.documents.card_review.atomic_repair import apply_atomic_repairs
from memorive.documents.card_review.atomic_repair import build_atomic_delta_view
from memorive.documents.card_review.atomic_repair import build_atomic_repair_bundle
from memorive.documents.card_review.atomic_repair import build_atomic_repair_prompt
from memorive.documents.card_review.atomic_repair import build_atomic_repair_request
from memorive.documents.card_review.atomic_repair import classify_delta_resolution
from memorive.documents.card_review.atomic_repair import governed_omission_precheck
from memorive.documents.card_review.atomic_repair import prune_unresolved_targets
from memorive.documents.card_review.atomic_repair import route_full_review_to_closure
from memorive.documents.card_review.atomic_repair import validate_atomic_repair_gateway_response
from memorive.documents.card_review.atomic_repair import validate_atomic_repairs
from memorive.documents.card_review.opportunity_verification import verify_opportunity_candidate
from memorive.documents.card_review.ownership_binding import compute_analysis_binding_hash
__all__ = [*__all__, 'mechanical_check', 'verify_and_drive', 'make_evidence_review_source', 'read_report', 'compute_analysis_binding_hash', 'build_claim_evidence_view', 'apply_fact_verdicts', 'field_credibility_summaries', 'repair_evidence_occurrence', 'render_reviewed_field', 'build_quality_axes', 'compare_numeric_facts', 'assess_numeric_plausibility', 'route_full_review_to_closure', 'build_atomic_repair_bundle', 'build_atomic_repair_prompt', 'build_atomic_repair_request', 'validate_atomic_repair_gateway_response', 'validate_atomic_repairs', 'apply_atomic_repairs', 'build_atomic_delta_view', 'classify_delta_resolution', 'governed_omission_precheck', 'prune_unresolved_targets', 'verify_opportunity_candidate']
