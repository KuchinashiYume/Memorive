"""RETRIEVAL-CALIBRATION retrieval-calibration qualification facade."""

from memorive.research.retrieval.retrieval_calibration.candidate_features import (
    build_reranker_provenance,
    candidate_identity,
    enrich_candidates,
    resolve_feature_envelope,
)
from memorive.research.retrieval.retrieval_calibration.candidate_trace import build_trace, explain_result
from memorive.research.retrieval.retrieval_calibration.candidate_union_adapter import stable_candidate_union
from memorive.research.retrieval.retrieval_calibration.fallback import apply_fallback
from memorive.research.retrieval.retrieval_calibration.pre_rerank_snapshot import create_snapshot, restore_snapshot, verify_snapshot
from memorive.research.retrieval.retrieval_calibration.profile_compare import compare_profiles, run_profile
from memorive.research.retrieval.retrieval_calibration.qualification import (
    MULTIMODAL_LISTWISE_SUBJECT,
    bind_listwise_batches,
    bind_score_batches,
    prepare_reranker_batches,
    run_qualification,
)

__all__ = [
    "apply_fallback",
    "build_trace",
    "build_reranker_provenance",
    "candidate_identity",
    "bind_listwise_batches",
    "bind_score_batches",
    "MULTIMODAL_LISTWISE_SUBJECT",
    "compare_profiles",
    "create_snapshot",
    "enrich_candidates",
    "explain_result",
    "resolve_feature_envelope",
    "prepare_reranker_batches",
    "restore_snapshot",
    "run_profile",
    "run_qualification",
    "stable_candidate_union",
    "verify_snapshot",
]
