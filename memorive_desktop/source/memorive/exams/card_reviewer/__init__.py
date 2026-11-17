"""Card reviewer exams: the executors and the scoring of the card reviewer node's exam.

  policy.py             revisions, the scoring semantics, the workload profile and the request
                        reserve, comparison metadata
  successor.py          the successor form: its sample, scoring and pointer normalization
  repair_prompt.py      the repair prompt built from a failed attempt's fields
  role_projection.py    scoring of the card reviewer role (score_card_reviewer_role_projection)
  executor.py           QualityCardReviewerExamExecutor, the exam executor
  successor_executor.py QualityCardReviewerExamSuccessorExecutor, the successor form's executor

Every name stays importable from this package (the settings, the analysis reviewer exam, the data
anomaly exam and the analysis exam import from here). EXECUTOR_REF values are unchanged.
"""
from __future__ import annotations

from memorive.exams.card_reviewer.policy import (
    CARD_REVIEWER_EXAM_CATEGORY_ID,
    CARD_REVIEWER_ROLE_ID,
    CARD_REVIEWER_SCORING_PROJECTION_REVISION,
    CARD_REVIEWER_SCORE_SEMANTICS,
    CARD_REVIEWER_OPERATIONAL_POLICY_REVISION,
    CARD_REVIEWER_OPERATIONAL_PASSING_SCORE,
    CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION,
    CORE_CARD_REVIEWER_LIFECYCLE_REVISION,
    CORE_CARD_REVIEWER_WORKLOAD_PROFILE_REVISION,
    Core_CORE_FINAL_SUPPORTED_PAPER_COUNT,
    Core_CORE_FINAL_CLEANMD_TOTAL_CHARS,
    Core_CORE_FINAL_CLEANMD_MEAN_CHARS,
    CORE_CARD_REVIEWER_WORKLOAD_MULTIPLIER,
    CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT,
    CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_MULTIPLIER,
    CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT,
    CORE_CARD_REVIEWER_SOURCE_MANIFEST_SHA256,
    core_card_reviewer_workload_profile,
    _api_worst_case_request_reserve_cny,
    card_reviewer_comparison_metadata,
)
from memorive.exams.card_reviewer.successor import (
    CARD_REVIEWER_SUCCESSOR_SAMPLE_REVISION,
    CARD_REVIEWER_SUCCESSOR_GOLD_CORRECTION_REVISION,
    CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION,
    CARD_REVIEWER_SUCCESSOR_GOLD_CORRECTIONS,
    CARD_REVIEWER_SUCCESSOR_SAMPLE_SLOTS,
    CARD_REVIEWER_SUCCESSOR_FAMILY_ORDER,
    build_card_reviewer_successor_sample,
    score_card_reviewer_successor_form,
    mechanically_normalize_successor_candidate_pointers,
    execute_card_reviewer_successor_form,
)
from memorive.exams.card_reviewer.repair_prompt import (
    project_card_reviewer_repair_prompt,
)
from memorive.exams.card_reviewer.role_projection import (
    score_card_reviewer_role_projection,
)
from memorive.exams.card_reviewer.executor import (
    QualityCardReviewerExamExecutor,
)
from memorive.exams.card_reviewer.successor_executor import (
    QualityCardReviewerExamSuccessorExecutor,
)
