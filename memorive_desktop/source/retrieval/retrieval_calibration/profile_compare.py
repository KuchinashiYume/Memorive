"""Four-profile shadow comparison with strict score/index alignment."""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping, Sequence

from retrieval_weighting.retrieval_calibration_route_profile import RetrievalProfile

from .candidate_trace import build_trace
from .pre_rerank_snapshot import create_snapshot


def _validate_score_rows(
    score_rows: Sequence[Mapping[str, Any]], expected_count: int
) -> list[float]:
    if len(score_rows) != expected_count:
        raise ValueError("SCORE_COUNT_MISMATCH")
    indices: set[int] = set()
    scores: dict[int, float] = {}
    for row in score_rows:
        index = row.get("index")
        score = row.get("score")
        if not isinstance(index, int) or isinstance(index, bool) or index in indices:
            raise ValueError("SCORE_INDEX_MISMATCH")
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score):
            raise ValueError("SCORE_NONFINITE")
        indices.add(index)
        scores[index] = float(score)
    if indices != set(range(expected_count)):
        raise ValueError("SCORE_INDEX_MISMATCH")
    return [scores[index] for index in range(expected_count)]


def _rerank(
    candidates: Sequence[Mapping[str, Any]],
    score_rows: Sequence[Mapping[str, Any]],
    *,
    fuse_structure_delta: bool,
) -> list[dict[str, Any]]:
    scores = _validate_score_rows(score_rows, len(candidates))
    rows = [deepcopy(dict(candidate)) for candidate in candidates]
    for index, (row, score) in enumerate(zip(rows, scores)):
        row["original_index"] = index
        row["rerank_score"] = score
    rows.sort(
        key=lambda row: (
            -row["rerank_score"],
            row.get("pre_rerank_rank", row.get("base_rank", row["original_index"] + 1)),
            row["candidate_id"],
        )
    )
    for rank, row in enumerate(rows, 1):
        row["rerank_rank"] = rank
        structure_delta = row.get("calibration_rank_delta", 0) if fuse_structure_delta else 0
        if not isinstance(structure_delta, int) or isinstance(structure_delta, bool) or structure_delta < 0:
            raise ValueError("CALIBRATION_RANK_DELTA_INVALID")
        row["rank_fusion_method"] = (
            "RERANK_RANK_PLUS_STRUCTURE_DELTA"
            if fuse_structure_delta
            else "RERANK_RANK_ONLY"
        )
        row["rank_fusion_value"] = rank + structure_delta
    rows.sort(
        key=lambda row: (
            row["rank_fusion_value"],
            row["rerank_rank"],
            row.get("pre_rerank_rank", row.get("base_rank", row["original_index"] + 1)),
            row["candidate_id"],
        )
    )
    return rows


def _apply_listwise_order(
    candidates: Sequence[Mapping[str, Any]],
    ranked_candidate_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """Apply a complete generative listwise order without inventing scores."""

    rows = [deepcopy(dict(candidate)) for candidate in candidates]
    expected_ids = [row.get("candidate_id") for row in rows]
    if (
        isinstance(ranked_candidate_ids, (str, bytes))
        or len(ranked_candidate_ids) != len(expected_ids)
        or len(set(ranked_candidate_ids)) != len(ranked_candidate_ids)
        or set(ranked_candidate_ids) != set(expected_ids)
    ):
        raise ValueError("LISTWISE_CANDIDATE_EXACT_SET_MISMATCH")
    by_id = {row["candidate_id"]: row for row in rows}
    ordered = [by_id[candidate_id] for candidate_id in ranked_candidate_ids]
    for rank, row in enumerate(ordered, 1):
        row["rerank_score"] = None
        row["rerank_rank"] = rank
        row["rank_fusion_method"] = "GENERATIVE_LISTWISE_DIRECT"
        row["rank_fusion_value"] = rank
    return ordered


def run_profile(
    query: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    profile: str | RetrievalProfile,
    *,
    policy: Any,
    score_rows: Sequence[Mapping[str, Any]] | None = None,
    ranked_candidate_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    selected = RetrievalProfile(profile)
    query_id = query.get("query_id")
    query_intent = query.get("query_intent")
    if not isinstance(query_id, str) or not query_id:
        raise ValueError("QUERY_ID_INVALID")
    baseline = [deepcopy(dict(row)) for row in candidates]
    for rank, row in enumerate(baseline, 1):
        row.setdefault("base_rank", rank)
        row.setdefault("rerank_score", None)
    structure = selected in {
        RetrievalProfile.STRUCTURE_ONLY_SHADOW,
        RetrievalProfile.STRUCTURE_PLUS_RERANK_SHADOW,
    }
    reranker = selected in {
        RetrievalProfile.RERANK_ONLY_SHADOW,
        RetrievalProfile.STRUCTURE_PLUS_RERANK_SHADOW,
    }
    if structure:
        ordered = policy.calibrate(query_intent, baseline)
    else:
        ordered = baseline
    snapshot = create_snapshot(query_id, ordered, profile=selected.value)
    ordered = snapshot["candidates"]
    if reranker:
        if (score_rows is None) == (ranked_candidate_ids is None):
            raise ValueError("EXACTLY_ONE_RANKING_OUTPUT_REQUIRED")
        if ranked_candidate_ids is not None:
            ordered = _apply_listwise_order(ordered, ranked_candidate_ids)
        else:
            ordered = _rerank(
                ordered,
                score_rows or (),
                fuse_structure_delta=(
                    selected is RetrievalProfile.STRUCTURE_PLUS_RERANK_SHADOW
                ),
            )
    elif score_rows is not None or ranked_candidate_ids is not None:
        raise ValueError("OFF_OR_STRUCTURE_RANKING_OUTPUT_FORBIDDEN")
    traces = []
    for final_rank, row in enumerate(ordered, 1):
        row["final_rank"] = final_rank
        traces.append(
            build_trace(
                row,
                query_id=query_id,
                profile=selected.value,
                final_rank=final_rank,
                snapshot_sha256=snapshot["snapshot_sha256"],
            )
        )
    return {
        "profile": selected.value,
        "query_id": query_id,
        "candidates": ordered,
        "candidate_ids": [row["candidate_id"] for row in ordered],
        "snapshot": snapshot,
        "traces": traces,
        "production_eligible": False,
    }


def compare_profiles(
    query: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    policy: Any,
    rerank_only_scores: Sequence[Mapping[str, Any]] | None = None,
    combined_scores: Sequence[Mapping[str, Any]] | None = None,
    rerank_only_candidate_ids: Sequence[str] | None = None,
    combined_candidate_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    outputs = {}
    for profile in RetrievalProfile:
        scores = None
        ranked_ids = None
        if profile is RetrievalProfile.RERANK_ONLY_SHADOW:
            scores = rerank_only_scores
            ranked_ids = rerank_only_candidate_ids
        elif profile is RetrievalProfile.STRUCTURE_PLUS_RERANK_SHADOW:
            scores = combined_scores
            ranked_ids = combined_candidate_ids
        outputs[profile.value] = run_profile(
            query,
            candidates,
            profile,
            policy=policy,
            score_rows=scores,
            ranked_candidate_ids=ranked_ids,
        )
    return outputs
