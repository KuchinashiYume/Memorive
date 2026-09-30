"""Profile-aware, local-only capacity routing for LONG-DOCUMENT."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import floor
from typing import Any, Mapping, Sequence

from .contracts import SegmentedDistillContractError
from .topology import _sentence_ranges


class CapacityRoute(str, Enum):
    LEGACY_SINGLE_PASS = "LEGACY_SINGLE_PASS"
    SEGMENTED_LONG_DOCUMENT = "SEGMENTED_LONG_DOCUMENT"
    BLOCKED_ROUTE_UNCERTAIN = "BLOCKED_ROUTE_UNCERTAIN"


@dataclass(frozen=True)
class CapacityProfile:
    profile_id: str
    context_window_tokens: int
    max_output_tokens: int
    prompt_reserve_tokens: int
    reasoning_reserve_tokens: int
    safety_margin: float
    segment_core_token_limit: int
    estimator_revision: str

    def validate(self) -> None:
        integer_fields = {
            "context_window_tokens": self.context_window_tokens,
            "max_output_tokens": self.max_output_tokens,
            "prompt_reserve_tokens": self.prompt_reserve_tokens,
            "reasoning_reserve_tokens": self.reasoning_reserve_tokens,
            "segment_core_token_limit": self.segment_core_token_limit,
        }
        if not self.profile_id or not self.estimator_revision:
            raise SegmentedDistillContractError(
                "CAPACITY_PROFILE_IDENTITY_MISSING",
                "profile_id and estimator_revision are required",
            )
        if any(not isinstance(value, int) or value < 0 for value in integer_fields.values()):
            raise SegmentedDistillContractError(
                "CAPACITY_PROFILE_NUMBER_INVALID", str(integer_fields)
            )
        if self.context_window_tokens < 1 or self.segment_core_token_limit < 1:
            raise SegmentedDistillContractError(
                "CAPACITY_PROFILE_LIMIT_INVALID", "context and segment limits must be positive"
            )
        if not 0 < self.safety_margin <= 1:
            raise SegmentedDistillContractError(
                "CAPACITY_SAFETY_MARGIN_INVALID", str(self.safety_margin)
            )


@dataclass(frozen=True)
class CapacityDecision:
    route: CapacityRoute
    profile_id: str
    estimated_input_tokens: int | None
    safe_context_tokens: int
    reserved_tokens: int
    available_input_tokens: int
    reasons: tuple[str, ...]
    force_segment_comparison: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route.value,
            "profile_id": self.profile_id,
            "estimated_input_tokens": self.estimated_input_tokens,
            "safe_context_tokens": self.safe_context_tokens,
            "reserved_tokens": self.reserved_tokens,
            "available_input_tokens": self.available_input_tokens,
            "reasons": list(self.reasons),
            "force_segment_comparison": self.force_segment_comparison,
        }


def _manifest_chunks(source_manifest: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    chunks = source_manifest.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise SegmentedDistillContractError(
            "SOURCE_MANIFEST_CHUNKS_INVALID", "ordered chunks are required"
        )
    sequence = [item.get("sequence_index") for item in chunks]
    if sequence != list(range(1, len(chunks) + 1)):
        raise SegmentedDistillContractError(
            "SOURCE_ORDER_UNCERTAIN", "sequence_index must be contiguous and ordered"
        )
    if len({item.get("chunk_id") for item in chunks}) != len(chunks):
        raise SegmentedDistillContractError(
            "SOURCE_CHUNK_ID_NOT_UNIQUE", "chunk ids must be unique"
        )
    return chunks


def _can_split_at_sentence_boundary(chunk: Mapping[str, Any], limit: int) -> bool:
    value = chunk.get("text")
    if not isinstance(value, str):
        return False
    ranges = _sentence_ranges(value)
    if len(ranges) < 2:
        return False
    total_chars = max(1, len(value))
    return all(
        max(1, round(chunk["estimated_tokens"] * (end - start) / total_chars))
        <= limit
        for start, end in ranges
    )


def route_document(
    source_manifest: Mapping[str, Any],
    profile: CapacityProfile,
    *,
    force_segment: bool = False,
    comparison_mode: bool = False,
) -> CapacityDecision:
    """Return a deterministic three-way route without reading credentials."""

    profile.validate()
    safe_context = floor(profile.context_window_tokens * profile.safety_margin)
    reserved = (
        profile.max_output_tokens
        + profile.prompt_reserve_tokens
        + profile.reasoning_reserve_tokens
    )
    available = safe_context - reserved
    if force_segment and not comparison_mode:
        raise SegmentedDistillContractError(
            "FORCE_SEGMENT_PRODUCTION_FORBIDDEN",
            "force_segment is available only for explicit comparison mode",
        )
    try:
        chunks = _manifest_chunks(source_manifest)
    except SegmentedDistillContractError as exc:
        return CapacityDecision(
            CapacityRoute.BLOCKED_ROUTE_UNCERTAIN,
            profile.profile_id,
            None,
            safe_context,
            reserved,
            available,
            (exc.code,),
            force_segment and comparison_mode,
        )
    if source_manifest.get("estimator_revision") != profile.estimator_revision:
        return CapacityDecision(
            CapacityRoute.BLOCKED_ROUTE_UNCERTAIN,
            profile.profile_id,
            None,
            safe_context,
            reserved,
            available,
            ("TOKEN_ESTIMATOR_REVISION_MISMATCH",),
            force_segment and comparison_mode,
        )
    estimates = [item.get("estimated_tokens") for item in chunks]
    if any(not isinstance(value, int) or value < 1 for value in estimates):
        return CapacityDecision(
            CapacityRoute.BLOCKED_ROUTE_UNCERTAIN,
            profile.profile_id,
            None,
            safe_context,
            reserved,
            available,
            ("TOKEN_ESTIMATE_MISSING_OR_INVALID",),
            force_segment and comparison_mode,
        )
    total = sum(estimates)
    if available < 1:
        route = CapacityRoute.BLOCKED_ROUTE_UNCERTAIN
        reasons = ("PROFILE_RESERVE_EXHAUSTS_CONTEXT",)
    elif max(estimates) > profile.segment_core_token_limit:
        oversized = [
            item
            for item in chunks
            if item["estimated_tokens"] > profile.segment_core_token_limit
        ]
        if all(
            _can_split_at_sentence_boundary(item, profile.segment_core_token_limit)
            for item in oversized
        ):
            route = CapacityRoute.SEGMENTED_LONG_DOCUMENT
            reasons = ("OVERSIZED_CHUNK_HAS_SAFE_DETERMINISTIC_SPLIT",)
        else:
            route = CapacityRoute.BLOCKED_ROUTE_UNCERTAIN
            reasons = ("SINGLE_CHUNK_CAPACITY_UNRESOLVED",)
    elif force_segment:
        route = CapacityRoute.SEGMENTED_LONG_DOCUMENT
        reasons = ("EXPLICIT_A_COMPARISON_FORCE_SEGMENT",)
    elif total <= available:
        route = CapacityRoute.LEGACY_SINGLE_PASS
        reasons = ("WHOLE_DOCUMENT_WITHIN_FROZEN_SAFE_BOUNDARY",)
    else:
        route = CapacityRoute.SEGMENTED_LONG_DOCUMENT
        reasons = ("WHOLE_DOCUMENT_EXCEEDS_SAFE_BOUNDARY_SEGMENTS_FIT",)
    return CapacityDecision(
        route,
        profile.profile_id,
        total,
        safe_context,
        reserved,
        available,
        reasons,
        force_segment and comparison_mode,
    )


__all__ = [
    "CapacityDecision",
    "CapacityProfile",
    "CapacityRoute",
    "route_document",
]
