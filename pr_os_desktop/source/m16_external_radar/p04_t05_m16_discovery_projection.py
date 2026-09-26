"""P04/T05 sandbox candidate: project accepted T03/T04 facts for M17.

This module is deliberately pure and offline. It does not perform discovery,
identity resolution, relation inference, ranking, persistence, or network I/O.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any


SCHEMA_VERSION = "p04.t05.discovery-candidate-fact.0.1"
ZERO_SIDE_EFFECTS = {
    "candidate_mutations": 0,
    "comparison_mutations": 0,
    "production_writes": 0,
}
REQUIRED_STATE_AXES = (
    "identity_status",
    "access_status",
    "decision_status",
    "candidate_card_status",
    "promotion_status",
)
REQUIRED_METADATA = {
    "source_id",
    "source_available",
    "authors",
    "institutions",
    "journal_id",
    "primary_direction_id",
    "direction_signals",
    "bridge_direction_refs",
    "horizon_anchor_refs",
    "coverage_gap_refs",
    "evidence_completeness",
    "manifestation_fitness",
    "timeliness",
    "access_posture",
    "evidence_refs",
}
T04_ZERO_SIDE_EFFECT_KEYS = {
    "network_calls",
    "external_source_calls",
    "external_api_calls",
    "model_calls",
    "tokens",
    "cost_cny",
    "production_library_reads",
    "production_library_writes",
    "production_chroma_queries",
    "production_chroma_writes",
    "m13_reads",
    "m13_writes",
    "candidate_card_writes",
    "card_analysis_reads",
    "card_analysis_writes",
    "formal_writes",
}


class DiscoveryProjectionError(ValueError):
    """Fail-closed input or authority error with a stable code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest().upper()


def seal_object(value: Mapping[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(dict(value))
    body.pop("content_hash", None)
    body["content_hash"] = sha256_json(body)
    return body


def _assert_sealed(value: Mapping[str, Any], code: str) -> None:
    body = copy.deepcopy(dict(value))
    recorded = body.pop("content_hash", None)
    if not isinstance(recorded, str) or recorded != sha256_json(body):
        raise DiscoveryProjectionError(code, "content_hash mismatch")


def _sorted_unique_strings(value: Any, field: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise DiscoveryProjectionError("RANKING_METADATA_INVALID", field)
    if any(not isinstance(item, str) or not item for item in value):
        raise DiscoveryProjectionError("RANKING_METADATA_INVALID", field)
    if len(set(value)) != len(value):
        raise DiscoveryProjectionError("RANKING_METADATA_INVALID", f"{field} not unique")
    return sorted(value)


def _normalize_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    if set(metadata) != REQUIRED_METADATA:
        missing = sorted(REQUIRED_METADATA - set(metadata))
        extra = sorted(set(metadata) - REQUIRED_METADATA)
        raise DiscoveryProjectionError(
            "RANKING_METADATA_SHAPE_INVALID", f"missing={missing};extra={extra}"
        )
    if not isinstance(metadata["source_id"], str) or not metadata["source_id"]:
        raise DiscoveryProjectionError("RANKING_METADATA_INVALID", "source_id")
    if not isinstance(metadata["source_available"], bool):
        raise DiscoveryProjectionError("RANKING_METADATA_INVALID", "source_available")
    if not isinstance(metadata["journal_id"], str) or not metadata["journal_id"]:
        raise DiscoveryProjectionError("RANKING_METADATA_INVALID", "journal_id")
    if not isinstance(metadata["primary_direction_id"], str) or not metadata["primary_direction_id"]:
        raise DiscoveryProjectionError("RANKING_METADATA_INVALID", "primary_direction_id")
    for field in ("evidence_completeness", "manifestation_fitness", "timeliness"):
        if type(metadata[field]) is not int or not 0 <= metadata[field] <= 3:
            raise DiscoveryProjectionError("RANKING_METADATA_INVALID", field)
    if metadata["access_posture"] not in {"OPEN", "METADATA_ONLY", "UNKNOWN"}:
        raise DiscoveryProjectionError("RANKING_METADATA_INVALID", "access_posture")

    signals = metadata["direction_signals"]
    if not isinstance(signals, list) or not signals:
        raise DiscoveryProjectionError("RANKING_METADATA_INVALID", "direction_signals")
    normalized_signals: list[dict[str, str]] = []
    seen_signals: set[tuple[str, str, str]] = set()
    for signal in signals:
        if not isinstance(signal, Mapping) or set(signal) != {"direction_id", "match", "evidence_ref"}:
            raise DiscoveryProjectionError("RANKING_METADATA_INVALID", "direction_signal shape")
        if signal["match"] not in {"EXACT", "RELATED", "NONE"}:
            raise DiscoveryProjectionError("RANKING_METADATA_INVALID", "direction_signal match")
        key = (signal["direction_id"], signal["match"], signal["evidence_ref"])
        if any(not isinstance(item, str) or not item for item in key) or key in seen_signals:
            raise DiscoveryProjectionError("RANKING_METADATA_INVALID", "direction_signal identity")
        seen_signals.add(key)
        normalized_signals.append(
            {"direction_id": key[0], "match": key[1], "evidence_ref": key[2]}
        )
    normalized_signals.sort(key=lambda item: (item["direction_id"], item["match"], item["evidence_ref"]))

    return {
        "source_id": metadata["source_id"],
        "source_available": metadata["source_available"],
        "authors": _sorted_unique_strings(metadata["authors"], "authors", allow_empty=False),
        "institutions": _sorted_unique_strings(
            metadata["institutions"], "institutions", allow_empty=False
        ),
        "journal_id": metadata["journal_id"],
        "primary_direction_id": metadata["primary_direction_id"],
        "direction_signals": normalized_signals,
        "bridge_direction_refs": _sorted_unique_strings(
            metadata["bridge_direction_refs"], "bridge_direction_refs"
        ),
        "horizon_anchor_refs": _sorted_unique_strings(
            metadata["horizon_anchor_refs"], "horizon_anchor_refs"
        ),
        "coverage_gap_refs": _sorted_unique_strings(
            metadata["coverage_gap_refs"], "coverage_gap_refs"
        ),
        "evidence_completeness": metadata["evidence_completeness"],
        "manifestation_fitness": metadata["manifestation_fitness"],
        "timeliness": metadata["timeliness"],
        "access_posture": metadata["access_posture"],
        "evidence_refs": _sorted_unique_strings(
            metadata["evidence_refs"], "evidence_refs", allow_empty=False
        ),
    }


def project_candidate_fact(
    candidate: Mapping[str, Any],
    comparison: Mapping[str, Any],
    ranking_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Create one immutable T05 fact projection without altering upstream objects."""

    candidate_before = canonical_json(candidate)
    comparison_before = canonical_json(comparison)
    _assert_sealed(candidate, "T04_CANDIDATE_HASH_INVALID")
    _assert_sealed(comparison, "T04_COMPARISON_HASH_INVALID")

    if candidate.get("schema_version") != "p04.t04.candidate-input.0.1":
        raise DiscoveryProjectionError("T04_CANDIDATE_SCHEMA_VERSION_UNSUPPORTED")
    if comparison.get("schema_version") != "p04.t04.candidate-comparison.0.1":
        raise DiscoveryProjectionError("T04_COMPARISON_SCHEMA_VERSION_UNSUPPORTED")
    if candidate.get("candidate_id") != comparison.get("candidate_id"):
        raise DiscoveryProjectionError("CANDIDATE_COMPARISON_ID_MISMATCH")
    if comparison.get("state_axes_unchanged") is not True:
        raise DiscoveryProjectionError("T04_STATE_AXES_NOT_PRESERVED")
    expected_axes = {name: candidate.get(name) for name in REQUIRED_STATE_AXES}
    if comparison.get("state_axes") != expected_axes:
        raise DiscoveryProjectionError("T04_STATE_AXES_MISMATCH")
    side_effects = comparison.get("side_effects")
    if not isinstance(side_effects, Mapping) or set(side_effects) != T04_ZERO_SIDE_EFFECT_KEYS:
        raise DiscoveryProjectionError("T04_FACT_SHAPE_INVALID", "side_effects")
    if any(value != 0 for value in side_effects.values()):
        raise DiscoveryProjectionError("T04_SIDE_EFFECTS_NONZERO")

    normalized_metadata = _normalize_metadata(ranking_metadata)
    relations = comparison.get("relations")
    novelty = comparison.get("novelty")
    if not isinstance(relations, list) or not isinstance(novelty, Mapping):
        raise DiscoveryProjectionError("T04_FACT_SHAPE_INVALID")
    novelty_axes_source = novelty.get("axes", {})
    novelty_axes: dict[str, str] = {}
    for axis in ("publication_new", "first_seen_new", "library_new", "direction_new"):
        value = novelty_axes_source.get(axis, {}).get("status")
        if value not in {"TRUE", "FALSE", "UNKNOWN"}:
            raise DiscoveryProjectionError("T04_NOVELTY_AXIS_INVALID", axis)
        novelty_axes[axis] = value

    relation_types = sorted({item["relation_type"] for item in relations})
    relation_refs = sorted({item["relation_id"] for item in relations})
    manifestations = _sorted_unique_strings(
        candidate.get("manifestation_ids"), "manifestation_ids", allow_empty=False
    )
    canonical_identity = "|".join(
        [candidate["work_cluster_id"], candidate["candidate_id"], manifestations[0]]
    )
    result = seal_object(
        {
            "schema_version": SCHEMA_VERSION,
            "object_type": "DiscoveryCandidateFact",
            "candidate_id": candidate["candidate_id"],
            "work_cluster_id": candidate["work_cluster_id"],
            "manifestation_ids": manifestations,
            "canonical_identity": canonical_identity,
            "title": candidate["title"],
            "publication_date": candidate["publication_date"],
            "state_axes": copy.deepcopy(expected_axes),
            "state_axes_unchanged": True,
            "relation_types": relation_types,
            "relation_refs": relation_refs,
            "novelty_axes": novelty_axes,
            "upstream_refs": {
                "candidate_hash": candidate["content_hash"],
                "comparison_hash": comparison["content_hash"],
            },
            "ranking_metadata": normalized_metadata,
            "side_effects": copy.deepcopy(ZERO_SIDE_EFFECTS),
        }
    )
    if canonical_json(candidate) != candidate_before:
        raise DiscoveryProjectionError("UPSTREAM_CANDIDATE_MUTATED")
    if canonical_json(comparison) != comparison_before:
        raise DiscoveryProjectionError("UPSTREAM_COMPARISON_MUTATED")
    return result


def project_batch(
    entries: Iterable[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]]
) -> list[dict[str, Any]]:
    facts = [project_candidate_fact(candidate, comparison, metadata) for candidate, comparison, metadata in entries]
    candidate_ids = [item["candidate_id"] for item in facts]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise DiscoveryProjectionError("DUPLICATE_CANDIDATE_ID")
    return sorted(facts, key=lambda item: item["canonical_identity"])


__all__ = [
    "DiscoveryProjectionError",
    "SCHEMA_VERSION",
    "ZERO_SIDE_EFFECTS",
    "canonical_json",
    "project_batch",
    "project_candidate_fact",
    "seal_object",
    "sha256_json",
]
