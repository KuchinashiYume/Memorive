"""Strict contracts shared by the LONG-DOCUMENT segmented-distillation path.

The module deliberately does not import the legacy whole-card validator.  A caller
must bind that validator explicitly when the expected artifact is a final Card.
This keeps a valid segment shard from being mistaken for a partial Card while
preserving the legacy Card gate at the final assembly boundary.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Callable, Mapping


class SegmentedDistillContractError(ValueError):
    """Fail-closed contract violation with a stable reason code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ArtifactKind(str, Enum):
    SEGMENT_FACT_SHARD = "segment_fact_shard"
    CARD_FIELD_CANDIDATE = "card_field_candidate"
    CARD_CANDIDATE = "card_candidate"


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest().upper()


def text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SegmentedDistillContractError(
            "NON_EMPTY_TEXT_REQUIRED", f"{field} must be non-empty text"
        )
    return value.strip()


def exact_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    label: str,
) -> None:
    if not isinstance(value, Mapping):
        raise SegmentedDistillContractError(
            "OBJECT_REQUIRED", f"{label} must be an object"
        )
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise SegmentedDistillContractError(
            "REQUIRED_FIELDS_MISSING",
            f"{label} missing fields: {sorted(missing)}",
        )
    if unknown:
        raise SegmentedDistillContractError(
            "UNKNOWN_FIELDS_FORBIDDEN",
            f"{label} contains unknown fields: {sorted(unknown)}",
        )


def assert_artifact_kind(
    expected: ArtifactKind | str, payload: Mapping[str, Any]
) -> ArtifactKind:
    try:
        expected_kind = ArtifactKind(expected)
    except ValueError as exc:
        raise SegmentedDistillContractError(
            "EXPECTED_ARTIFACT_KIND_INVALID", str(expected)
        ) from exc
    observed = payload.get("artifact_kind")
    if observed != expected_kind.value:
        raise SegmentedDistillContractError(
            "ARTIFACT_KIND_MISMATCH",
            f"expected {expected_kind.value}, observed {observed!r}",
        )
    return expected_kind


Validator = Callable[[Mapping[str, Any]], Any]


__all__ = [
    "ArtifactKind",
    "SegmentedDistillContractError",
    "assert_artifact_kind",
    "canonical_hash",
    "canonical_json",
    "exact_keys",
    "text",
]
