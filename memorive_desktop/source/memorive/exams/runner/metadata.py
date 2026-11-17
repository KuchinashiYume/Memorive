"""The metadata fields saved workflow exam scores carry, by revision, and which workflow node each
exam role binds to.
"""
from __future__ import annotations


WORKFLOW_EXAM_SCORE_METADATA_FIELDS_V4 = (
    "score_exact",
    "score_semantics",
    "qualification_verdict",
    "exam_category_id",
    "role_id",
    "reference_pack_id",
    "reference_pack_revision",
    "reference_pack_sha256",
    "scoring_protocol_revision",
    "scoring_protocol_sha256",
    "scoring_projection_revision",
    "comparison_binding_sha256",
    "comparison_cohort_id",
    "horizontal_comparison_eligible",
    "horizontal_comparison_allowed_only_with_same_cohort",
    "cross_category_comparison_forbidden",
    "global_ranking_forbidden",
)


WORKFLOW_EXAM_OPERATIONAL_METADATA_FIELDS_V5 = (
    "operational_eligibility_verdict",
    "operational_policy_revision",
    "operational_safety_gate",
    "safety_blocking_residual_count",
    "calibration_residual_count",
)


WORKFLOW_EXAM_REPAIR_METADATA_FIELDS_V6 = (
    "exact_case_repair_rate",
    "case_score_deficit_recovery_rate",
    "repair_metadata_collision_suspect_count",
    "collision_adjusted_repair_diagnostic_rate",
)


WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V7 = (
    "cache_reuse_status",
    "cache_provenance_kind",
    "cache_binding_sha256",
    "executor_ref",
    "sample_revision",
    "repair_prompt_projection_revision",
    "pointer_normalization_revision",
    "subject_transport_projection_revision",
    "core_closure_verdict",
    "core_closure_score",
    "semantic_repair_rounds",
    "failed_exam_item_count",
    "series_aggregate_method",
    "series_slots",
)


WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V8 = (
    "execution_outcome",
    "scorability",
    "quality_verdict",
    "terminal_disposition",
    "executor_binding_sha256",
    "executor_binding_kind",
    "sample_manifest_sha256",
    "cache_binding",
    "category_score_is_descriptive",
    "physical_model_calls",
    "token_usage",
    "provider_received_gold",
    "local_reference_pack_embedded",
)


WORKFLOW_EXAM_Quality_NEW_METADATA_FIELDS = (
    "quality_new",
    "language_distribution_status",
    "production_workload_score_established",
    "cache_reuse_requires_executor_ref_match",
    "cache_reuse_scope",
    "model_fail_verdict",
    "model_fail_established",
    "quality_threshold_defined",
    "primary_score_track",
    "calibration_series",
    "calibration_series_aggregate_method",
    "role_adequacy_policy_revision",
)


WORKFLOW_EXAM_SCORE_METADATA_FIELDS = (
    WORKFLOW_EXAM_SCORE_METADATA_FIELDS_V4
    + WORKFLOW_EXAM_OPERATIONAL_METADATA_FIELDS_V5
    + WORKFLOW_EXAM_REPAIR_METADATA_FIELDS_V6
    + WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V7
    + WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V8
    + WORKFLOW_EXAM_Quality_NEW_METADATA_FIELDS
)


WORKFLOW_EXAM_ROLE_NODE_BINDINGS = {
    "CARD_DISTILLER": frozenset({"card_distill", "ingest"}),
    "CARD_REVIEWER": frozenset({"transport_review"}),
    "ANALYSIS_PRIMARY": frozenset({"analysis"}),
    "ANALYSIS_REVIEWER": frozenset({"judgment_review"}),
    "OCR_PAGE": frozenset({"ingest"}),
    "EMBEDDING_TEXT": frozenset({"chunk_embedding"}),
    "RERANKER_TEXT": frozenset({"context_pack"}),
}
