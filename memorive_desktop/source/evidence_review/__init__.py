"""Memorive EVIDENCE-REVIEW/EVIDENCE_REVIEW · EVIDENCE_REVIEW 校核。

Verification 第 1 步:机械层引用 ID 校验(只校形式、纯规则、零额度)。

    from evidence_review import mechanical_check
    result = mechanical_check(card_path)   # → MechanicalResult{ok, problems, checked, resolved, broken, malformed}

只校形式(chunk_id resolve)、不校内容(内容留第 2 步 R1–R9 搬运类 / 第 3 步 GPT 异源)。
系统性错误(卡不可读 / YAML 解析失败 / chunk 库不可读)raise EvidenceReviewError;锚点级问题进 result.problems。
"""
from .errors import CardParseError, CardUnreadable, ChunkStoreError, EvidenceReviewError, OwnershipFailClosed
from .mechanical import mechanical_check
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
    "mechanical_check",
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
