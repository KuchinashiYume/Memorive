"""Public-safe, Gold-free per-candidate ranking trace."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .candidate_features import canonical_sha256, candidate_identity


_FORBIDDEN_GOLD_KEYS = {
    "labels",
    "critical_positive",
    "supporting_positive",
    "protected_positive",
    "hard_negative",
    "unjudged",
    "relevance_grade",
    "expected",
}


def _assert_gold_free(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in _FORBIDDEN_GOLD_KEYS:
                raise ValueError("TRACE_GOLD_FIELD_FORBIDDEN")
            _assert_gold_free(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_gold_free(child)


def build_trace(
    candidate: Mapping[str, Any],
    *,
    query_id: str,
    profile: str,
    final_rank: int,
    snapshot_sha256: str,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    if not query_id or final_rank < 1 or len(snapshot_sha256) != 64:
        raise ValueError("TRACE_BINDING_INVALID")
    trace = {
        "schema_version": "P06T13RetrievalCandidateTrace-v1",
        "query_id": query_id,
        "profile": profile,
        "candidate_id": candidate_identity(candidate),
        "paper_id": candidate.get("paper_id"),
        "chunk_id": candidate.get("chunk_id"),
        "source_id": candidate.get("source_id"),
        "base_channel": candidate.get("base_channel", "distance"),
        "base_score": candidate.get("distance"),
        "base_rank": candidate.get("base_rank"),
        "field_channels": candidate.get("channels", {}),
        "source_role": candidate.get("source_role"),
        "metadata_status": candidate.get("metadata_status", "unknown"),
        "calibration_action": candidate.get("calibration_action", "NOT_RUN"),
        "calibration_reason": candidate.get("calibration_reason"),
        "policy_sha256": candidate.get("policy_sha256"),
        "pre_rerank_rank": candidate.get("pre_rerank_rank"),
        "snapshot_sha256": snapshot_sha256,
        "rerank_score": candidate.get("rerank_score"),
        "rerank_rank": candidate.get("rerank_rank"),
        "rank_fusion_method": candidate.get("rank_fusion_method"),
        "rank_fusion_value": candidate.get("rank_fusion_value"),
        "final_rank": final_rank,
        "fallback_reason": fallback_reason,
        "source_identity_unchanged": True,
    }
    _assert_gold_free(trace)
    trace["trace_sha256"] = canonical_sha256(trace)
    return trace


def explain_result(traces: Sequence[Mapping[str, Any]], candidate_id: str) -> dict[str, Any]:
    matches = [dict(trace) for trace in traces if trace.get("candidate_id") == candidate_id]
    if len(matches) != 1:
        raise KeyError("TRACE_NOT_UNIQUE")
    _assert_gold_free(matches[0])
    return matches[0]
