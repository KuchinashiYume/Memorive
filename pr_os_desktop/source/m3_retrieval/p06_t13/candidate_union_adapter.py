"""Thin, read-only M3/M7 candidate-union adapter."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .candidate_features import canonical_sha256, candidate_identity


def stable_candidate_union(
    distance_candidates: Sequence[Mapping[str, Any]],
    field_candidates: Sequence[Mapping[str, Any]] | None,
    *,
    allowed_paper_ids: Sequence[str],
    scope: str,
    field_expected_min: int = 0,
) -> dict[str, Any]:
    if scope not in {"single_paper", "cross_paper"}:
        raise ValueError("SCOPE_INVALID")
    allowed = tuple(dict.fromkeys(allowed_paper_ids))
    if not allowed or (scope == "single_paper" and len(allowed) != 1):
        raise ValueError("ALLOWED_SCOPE_INVALID")
    field_available = field_candidates is not None
    field_rows = list(field_candidates or ())
    union: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    scope_rejections: list[str] = []

    def consume(rows: Sequence[Mapping[str, Any]], channel: str) -> None:
        for rank, raw in enumerate(rows, 1):
            row = deepcopy(dict(raw))
            identity = candidate_identity(row)
            paper_id = row.get("paper_id")
            if paper_id not in allowed:
                scope_rejections.append(identity)
                continue
            channels = {
                channel: {
                    "rank": rank,
                    "score": row.get("distance") if channel == "distance" else row.get("field_score"),
                }
            }
            if identity not in union:
                row["candidate_id"] = identity
                row["channels"] = channels
                row["base_channel"] = channel
                row["base_rank"] = rank if channel == "distance" else len(distance_candidates) + rank
                union[identity] = row
                order.append(identity)
            else:
                union[identity].setdefault("channels", {}).update(channels)

    consume(distance_candidates, "distance")
    consume(field_rows, "field")
    candidates = [union[identity] for identity in order]
    receipt = {
        "schema_version": "P06T13CandidateUnionReceipt-v1",
        "scope": scope,
        "allowed_paper_ids": list(allowed),
        "distance_count": len(distance_candidates),
        "field_count": len(field_rows),
        "union_count": len(candidates),
        "scope_rejection_count": len(scope_rejections),
        "scope_rejected_candidate_ids": scope_rejections,
        "field_route_available": field_available,
        "field_route_underfilled": (not field_available) or len(field_rows) < field_expected_min,
        "underfill_reason": (
            "FIELD_ROUTE_UNAVAILABLE"
            if not field_available
            else "FIELD_ROUTE_UNDERFILLED"
            if len(field_rows) < field_expected_min
            else None
        ),
        "identity_order_sha256": canonical_sha256(order),
        "production_index_writes": 0,
    }
    return {"candidates": candidates, "receipt": receipt}
