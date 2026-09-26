"""Qualification-only Installation route profiles and fail-closed fallback decisions."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class RetrievalProfile(str, Enum):
    BASELINE_DISTANCE_OFF = "BASELINE_DISTANCE_OFF"
    STRUCTURE_ONLY_SHADOW = "STRUCTURE_ONLY_SHADOW"
    RERANK_ONLY_SHADOW = "RERANK_ONLY_SHADOW"
    STRUCTURE_PLUS_RERANK_SHADOW = "STRUCTURE_PLUS_RERANK_SHADOW"


@dataclass(frozen=True)
class RouteDecision:
    profile: str
    field_route: bool
    block_role_calibration: bool
    local_reranker: bool
    fallback: str | None
    degraded: bool
    fail_closed: bool
    reason_codes: tuple[str, ...]
    production_eligible: bool = False
    cloud_fallback: bool = False


def resolve_route(
    profile: str | RetrievalProfile,
    *,
    field_route_ready: bool,
    local_reranker_ready: bool,
    snapshot_valid: bool = True,
) -> RouteDecision:
    try:
        selected = RetrievalProfile(profile)
    except ValueError as exc:
        raise ValueError("PROFILE_INVALID") from exc
    wants_structure = selected in {
        RetrievalProfile.STRUCTURE_ONLY_SHADOW,
        RetrievalProfile.STRUCTURE_PLUS_RERANK_SHADOW,
    }
    wants_reranker = selected in {
        RetrievalProfile.RERANK_ONLY_SHADOW,
        RetrievalProfile.STRUCTURE_PLUS_RERANK_SHADOW,
    }
    reasons: list[str] = []
    fallback: str | None = None
    degraded = False
    fail_closed = False
    structure_enabled = wants_structure and field_route_ready
    reranker_enabled = wants_reranker and local_reranker_ready
    if wants_structure and not field_route_ready:
        degraded = True
        fallback = "DISTANCE_CANDIDATE_POOL"
        reasons.append("FIELD_ROUTE_UNAVAILABLE_OR_UNDERFILLED")
    if wants_reranker and not local_reranker_ready:
        if snapshot_valid:
            degraded = True
            fallback = "EXACT_PRE_RERANK_SNAPSHOT"
            reasons.append("LOCAL_RERANKER_UNAVAILABLE")
        else:
            fail_closed = True
            fallback = None
            reasons.append("SNAPSHOT_MISMATCH")
    if selected is RetrievalProfile.BASELINE_DISTANCE_OFF:
        reasons.append("OFF_IS_CURRENT_BASELINE")
    return RouteDecision(
        profile=selected.value,
        field_route=structure_enabled,
        block_role_calibration=structure_enabled,
        local_reranker=reranker_enabled,
        fallback=fallback,
        degraded=degraded,
        fail_closed=fail_closed,
        reason_codes=tuple(reasons),
    )


def route_matrix(
    *, field_route_ready: bool, local_reranker_ready: bool, snapshot_valid: bool = True
) -> dict[str, dict[str, Any]]:
    return {
        profile.value: asdict(
            resolve_route(
                profile,
                field_route_ready=field_route_ready,
                local_reranker_ready=local_reranker_ready,
                snapshot_valid=snapshot_valid,
            )
        )
        for profile in RetrievalProfile
    }
