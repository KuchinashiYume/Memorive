"""Independent counterevidence-candidate selection with frozen MMR inputs.

Selection labels objects only as candidates.  It never concludes that an item
is a contradiction or validated counterexample, and it never violates a hard
filter or diversity cap merely to fill requested slots.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .canonical import canonical_json_bytes, sha256_bytes
from .errors import ContractViolation, IdentityConflict, SourceSnapshotDriftError
from .shadow_contracts import validate_retrieval_policy_snapshot


COUNTEREVIDENCE_FIELD_PATH_PATTERNS = (
    r"author_conclusion",
    r"boundary_conditions/[0-9]+",
    r"author_limitations_outlook/limitations/[0-9]+/quote",
    r"author_limitations_outlook/outlook/[0-9]+/quote",
)
_COUNTEREVIDENCE_FIELD_PATH_RE = re.compile(
    r"^(?:" + "|".join(COUNTEREVIDENCE_FIELD_PATH_PATTERNS) + r")$"
)


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractViolation(f"{field} must be a non-empty exact string")
    if "\r" in value or "\n" in value:
        raise ContractViolation(f"{field} contains a forbidden line break")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractViolation(f"{field} must be a positive integer")
    return value


def _unit_interval(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractViolation(f"{field} must be a number in [0,1]")
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise ContractViolation(f"{field} must be a finite number in [0,1]")
    return number


def _sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def validate_counterevidence_field_path(value: Any) -> str:
    """Require one parsed ServiceContracts Field Registry path from the frozen counter set."""

    field_path = _non_empty_string(value, "field_id")
    if not _COUNTEREVIDENCE_FIELD_PATH_RE.fullmatch(field_path):
        raise ContractViolation(
            "field_id is outside the frozen independent counterevidence field set",
            context={
                "field_id": field_path,
                "allowed_patterns": list(COUNTEREVIDENCE_FIELD_PATH_PATTERNS),
            },
        )
    return field_path


def make_frozen_distance_matrix(
    distance_matrix: Mapping[str, Mapping[str, float]],
    candidate_ids: Sequence[str],
) -> dict[str, Any]:
    """Validate and hash one complete symmetric candidate distance matrix."""

    if not isinstance(distance_matrix, Mapping):
        raise ContractViolation("distance_matrix must be an object")
    if isinstance(candidate_ids, (str, bytes)) or not isinstance(candidate_ids, Sequence):
        raise ContractViolation("candidate_ids must be a sequence")
    ids = [_non_empty_string(item, "candidate_ids[]") for item in candidate_ids]
    if len(ids) != len(set(ids)):
        raise IdentityConflict("candidate_ids contain duplicates")
    ids = sorted(ids)
    if set(distance_matrix) != set(ids):
        raise ContractViolation(
            "distance_matrix rows must match the candidate exact set",
            context={
                "missing": sorted(set(ids) - set(distance_matrix)),
                "unexpected": sorted(set(distance_matrix) - set(ids)),
            },
        )

    normalized: dict[str, dict[str, float]] = {}
    for left in ids:
        raw_row = distance_matrix[left]
        if not isinstance(raw_row, Mapping) or set(raw_row) != set(ids):
            raise ContractViolation(f"distance_matrix[{left}] columns must match exact set")
        row: dict[str, float] = {}
        for right in ids:
            row[right] = _unit_interval(
                raw_row[right], f"distance_matrix[{left}][{right}]"
            )
        if row[left] != 0.0:
            raise ContractViolation("distance_matrix diagonal must be exactly zero")
        normalized[left] = row

    for left in ids:
        for right in ids:
            if normalized[left][right] != normalized[right][left]:
                raise ContractViolation("distance_matrix must be exactly symmetric")

    payload = {
        "candidate_ids": ids,
        "distance_metric": "distance_0_1_v1",
        "distances": normalized,
    }
    return {
        "object_type": "FrozenDistanceMatrix",
        "schema_version": "1.0",
        **payload,
        "matrix_sha256": _sha256(payload),
    }


def validate_frozen_distance_matrix(
    value: Mapping[str, Any],
    candidate_ids: Sequence[str],
    *,
    expected_matrix_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify matrix membership, structure and the pre-selection freeze hash."""

    if not isinstance(value, Mapping):
        raise ContractViolation("frozen_distance_matrix must be an object")
    frozen = deepcopy(dict(value))
    expected_keys = {
        "object_type",
        "schema_version",
        "candidate_ids",
        "distance_metric",
        "distances",
        "matrix_sha256",
    }
    if set(frozen) != expected_keys:
        raise ContractViolation("frozen_distance_matrix keys do not match exact set")
    if frozen["object_type"] != "FrozenDistanceMatrix" or frozen["schema_version"] != "1.0":
        raise ContractViolation("frozen_distance_matrix identity is unsupported")
    if frozen["distance_metric"] != "distance_0_1_v1":
        raise ContractViolation("frozen_distance_matrix metric is unsupported")
    rebuilt = make_frozen_distance_matrix(frozen["distances"], candidate_ids)
    if frozen["candidate_ids"] != rebuilt["candidate_ids"]:
        raise ContractViolation("frozen_distance_matrix candidate order is non-canonical")
    supplied_hash = _non_empty_string(frozen["matrix_sha256"], "matrix_sha256")
    if supplied_hash != rebuilt["matrix_sha256"]:
        raise SourceSnapshotDriftError(
            "frozen distance matrix hash drift",
            context={
                "expected": rebuilt["matrix_sha256"],
                "observed": supplied_hash,
            },
        )
    if expected_matrix_sha256 is not None:
        expected = _non_empty_string(expected_matrix_sha256, "expected_matrix_sha256")
        if supplied_hash != expected:
            raise SourceSnapshotDriftError(
                "distance matrix does not match the frozen policy hash",
                context={"expected": expected, "observed": supplied_hash},
            )
    return rebuilt


def _validate_candidate(
    raw_value: Any,
    *,
    allowed_paper_ids: frozenset[str],
) -> tuple[dict[str, Any], str | None]:
    if not isinstance(raw_value, Mapping):
        raise ContractViolation("counterevidence candidate must be an object")
    raw = dict(raw_value)
    required = {
        "candidate_id",
        "paper_id",
        "card_id",
        "source_id",
        "source_family",
        "field_id",
        "counter_relevance",
        "active",
        "scope_eligible",
        "rights_allowed",
    }
    if required - set(raw):
        raise ContractViolation(
            "counterevidence candidate is missing required fields",
            context={"missing": sorted(required - set(raw))},
        )
    result = {
        "candidate_id": _non_empty_string(raw["candidate_id"], "candidate_id"),
        "paper_id": _non_empty_string(raw["paper_id"], "paper_id"),
        "card_id": _non_empty_string(raw["card_id"], "card_id"),
        "source_id": _non_empty_string(raw["source_id"], "source_id"),
        "source_family": _non_empty_string(raw["source_family"], "source_family"),
        "field_id": validate_counterevidence_field_path(raw["field_id"]),
        "counter_relevance": _unit_interval(raw["counter_relevance"], "counter_relevance"),
    }
    for optional in ("doc_type", "provenance_state"):
        if optional in raw:
            result[optional] = _non_empty_string(raw[optional], optional)
    for flag in ("active", "scope_eligible", "rights_allowed"):
        if not isinstance(raw[flag], bool):
            raise ContractViolation(f"{flag} must be an explicit boolean")
    if raw["active"] is not True:
        return result, "INACTIVE"
    if raw["scope_eligible"] is not True or result["paper_id"] not in allowed_paper_ids:
        return result, "OUT_OF_EXPLICIT_SCOPE"
    if raw["rights_allowed"] is not True:
        return result, "RIGHTS_NOT_ALLOWED"
    return result, None


def select_counterevidence_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    allowed_paper_ids: Sequence[str],
    policy_snapshot: Mapping[str, Any],
    frozen_distance_matrix: Mapping[str, Any],
    expected_matrix_sha256: str | None = None,
) -> dict[str, Any]:
    """Select diverse candidates with deterministic MMR and honest underfill."""

    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise ContractViolation("candidates must be a sequence")
    snapshot = validate_retrieval_policy_snapshot(policy_snapshot)
    aggregation_policy = snapshot["aggregation_policy"]
    slots = _positive_int(aggregation_policy["counterevidence_slots"], "policy.counterevidence_slots")
    per_source_cap = _positive_int(
        aggregation_policy["counter_per_source_cap"], "policy.counter_per_source_cap"
    )
    per_paper_cap = _positive_int(
        aggregation_policy["counter_per_paper_cap"], "policy.counter_per_paper_cap"
    )
    minimum_relevance = _unit_interval(
        aggregation_policy["counter_minimum_relevance"],
        "policy.counter_minimum_relevance",
    )
    mmr_lambda = _unit_interval(aggregation_policy["mmr_lambda"], "policy.mmr_lambda")
    if isinstance(allowed_paper_ids, (str, bytes)) or not isinstance(
        allowed_paper_ids, Sequence
    ) or not allowed_paper_ids:
        raise ContractViolation("allowed_paper_ids must be a non-empty explicit scope")
    scope_values = [_non_empty_string(item, "allowed_paper_ids[]") for item in allowed_paper_ids]
    if len(scope_values) != len(set(scope_values)):
        raise ContractViolation("allowed_paper_ids must not contain duplicates")
    scope = frozenset(scope_values)

    checked: list[dict[str, Any]] = []
    rejection_counts: Counter[str] = Counter()
    candidate_ids: list[str] = []
    seen_candidate_ids: set[str] = set()
    for raw_candidate in candidates:
        candidate, rejection = _validate_candidate(
            raw_candidate,
            allowed_paper_ids=scope,
        )
        candidate_id = candidate["candidate_id"]
        if candidate_id in seen_candidate_ids:
            raise IdentityConflict("counterevidence candidate IDs must be unique")
        seen_candidate_ids.add(candidate_id)
        if rejection is not None:
            rejection_counts[rejection] += 1
            continue
        if candidate["counter_relevance"] < minimum_relevance:
            rejection_counts["BELOW_MINIMUM_RELEVANCE"] += 1
            continue
        checked.append(candidate)
        candidate_ids.append(candidate_id)

    matrix = validate_frozen_distance_matrix(
        frozen_distance_matrix,
        candidate_ids,
        expected_matrix_sha256=expected_matrix_sha256,
    )
    distances = matrix["distances"]

    remaining = {candidate["candidate_id"]: candidate for candidate in checked}
    selected: list[dict[str, Any]] = []
    source_counts: defaultdict[str, int] = defaultdict(int)
    paper_counts: defaultdict[str, int] = defaultdict(int)
    diversity_skips: defaultdict[str, set[str]] = defaultdict(set)

    while remaining and len(selected) < slots:
        scored: list[tuple[float, float, str, dict[str, Any]]] = []
        for candidate_id, candidate in remaining.items():
            if source_counts[candidate["source_family"]] >= per_source_cap:
                diversity_skips["SOURCE_FAMILY_CAP"].add(candidate_id)
                continue
            if paper_counts[candidate["paper_id"]] >= per_paper_cap:
                diversity_skips["PAPER_CAP"].add(candidate_id)
                continue
            if selected:
                maximum_similarity = max(
                    1.0 - distances[candidate_id][chosen["candidate_id"]]
                    for chosen in selected
                )
            else:
                maximum_similarity = 0.0
            mmr_score = math.fsum(
                (
                    mmr_lambda * candidate["counter_relevance"],
                    -(1.0 - mmr_lambda) * maximum_similarity,
                )
            )
            scored.append(
                (mmr_score, candidate["counter_relevance"], candidate_id, candidate)
            )
        if not scored:
            break
        mmr_score, relevance, candidate_id, candidate = min(
            scored,
            key=lambda item: (-item[0], -item[1], item[2]),
        )
        selected.append(
            {
                **deepcopy(candidate),
                "counterevidence_candidate": True,
                "selection_order": len(selected) + 1,
                "mmr_score": mmr_score,
            }
        )
        source_counts[candidate["source_family"]] += 1
        paper_counts[candidate["paper_id"]] += 1
        del remaining[candidate_id]

    underfill_count = slots - len(selected)
    underfill_reasons: list[dict[str, Any]] = []
    if underfill_count:
        underfill_reasons.append(
            {"reason_code": "NO_MORE_QUALIFIED_CANDIDATES", "count": underfill_count}
        )
        for reason in sorted(rejection_counts):
            underfill_reasons.append(
                {"reason_code": reason, "count": rejection_counts[reason]}
            )
        for reason in sorted(diversity_skips):
            underfill_reasons.append(
                {"reason_code": reason, "count": len(diversity_skips[reason])}
            )

    return {
        "object_type": "CounterevidenceCandidateSet",
        "schema_version": "1.0",
        "claim_status": "candidate_only_not_scientific_conclusion",
        "requested_slots": slots,
        "selected_count": len(selected),
        "underfilled": bool(underfill_count),
        "underfill_count": underfill_count,
        "underfill_reasons": underfill_reasons,
        "minimum_relevance": minimum_relevance,
        "mmr_lambda": mmr_lambda,
        "distance_matrix_sha256": matrix["matrix_sha256"],
        "per_source_cap": per_source_cap,
        "per_paper_cap": per_paper_cap,
        "hard_filter_report": {
            "active_only": True,
            "explicit_paper_scope": sorted(scope),
            "rights_required": True,
            "rejection_counts": [
                {"reason_code": reason, "count": rejection_counts[reason]}
                for reason in sorted(rejection_counts)
            ],
        },
        "diversity_report": {
            "source_family_counts": [
                {"source_family": key, "count": source_counts[key]}
                for key in sorted(source_counts)
            ],
            "paper_counts": [
                {"paper_id": key, "count": paper_counts[key]}
                for key in sorted(paper_counts)
            ],
            "cap_skip_counts": [
                {"reason_code": reason, "count": len(diversity_skips[reason])}
                for reason in sorted(diversity_skips)
            ],
        },
        "candidates": selected,
    }
