"""QualityNewEmbeddingLegacyExamExecutor: the earlier form of the new embedding exam.
"""
from __future__ import annotations

from copy import deepcopy
import statistics
from typing import Any, Mapping
from memorive.exams.common import EXECUTOR_REF, _sha256
from memorive.exams.embedding.executor import QualityEmbeddingExamExecutor


class QualityNewEmbeddingLegacyExamExecutor(QualityEmbeddingExamExecutor):
    """Keep the full 240-corpus exam while halving order-preserving requests."""

    EXECUTOR_REF = "Quality_NEW_Quality_EMBEDDING_EXECUTOR_V1"
    MODE = "LIVE_Quality_NEW_EMBEDDING_FULL_PACK_BATCHED_REFERENCE_REGRESSION"
    CORPORA_PER_MODEL_REQUEST = 10
    INPUTS_PER_MODEL_REQUEST = CORPORA_PER_MODEL_REQUEST * QualityEmbeddingExamExecutor.INPUTS_PER_CORPUS
    PHYSICAL_MODEL_CALLS = 24
    SCORING_PROJECTION_REVISION = (
        "Quality_NEW_EMBEDDING_ORDER_PRESERVING_BATCH_V1"
    )
    BATCH_EQUIVALENCE_REVISION = (
        "Quality_NEW_EMBEDDING_TEN_CORPUS_ORDER_PRESERVING_BATCH_V1"
    )

    def _embedding_diagnostics_for_scores(
        self,
        scores: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        forms: dict[str, Any] = {}
        task_count = 0
        critical_miss_count = 0
        for form_id in ("A", "B"):
            source = scores[form_id]
            task_scores = source.get("task_scores")
            observed_task_count = (
                len(task_scores) if isinstance(task_scores, list) else None
            )
            if observed_task_count is not None:
                task_count += observed_task_count
            form_critical = int(source.get("critical_miss_count") or 0)
            critical_miss_count += form_critical
            forms[form_id] = {
                "score": float(source["score"]),
                "component_raw_scores": deepcopy(
                    dict(source.get("component_raw_scores") or {})
                ),
                "family_means": deepcopy(
                    dict(source.get("family_means") or {})
                ),
                "difficulty_means": deepcopy(
                    dict(source.get("difficulty_means") or {})
                ),
                "critical_miss_count": form_critical,
                "task_count": observed_task_count,
            }
        mean = statistics.fmean(forms[form_id]["score"] for form_id in ("A", "B"))
        diagnostic: dict[str, Any] = {
            "schema_version": "QualityNewEmbeddingLiveDiagnostic-v1",
            "scoring_protocol_revision": "embedding_text_scoring_r0.2",
            "scoring_projection_revision": self.SCORING_PROJECTION_REVISION,
            "score_views": {
                "primary": {
                    "kind": "RAW_DEDUCTION_SCORE",
                    "mean": round(mean, 6),
                    "qualification_eligible": False,
                },
                "post_hoc_selection": {
                    "status": "NOT_APPLIED_TO_NEW_MODEL_RUN",
                    "qualification_eligible": False,
                },
            },
            "forms": forms,
            "critical_misses": {
                "count": critical_miss_count,
                "task_count": task_count or None,
                "rate_percent": (
                    round(critical_miss_count * 100.0 / task_count, 6)
                    if task_count
                    else None
                ),
                "semantics": "DIAGNOSTIC_CRITICAL_MISS_NOT_ADMISSION_FAIL",
            },
            "selection_contract": {
                "single_aggregate_is_sufficient_for_selection": False,
                "repeatability_and_operational_profile_required": True,
                "meaningful_pairwise_separation_must_be_demonstrated": True,
                "quality_threshold_defined": False,
                "model_failure_established": False,
            },
            "execution_provenance": {
                "physical_model_call_target": self.PHYSICAL_MODEL_CALLS,
                "corpora_per_model_request": self.CORPORA_PER_MODEL_REQUEST,
                "logical_corpus_count": self.LOGICAL_CORPUS_CALLS,
                "historical_quality_call_count_relabelled": False,
            },
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
        }
        diagnostic["diagnostic_sha256"] = _sha256(diagnostic)
        return diagnostic
