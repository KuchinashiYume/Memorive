"""Coverage tiers, independent of role difficulty, reply limits and repair rights.

Minimum scorer coverage is explicit, never hidden behind an advertised cost %.
The frozen full suite and category-local penalty functions remain authoritative.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping, Sequence

from .contracts import canonical_sha256
from .exam_successor import cumulative_category_repair_penalty

REVISION = "PHASE2_NEW_NESTED_COVERAGE_AND_UNIFORM_TWO_REPAIRS_V1"
TIERS = {"LIGHT": (0.2, "轻度"), "HALF": (0.5, "半额"), "FULL": (1.0, "全量")}
ROLE_BY_NODE = {"ingest": "OCR_PAGE", "chunk_embedding": "EMBEDDING_TEXT",
                "card_distill": "CARD_DISTILLER", "transport_review": "CARD_REVIEWER",
                "context_pack": "RERANKER_TEXT", "analysis": "ANALYSIS_PRIMARY",
                "judgment_review": "ANALYSIS_REVIEWER"}


def validate_tier(tier: str) -> str:
    if not isinstance(tier, str) or tier not in TIERS:
        raise ValueError("EXAM_TIER_INVALID")
    return tier


def nested_selection(rows: Sequence[Mapping[str, Any]], *, tier: str, id_key: str,
                     required_ids: Sequence[str] = (), strata: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Stable stratified priority order; no answer/score is consulted.

    Interleave strata by round-robin, then take a prefix. Required controls come
    first, so LIGHT is a subset of HALF and HALF a subset of FULL.
    """
    fraction = TIERS[validate_tier(tier)][0]
    by_id = {str(row[id_key]): row for row in rows}
    if not rows or len(by_id) != len(rows) or not set(required_ids) <= set(by_id):
        raise ValueError("EXAM_TIER_SAMPLE_EXACT_SET_INVALID")
    groups: dict[tuple[str, ...], list[str]] = {}
    for identifier, row in by_id.items():
        groups.setdefault(tuple(str(row.get(k, "")) for k in strata), []).append(identifier)
    for values in groups.values():
        values.sort(key=lambda x: canonical_sha256({"revision": REVISION, "id": x}))
    ordered = list(dict.fromkeys(required_ids))
    for index in range(max(map(len, groups.values()))):
        for key in sorted(groups):
            if index < len(groups[key]) and groups[key][index] not in ordered:
                ordered.append(groups[key][index])
    count = max(len(required_ids), math.ceil(len(rows) * fraction))
    selected = set(ordered[:count])
    # Preserve the frozen source order in the subject prompt.
    return [deepcopy(dict(row)) for row in rows if str(row[id_key]) in selected]


def tier_manifest(*, role: str, tier: str, full_ids: Sequence[str], selected_ids: Sequence[str],
                  required_ids: Sequence[str] = (), source_binding: Any = None,
                  floor_reason: str | None = None, unit: str = "CASE") -> dict[str, Any]:
    validate_tier(tier)
    if (not full_ids or len(set(full_ids)) != len(full_ids) or not selected_ids
            or len(set(selected_ids)) != len(selected_ids)
            or not set(required_ids) <= set(selected_ids) <= set(full_ids)):
        raise ValueError("EXAM_TIER_MANIFEST_EXACT_SET_INVALID")
    value = dict(schema_version=REVISION, role_id=role, tier=tier, label=TIERS[tier][1],
                 requested_fraction=TIERS[tier][0], actual_fraction=len(selected_ids) / len(full_ids),
                 full_count=len(full_ids), selected_count=len(selected_ids), unit=unit,
                 selected_ids=list(selected_ids), required_ids=list(required_ids),
                 full_ids_sha256=canonical_sha256(list(full_ids)), source_binding=source_binding,
                 minimum_coverage_reason=floor_reason, max_directed_repairs=2,
                 repair_policy="EXISTING_CATEGORY_NONLINEAR_ROUND1_PLUS_ROUND2",
                 repair_allowance_varies_by_tier=False, output_limit_varies_by_tier=False,
                 content_difficulty_varies_by_tier=False, missing_family_is_zero=False,
                 cost_fraction_guaranteed=False, formal_qualification_eligible=False,
                 cross_category_comparison_forbidden=True, model_fail_established=False,
                 precision="FULL_REFERENCE_SUITE" if tier == "FULL" else "PROVISIONAL_SUBSET")
    value["manifest_sha256"] = canonical_sha256(value)
    return value


def scored_projection(role: str, raw_score: float, rounds: Sequence[Mapping[str, Any]], tier: str) -> dict[str, Any]:
    """Same coefficients for every tier; no new global repair weight or curve."""
    validate_tier(tier)
    if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)) or not math.isfinite(raw_score) or not 0 <= raw_score <= 100:
        raise ValueError("EXAM_TIER_SCORE_INVALID")
    if any(row.get("category_id") != role for row in rounds):
        raise ValueError("CROSS_CATEGORY_REPAIR_COEFFICIENTS_FORBIDDEN")
    penalty = cumulative_category_repair_penalty(rounds) if rounds else None
    amount = penalty["cumulative_penalty"] if penalty else 0.0
    return dict(role_id=role, tier=tier, post_repair_capability_score=raw_score,
                repair_adjusted_exam_score=round(max(0.0, raw_score - amount), 6),
                cumulative_category_repair_penalty=penalty, directed_repairs_used=len(rounds),
                max_directed_repairs=2, qualification_eligible=False)
