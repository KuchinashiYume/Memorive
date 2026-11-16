"""COST-ROUTING deterministic quality-cost routing advice.

This surface emits advice only.  It never dispatches a model call, writes a
route, makes an DECISION_LOG decision, registers an ARTIFACT_REGISTRY artifact, moves a pointer, or
claims scientific optimality.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping, Sequence

from retrieval_weighting.contracts import make_hashed_payload, make_stable_id, verify_hashed_payload


POLICY_ORDER = [
    "evidence_qualification",
    "quality_hard_gate",
    "stability_reliability",
    "pareto_dominance",
    "cost",
    "latency",
    "deterministic_candidate_id_order",
]
ZERO_SIDE_EFFECT_COUNTERS = {
    "external_calls": 0,
    "paid_calls": 0,
    "production_route_writes": 0,
    "decision_log_decision_writes": 0,
    "artifact_registry_registry_writes": 0,
    "pointer_moves": 0,
    "deployments": 0,
    "deletions": 0,
}
COMPARABILITY_FIELDS = (
    "role_family",
    "qualification_contract_ref",
    "qset_hash",
    "pack_hash",
    "scorer_contract_ref",
    "profile_hash",
    "route_snapshot_id",
    "provider_region",
    "egress_identity",
    "evaluation_window",
    "sample_basis",
)
_CANDIDATE_KEYS = {
    "schema_version",
    "candidate_id",
    "role_family",
    "evidence_class",
    "evidence_status",
    "qualification_eligibility",
    "qualification_contract_ref",
    "qset_hash",
    "pack_hash",
    "scorer_contract_ref",
    "profile_hash",
    "route_snapshot_id",
    "provider_region",
    "egress_identity",
    "evaluation_window",
    "sample_basis",
    "quality_metric",
    "quality_score",
    "quality_floor",
    "critical_failure_count",
    "reliability_rate",
    "reliability_floor",
    "cost_cny_per_unit",
    "latency_ms_p95",
    "ownership_allowed",
    "source_evidence_refs",
    "content_hash",
}


class DynamicRoutingError(ValueError):
    """A routing candidate or advice request violates the Retrieval contract."""


def _text(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise DynamicRoutingError(f"{field} must be non-empty exact text")
    return value


def _number(
    value: Any,
    field: str,
    *,
    nullable: bool = False,
    minimum: float = 0,
    maximum: float | None = None,
) -> float | int | None:
    if value is None and nullable:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        raise DynamicRoutingError(f"{field} must be numeric and >= {minimum}")
    return value


def build_routing_policy_candidate(**values: Any) -> dict[str, Any]:
    payload = {"schema_version": "COST_ROUTING_ROUTING_POLICY_CANDIDATE_V1", **deepcopy(values)}
    return validate_routing_policy_candidate(make_hashed_payload(payload))


def validate_routing_policy_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        candidate = verify_hashed_payload(value, "routing_policy_candidate")
    except Exception as exc:
        raise DynamicRoutingError("ROUTING_CANDIDATE_HASH_INVALID") from exc
    if set(candidate) != _CANDIDATE_KEYS:
        raise DynamicRoutingError("ROUTING_CANDIDATE_EXACT_KEYS_MISMATCH")
    if candidate["schema_version"] != "COST_ROUTING_ROUTING_POLICY_CANDIDATE_V1":
        raise DynamicRoutingError("ROUTING_CANDIDATE_SCHEMA_UNSUPPORTED")
    for field in ("candidate_id", "role_family", "evidence_class", "evidence_status", "quality_metric"):
        _text(candidate[field], field)
    for field in COMPARABILITY_FIELDS[1:]:
        _text(candidate[field], field, nullable=True)
    if candidate["evidence_class"] not in {
        "QUALIFICATION", "NONBLIND_REFERENCE", "SELECTION", "REGRESSION", "DIAGNOSTIC", "UNSCORED_NOT_ASSESSED"
    }:
        raise DynamicRoutingError("ROUTING_CANDIDATE_EVIDENCE_CLASS_INVALID")
    if candidate["evidence_status"] not in {"PASS", "FAIL", "ERROR", "NOT_ASSESSED"}:
        raise DynamicRoutingError("ROUTING_CANDIDATE_EVIDENCE_STATUS_INVALID")
    for field in ("qualification_eligibility", "ownership_allowed"):
        if not isinstance(candidate[field], bool):
            raise DynamicRoutingError(f"{field} must be boolean")
    for field in ("quality_score", "cost_cny_per_unit", "latency_ms_p95"):
        _number(candidate[field], field, nullable=True)
    _number(candidate["reliability_rate"], "reliability_rate", nullable=True, maximum=1)
    _number(candidate["quality_floor"], "quality_floor")
    for field in ("reliability_floor",):
        _number(candidate[field], field, maximum=1)
    failures = candidate["critical_failure_count"]
    if isinstance(failures, bool) or not isinstance(failures, int) or failures < 0:
        raise DynamicRoutingError("critical_failure_count must be a non-negative integer")
    refs = candidate["source_evidence_refs"]
    if not isinstance(refs, list) or not refs or any(_text(ref, "source_evidence_refs[]") is None for ref in refs) or refs != sorted(set(refs)):
        raise DynamicRoutingError("source_evidence_refs must be non-empty, sorted and unique")
    return candidate


def _group_identity(candidate: Mapping[str, Any]) -> dict[str, str] | None:
    identity = {field: candidate[field] for field in COMPARABILITY_FIELDS}
    if any(value is None for value in identity.values()):
        return None
    return {field: str(value) for field, value in identity.items()}


def project_candidate_eligibility(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = validate_routing_policy_candidate(value)
    not_assessed: list[str] = []
    rejected: list[str] = []
    if candidate["evidence_class"] != "QUALIFICATION":
        not_assessed.append("EVIDENCE_CLASS_NOT_QUALIFICATION")
    if candidate["evidence_status"] != "PASS":
        not_assessed.append("EVIDENCE_STATUS_NOT_PASS")
    if candidate["qualification_eligibility"] is not True:
        not_assessed.append("QUALIFICATION_ELIGIBILITY_FALSE")
    group_identity = _group_identity(candidate)
    if group_identity is None:
        not_assessed.append("COMPARABILITY_BINDING_INCOMPLETE")
    for field in ("quality_score", "reliability_rate", "cost_cny_per_unit", "latency_ms_p95"):
        if candidate[field] is None:
            not_assessed.append(f"{field.upper()}_UNKNOWN")
    if not not_assessed:
        if candidate["ownership_allowed"] is not True:
            rejected.append("OWNERSHIP_NOT_ALLOWED")
        if candidate["critical_failure_count"] > 0:
            rejected.append("CRITICAL_FAILURE_PRESENT")
        if candidate["quality_score"] < candidate["quality_floor"]:
            rejected.append("QUALITY_BELOW_FLOOR")
        if candidate["reliability_rate"] < candidate["reliability_floor"]:
            rejected.append("RELIABILITY_BELOW_FLOOR")
    status = "NOT_ASSESSED" if not_assessed else "REJECTED" if rejected else "ELIGIBLE"
    group_id = make_stable_id("cmp_", group_identity) if group_identity is not None else None
    return make_hashed_payload(
        {
            "schema_version": "COST_ROUTING_CANDIDATE_ELIGIBILITY_V1",
            "candidate_id": candidate["candidate_id"],
            "role_family": candidate["role_family"],
            "status": status,
            "comparability_group_id": group_id,
            "reason_codes": sorted(set(not_assessed + rejected)),
            "quality_score": candidate["quality_score"],
            "reliability_rate": candidate["reliability_rate"],
            "cost_cny_per_unit": candidate["cost_cny_per_unit"],
            "latency_ms_p95": candidate["latency_ms_p95"],
            "source_candidate_hash": deepcopy(candidate["content_hash"]),
        }
    )


def build_comparability_group_manifest(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    checked = [validate_routing_policy_candidate(row) for row in candidates]
    projections = [project_candidate_eligibility(row) for row in checked]
    candidate_ids = [row["candidate_id"] for row in checked]
    if candidate_ids != sorted(set(candidate_ids)):
        raise DynamicRoutingError("candidate_id values must be sorted and unique")
    groups: dict[str, list[str]] = {}
    ungrouped: list[str] = []
    for row in projections:
        group_id = row["comparability_group_id"]
        if group_id is None:
            ungrouped.append(row["candidate_id"])
        else:
            groups.setdefault(group_id, []).append(row["candidate_id"])
    return make_hashed_payload(
        {
            "schema_version": "COST_ROUTING_COMPARABILITY_GROUP_MANIFEST_V1",
            "groups": [
                {"comparability_group_id": key, "candidate_ids": sorted(value)}
                for key, value in sorted(groups.items())
            ],
            "ungrouped_candidate_ids": sorted(ungrouped),
            "cross_group_sort_performed": False,
        }
    )


def _dominates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    at_least = (
        left["quality_score"] >= right["quality_score"]
        and left["reliability_rate"] >= right["reliability_rate"]
        and left["cost_cny_per_unit"] <= right["cost_cny_per_unit"]
        and left["latency_ms_p95"] <= right["latency_ms_p95"]
    )
    strict = (
        left["quality_score"] > right["quality_score"]
        or left["reliability_rate"] > right["reliability_rate"]
        or left["cost_cny_per_unit"] < right["cost_cny_per_unit"]
        or left["latency_ms_p95"] < right["latency_ms_p95"]
    )
    return at_least and strict


def advise_routes(
    candidates: Sequence[Mapping[str, Any]],
    *,
    role_family: str,
    requested_group_id: str | None = None,
) -> dict[str, Any]:
    _text(role_family, "role_family")
    if requested_group_id is not None:
        _text(requested_group_id, "requested_group_id")
    checked = [validate_routing_policy_candidate(row) for row in candidates]
    ids = [row["candidate_id"] for row in checked]
    if ids != sorted(set(ids)):
        raise DynamicRoutingError("candidate_id values must be sorted and unique")
    projections = [project_candidate_eligibility(row) for row in checked]
    by_id = {row["candidate_id"]: row for row in checked}
    scoped = [row for row in projections if row["role_family"] == role_family]
    eligible_groups: dict[str, list[dict[str, Any]]] = {}
    for row in scoped:
        if row["status"] == "ELIGIBLE":
            eligible_groups.setdefault(str(row["comparability_group_id"]), []).append(row)
    reason_codes: list[str] = []
    selected_group: str | None = None
    outcome: str
    selected_ids: list[str] = []
    if requested_group_id is not None:
        selected_group = requested_group_id
        group_rows = eligible_groups.get(requested_group_id, [])
        if not group_rows:
            outcome = "ABSTAIN_INSUFFICIENT_EVIDENCE"
            reason_codes.append("REQUESTED_GROUP_HAS_NO_ELIGIBLE_CANDIDATE")
        else:
            outcome = "PENDING_PARETO"
    elif len(eligible_groups) == 1:
        selected_group, group_rows = next(iter(eligible_groups.items()))
        outcome = "PENDING_PARETO"
    elif len(eligible_groups) > 1:
        group_rows = []
        outcome = "ABSTAIN_MULTIPLE_COMPARABILITY_GROUPS"
        reason_codes.append("CROSS_GROUP_SORT_FORBIDDEN")
    else:
        group_rows = []
        outcome = "ABSTAIN_INSUFFICIENT_EVIDENCE"
        if not scoped:
            reason_codes.append("ROLE_FAMILY_HAS_NO_CANDIDATE")
        elif any(row["status"] == "REJECTED" for row in scoped):
            reason_codes.append("NO_CANDIDATE_PASSED_HARD_GATES")
        else:
            reason_codes.append("NO_QUALIFICATION_ELIGIBLE_CANDIDATE")
    if outcome == "PENDING_PARETO":
        frontier = [
            row
            for row in group_rows
            if not any(
                other["candidate_id"] != row["candidate_id"] and _dominates(other, row)
                for other in group_rows
            )
        ]
        frontier.sort(
            key=lambda row: (
                -float(row["quality_score"]),
                -float(row["reliability_rate"]),
                float(row["cost_cny_per_unit"]),
                float(row["latency_ms_p95"]),
                row["candidate_id"],
            )
        )
        selected_ids = [row["candidate_id"] for row in frontier]
        outcome = "RECOMMEND_SINGLE" if len(selected_ids) == 1 else "RECOMMEND_PARETO_SET"
    rejection_entries = []
    for row in scoped:
        if row["candidate_id"] in selected_ids:
            continue
        reasons = list(row["reason_codes"])
        status = row["status"]
        if status == "ELIGIBLE" and selected_group == row["comparability_group_id"]:
            reasons = ["PARETO_DOMINATED"]
            status = "NOT_RECOMMENDED"
        elif status == "ELIGIBLE":
            reasons = ["OUTSIDE_SELECTED_COMPARABILITY_GROUP"]
            status = "NOT_RECOMMENDED"
        rejection_entries.append(
            {
                "candidate_id": row["candidate_id"],
                "status": status,
                "reason_codes": sorted(reasons),
                "append_only": True,
            }
        )
    return make_hashed_payload(
        {
            "schema_version": "COST_ROUTING_ROUTING_ADVICE_V1",
            "role_family": role_family,
            "outcome": outcome,
            "reason_codes": sorted(reason_codes),
            "selected_comparability_group_id": selected_group,
            "selected_candidate_ids": selected_ids,
            "considered_candidate_ids": [row["candidate_id"] for row in scoped],
            "eligible_group_ids": sorted(eligible_groups),
            "policy_order": POLICY_ORDER,
            "global_weighted_score_used": False,
            "cross_group_sort_performed": False,
            "rejection_and_abstention_entries": rejection_entries,
            "side_effect_counters": deepcopy(ZERO_SIDE_EFFECT_COUNTERS),
            "production_activation_authorized": False,
            "scientific_optimality": "NOT_ASSESSED",
            "acceptance_verdict": "NOT_ASSESSED",
        }
    )


__all__ = [
    "COMPARABILITY_FIELDS",
    "DynamicRoutingError",
    "POLICY_ORDER",
    "ZERO_SIDE_EFFECT_COUNTERS",
    "advise_routes",
    "build_comparability_group_manifest",
    "build_routing_policy_candidate",
    "project_candidate_eligibility",
    "validate_routing_policy_candidate",
]
