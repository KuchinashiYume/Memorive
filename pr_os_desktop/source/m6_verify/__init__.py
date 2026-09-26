"""PR-OS P01/T06/M06 · M6 校核。

T6 第 1 步:机械层引用 ID 校验(只校形式、纯规则、零额度)。

    from m6_verify import mechanical_check
    result = mechanical_check(card_path)   # → MechanicalResult{ok, problems, checked, resolved, broken, malformed}

只校形式(chunk_id resolve)、不校内容(内容留第 2 步 R1–R9 搬运类 / 第 3 步 GPT 异源)。
系统性错误(卡不可读 / YAML 解析失败 / chunk 库不可读)raise M6Error;锚点级问题进 result.problems。
"""
from .errors import CardParseError, CardUnreadable, ChunkStoreError, M6Error, OwnershipFailClosed
from .mechanical import mechanical_check
from .parser import parse_violations
from .problems import MechanicalResult, Problem
from .report import M6Report, read_report, resolve_report, write_report
from .transcription import make_m6_source, transcription_verify, verify_and_drive
from .grading import AdmitResult, grade_and_admit as legacy_grade_and_admit, grade_from_report
from .repair_flow import RepairAdmitResult, repair_and_admit
from .judgment import (Conclusion, build_gpt_payload, conclusion_from_claim, judgment_verify,
                       parse_gpt_disputes, select_judgment_candidates, should_gpt_verify,
                       VerifierCandidate, verify_m4_analysis)
from .claim_evidence import (
    apply_fact_verdicts,
    build_claim_evidence_view,
    field_credibility_summaries,
    repair_evidence_occurrence,
    render_reviewed_field,
    validate_claim_evidence_review,
)
from .quality_axes import (
    assess_numeric_plausibility,
    build_quality_axes,
    compare_numeric_facts,
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
    apply_atomic_repairs,
    build_atomic_delta_view,
    build_atomic_repair_bundle,
    build_atomic_repair_prompt,
    build_atomic_repair_request,
    classify_delta_resolution,
    governed_omission_precheck,
    prune_unresolved_targets,
    route_full_review_to_closure,
    validate_atomic_repair_gateway_response,
    validate_atomic_repairs,
)
from .opportunity_verification import verify_opportunity_candidate
from .ownership_binding import (
    OwnershipReviewTask,
    build_ownership_review_task,
    compute_analysis_binding_hash,
)

# The production default is the user-approved single repair round.  Keep the
# former two-whole-card implementation under an explicit legacy name only.
grade_and_admit = repair_and_admit

__all__ = [
    # 第 1 步 机械层
    "mechanical_check", "MechanicalResult", "Problem",
    "M6Error", "CardUnreadable", "CardParseError", "ChunkStoreError", "OwnershipFailClosed",
    # 第 2 步-c 搬运类校核
    "transcription_verify", "verify_and_drive", "make_m6_source",
    "parse_violations", "M6Report", "write_report", "resolve_report", "read_report",
    # v6.4 之四·上半 分级信任准入(M6 判 → per-field 信用档 → 卡带档进 active、不整卡 quarantine)
    "grade_and_admit", "repair_and_admit", "RepairAdmitResult",
    "legacy_grade_and_admit", "grade_from_report", "AdmitResult",
    # 第 3 步 判断类 GPT 异源 + T9第2步② M4分析结论校核 glue
    "judgment_verify", "should_gpt_verify", "build_gpt_payload", "parse_gpt_disputes", "Conclusion",
    "verify_m4_analysis", "conclusion_from_claim", "select_judgment_candidates", "VerifierCandidate",
    "OwnershipReviewTask", "build_ownership_review_task", "compute_analysis_binding_hash",
    # Phase 1 P0-B/P0-D additive sidecars
    "build_claim_evidence_view", "validate_claim_evidence_review",
    "apply_fact_verdicts", "field_credibility_summaries", "repair_evidence_occurrence",
    "render_reviewed_field",
    "build_quality_axes", "needs_review", "compare_numeric_facts",
    "assess_numeric_plausibility",
    # Provider-neutral Full -> targeted repair -> Delta closure.
    "TARGETED_REPAIR", "DELTA_PASS", "PASS_WITH_TOMBSTONES", "HUMAN_REQUIRED",
    "ATOMIC_REPAIR_MAX_TOKENS", "ATOMIC_REPAIR_TIMEOUT_SECONDS",
    "AtomicRepairContractError", "route_full_review_to_closure",
    "build_atomic_repair_bundle", "build_atomic_repair_prompt", "build_atomic_repair_request",
    "validate_atomic_repair_gateway_response", "validate_atomic_repairs",
    "apply_atomic_repairs", "build_atomic_delta_view", "classify_delta_resolution",
    "governed_omission_precheck", "prune_unresolved_targets",
    # P03/T05 deterministic role-separated opportunity verification adapter
    "verify_opportunity_candidate",
]
