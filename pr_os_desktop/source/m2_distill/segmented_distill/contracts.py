"""Strict contracts shared by the P06/T06 segmented-distillation path.

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


def validate_field_candidate(payload: Mapping[str, Any]) -> dict[str, Any]:
    assert_artifact_kind(ArtifactKind.CARD_FIELD_CANDIDATE, payload)
    exact_keys(
        payload,
        required={
            "schema_version",
            "artifact_kind",
            "paper_id",
            "field_path",
            "value",
            "fact_record_ids",
            "source_refs",
            "composition_mode",
            "content_hash",
        },
        label="CardFieldCandidate",
    )
    text(payload["schema_version"], "schema_version")
    text(payload["paper_id"], "paper_id")
    text(payload["field_path"], "field_path")
    if payload["composition_mode"] != "DETERMINISTIC":
        raise SegmentedDistillContractError(
            "FIELD_COMPOSITION_MODE_FORBIDDEN",
            "only deterministic field composition is enabled",
        )
    fact_ids = payload["fact_record_ids"]
    refs = payload["source_refs"]
    if not isinstance(fact_ids, list) or not fact_ids or len(fact_ids) != len(set(fact_ids)):
        raise SegmentedDistillContractError(
            "FIELD_FACT_SET_INVALID", "fact_record_ids must be non-empty and unique"
        )
    if not isinstance(refs, list) or not refs:
        raise SegmentedDistillContractError(
            "FIELD_SOURCE_REFS_MISSING", "source_refs must be non-empty"
        )
    body = {key: value for key, value in payload.items() if key != "content_hash"}
    if payload["content_hash"] != canonical_hash(body):
        raise SegmentedDistillContractError(
            "FIELD_CONTENT_HASH_MISMATCH", "field candidate hash mismatch"
        )
    return dict(payload)


Validator = Callable[[Mapping[str, Any]], Any]


def dispatch_artifact_validation(
    *,
    expected_artifact_kind: ArtifactKind | str,
    payload: Mapping[str, Any],
    shard_validator: Validator | None = None,
    field_validator: Validator | None = None,
    full_card_validator: Validator | None = None,
) -> Any:
    """Dispatch only from the frozen call contract, never from payload shape."""

    kind = assert_artifact_kind(expected_artifact_kind, payload)
    validators: dict[ArtifactKind, Validator | None] = {
        ArtifactKind.SEGMENT_FACT_SHARD: shard_validator,
        ArtifactKind.CARD_FIELD_CANDIDATE: field_validator or validate_field_candidate,
        ArtifactKind.CARD_CANDIDATE: full_card_validator,
    }
    validator = validators[kind]
    if validator is None:
        code = (
            "FULL_CARD_VALIDATOR_REQUIRED"
            if kind is ArtifactKind.CARD_CANDIDATE
            else "ARTIFACT_VALIDATOR_REQUIRED"
        )
        raise SegmentedDistillContractError(
            code, f"no validator bound for {kind.value}"
        )
    return validator(payload)


__all__ = [
    "ArtifactKind",
    "SegmentedDistillContractError",
    "assert_artifact_kind",
    "canonical_hash",
    "canonical_json",
    "dispatch_artifact_validation",
    "exact_keys",
    "text",
    "validate_field_candidate",
]
