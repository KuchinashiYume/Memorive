"""Auditable six-provider aggregation policies for P03/T04 Shadow retrieval."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .derived_penalty import validate_derived_penalty_against_snapshot
from .errors import ContractViolation, IdentityConflict
from .providers import (
    AUTHORITY_PROVIDERS,
    PROVIDERS,
    RELEVANCE_PROVIDERS,
    validate_provider_set,
)
from .shadow_contracts import validate_retrieval_policy_snapshot


PROVIDER_WEIGHTS: dict[str, float] = {
    "Similarity": 0.45,
    "Semantic": 0.20,
    "Manual": 0.15,
    "Rule": 0.10,
    "Note": 0.07,
    "Citation": 0.03,
}
RELEVANCE_MASS = 0.65
AUTHORITY_MASS = 0.35


def _validate_inputs(
    provider_records: Sequence[Mapping[str, Any]],
    derived_penalty_record: Mapping[str, Any],
    policy_snapshot: Mapping[str, Any],
) -> tuple[str, dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
    snapshot = validate_retrieval_policy_snapshot(policy_snapshot)
    provider_policy = snapshot["provider_policy"]
    aggregation_policy = snapshot["aggregation_policy"]
    if provider_policy["weights"] != PROVIDER_WEIGHTS:
        raise ContractViolation("runtime provider weights differ from the frozen snapshot")
    if aggregation_policy["authority_mass_cap"] != AUTHORITY_MASS:
        raise ContractViolation("runtime authority mass differs from the frozen snapshot")
    if isinstance(provider_records, (str, bytes)) or not isinstance(
        provider_records, Sequence
    ):
        raise ContractViolation("provider_records must be a sequence")
    if not provider_records:
        raise ContractViolation("provider_records must contain exactly six records")
    first = provider_records[0]
    if not isinstance(first, Mapping):
        raise ContractViolation("provider_records[0] must be an object")
    candidate_id = first.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ContractViolation("candidate_id must be a non-empty string")
    checked_records = validate_provider_set(provider_records, candidate_id)
    records = {record["provider"]: record for record in checked_records}
    if tuple(records) != tuple(PROVIDERS):
        raise ContractViolation("validated provider order drifted from frozen six-provider set")
    if set(PROVIDER_WEIGHTS) != set(PROVIDERS):
        raise ContractViolation("provider weights do not match the frozen exact provider set")

    for provider, record in records.items():
        expected_version = provider_policy["provider_versions"][provider]
        if record["provider_version"] != expected_version:
            raise ContractViolation(
                "provider version differs from the frozen snapshot",
                context={"provider": provider},
            )
        if record["state"] == "observed":
            expected_profile = provider_policy["normalization_profiles"][provider]
            if record["normalization_profile"] != expected_profile:
                raise ContractViolation(
                    "provider normalization differs from the frozen snapshot",
                    context={"provider": provider},
                )

    penalty = validate_derived_penalty_against_snapshot(
        derived_penalty_record, snapshot
    )
    if penalty["candidate_id"] != candidate_id:
        raise IdentityConflict("provider and DerivedPenalty candidate identities differ")
    for provider in PROVIDERS:
        if records[provider]["state"] == "invalid":
            raise ContractViolation(
                "invalid provider blocks candidate aggregation",
                context={"candidate_id": candidate_id, "provider": provider},
            )
    return candidate_id, records, penalty, snapshot


def _observed_score(record: Mapping[str, Any]) -> float | None:
    if record["state"] != "observed":
        return None
    value = record["score"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractViolation("observed provider score must be numeric")
    score = float(value)
    if not math.isfinite(score) or score < 0.0 or score > 1.0:
        raise ContractViolation("observed provider score must be finite and in [0,1]")
    return score


def _availability(
    records: Mapping[str, Mapping[str, Any]],
    penalty: Mapping[str, Any],
) -> dict[str, Any]:
    observed_relevance = [
        provider
        for provider in RELEVANCE_PROVIDERS
        if _observed_score(records[provider]) is not None
    ]
    if not observed_relevance:
        raise ContractViolation(
            "all relevance providers are unavailable; candidate may not be ranked"
        )
    authority_blocked = penalty["authority_blocked"] is True
    observed_authority = [] if authority_blocked else [
        provider
        for provider in AUTHORITY_PROVIDERS
        if _observed_score(records[provider]) is not None
    ]
    relevance_observed_mass = math.fsum(
        PROVIDER_WEIGHTS[provider] for provider in observed_relevance
    )
    authority_observed_mass = math.fsum(
        PROVIDER_WEIGHTS[provider] for provider in observed_authority
    )
    missing_mass = math.fsum(
        PROVIDER_WEIGHTS[provider]
        for provider in PROVIDERS
        if records[provider]["state"] != "observed"
    )
    authority_blocked_mass = 0.0
    if authority_blocked:
        authority_blocked_mass = math.fsum(
            PROVIDER_WEIGHTS[provider]
            for provider in AUTHORITY_PROVIDERS
            if records[provider]["state"] == "observed"
        )
    return {
        "observed_relevance": observed_relevance,
        "observed_authority": observed_authority,
        "relevance_observed_mass": relevance_observed_mass,
        "authority_observed_mass": authority_observed_mass,
        "missing_mass": missing_mass,
        "authority_blocked_mass": authority_blocked_mass,
        "authority_blocked": authority_blocked,
    }


def _provider_state_report(
    records: Mapping[str, Mapping[str, Any]],
    availability: Mapping[str, Any],
) -> list[dict[str, Any]]:
    observed_authority = set(availability["observed_authority"])
    result: list[dict[str, Any]] = []
    for provider in PROVIDERS:
        record = records[provider]
        state = record["state"]
        usable = state == "observed"
        if provider in AUTHORITY_PROVIDERS and provider not in observed_authority:
            usable = False
        result.append(
            {
                "provider": provider,
                "group": (
                    "relevance" if provider in RELEVANCE_PROVIDERS else "authority"
                ),
                "configured_weight": PROVIDER_WEIGHTS[provider],
                "state": state,
                "usable_in_aggregation": usable,
                "reason_code": record["reason_code"],
            }
        )
    return result


def aggregate_p00_baseline(
    provider_records: Sequence[Mapping[str, Any]],
    derived_penalty_record: Mapping[str, Any],
    *,
    policy_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Replay the P00 v6.1 fixed-weight formula without missing renormalization."""

    candidate_id, records, penalty, snapshot = _validate_inputs(
        provider_records, derived_penalty_record, policy_snapshot
    )
    availability = _availability(records, penalty)
    effective_penalty = float(penalty["effective_penalty"])
    observed_relevance = set(availability["observed_relevance"])
    observed_authority = set(availability["observed_authority"])

    contributions: list[dict[str, Any]] = []
    relevance_terms: list[float] = []
    authority_terms_before_penalty: list[float] = []
    final_terms: list[float] = []
    for provider in PROVIDERS:
        score = _observed_score(records[provider])
        usable = (
            provider in observed_relevance
            if provider in RELEVANCE_PROVIDERS
            else provider in observed_authority
        )
        weighted = PROVIDER_WEIGHTS[provider] * score if score is not None and usable else 0.0
        if provider in RELEVANCE_PROVIDERS:
            relevance_terms.append(weighted)
            final_contribution = weighted
            penalty_applied = False
        else:
            authority_terms_before_penalty.append(weighted)
            final_contribution = weighted * effective_penalty
            penalty_applied = True
        final_terms.append(final_contribution)
        contributions.append(
            {
                "provider": provider,
                "state": records[provider]["state"],
                "score": score,
                "configured_weight": PROVIDER_WEIGHTS[provider],
                "weighted_before_penalty": weighted,
                "penalty_applied": penalty_applied,
                "final_contribution": final_contribution,
            }
        )

    relevance_subtotal = math.fsum(relevance_terms)
    authority_subtotal = math.fsum(authority_terms_before_penalty)
    final_score = math.fsum(final_terms)
    return {
        "object_type": "ScoreExplanation",
        "schema_version": "1.0",
        "candidate_id": candidate_id,
        "retrieval_policy_hash": snapshot["content_hash"],
        "policy_id": "policy_baseline_v6_1",
        "formula": "p00_fixed_weight_authority_penalty_only",
        "relevance_subtotal": relevance_subtotal,
        "authority_subtotal_before_penalty": authority_subtotal,
        "derived_penalty": effective_penalty,
        "authority_subtotal_after_penalty": authority_subtotal * effective_penalty,
        "configured_authority_mass": AUTHORITY_MASS,
        "lambda_authority": availability["authority_observed_mass"],
        "final_score": final_score,
        "relevance_coverage": availability["relevance_observed_mass"] / RELEVANCE_MASS,
        "authority_coverage": availability["authority_observed_mass"] / AUTHORITY_MASS,
        "missing_mass": availability["missing_mass"],
        "authority_blocked_mass": availability["authority_blocked_mass"],
        "provider_states": _provider_state_report(records, availability),
        "provider_contributions": contributions,
        "ranking_reason": "fixed P00 weights; RRF and MMR excluded from providers",
    }


def aggregate_availability_aware(
    provider_records: Sequence[Mapping[str, Any]],
    derived_penalty_record: Mapping[str, Any],
    *,
    policy_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the frozen availability-aware Shadow formula.

    Missing authority mass returns to relevance through ``1-lambda_A``.  If no
    authority provider is available (or provenance blocks authority),
    ``lambda_A`` is exactly zero.  The DerivedPenalty is applied only to the
    authority subtotal and never to relevance.
    """

    candidate_id, records, penalty, snapshot = _validate_inputs(
        provider_records, derived_penalty_record, policy_snapshot
    )
    availability = _availability(records, penalty)
    effective_penalty = float(penalty["effective_penalty"])
    relevance_mass = availability["relevance_observed_mass"]
    authority_mass = availability["authority_observed_mass"]
    if authority_mass > AUTHORITY_MASS:
        raise ContractViolation("observed authority mass exceeds frozen 0.35 cap")

    relevance_average = math.fsum(
        PROVIDER_WEIGHTS[provider] * float(records[provider]["score"])
        for provider in availability["observed_relevance"]
    ) / relevance_mass
    if authority_mass == 0.0:
        authority_average = 0.0
        lambda_authority = 0.0
    else:
        authority_average = math.fsum(
            PROVIDER_WEIGHTS[provider] * float(records[provider]["score"])
            for provider in availability["observed_authority"]
        ) / authority_mass
        lambda_authority = authority_mass

    contributions: list[dict[str, Any]] = []
    final_terms: list[float] = []
    observed_relevance = set(availability["observed_relevance"])
    observed_authority = set(availability["observed_authority"])
    for provider in PROVIDERS:
        score = _observed_score(records[provider])
        if provider in observed_relevance and score is not None:
            effective_weight = (1.0 - lambda_authority) * (
                PROVIDER_WEIGHTS[provider] / relevance_mass
            )
            weighted_before_penalty = effective_weight * score
            final_contribution = weighted_before_penalty
            penalty_applied = False
        elif provider in observed_authority and score is not None:
            effective_weight = lambda_authority * (
                PROVIDER_WEIGHTS[provider] / authority_mass
            )
            weighted_before_penalty = effective_weight * score
            final_contribution = weighted_before_penalty * effective_penalty
            penalty_applied = True
        else:
            effective_weight = 0.0
            weighted_before_penalty = 0.0
            final_contribution = 0.0
            penalty_applied = provider in AUTHORITY_PROVIDERS
        final_terms.append(final_contribution)
        contributions.append(
            {
                "provider": provider,
                "state": records[provider]["state"],
                "score": score,
                "configured_weight": PROVIDER_WEIGHTS[provider],
                "effective_weight": effective_weight,
                "weighted_before_penalty": weighted_before_penalty,
                "penalty_applied": penalty_applied,
                "final_contribution": final_contribution,
            }
        )

    final_score = math.fsum(final_terms)
    relevance_final_subtotal = (1.0 - lambda_authority) * relevance_average
    authority_before_penalty = lambda_authority * authority_average
    return {
        "object_type": "ScoreExplanation",
        "schema_version": "1.0",
        "candidate_id": candidate_id,
        "retrieval_policy_hash": snapshot["content_hash"],
        "policy_id": "policy_shadow_availability_aware_v1",
        "formula": "availability_aware_authority_penalty_only",
        "relevance_average": relevance_average,
        "authority_average": authority_average,
        "relevance_subtotal": relevance_final_subtotal,
        "authority_subtotal_before_penalty": authority_before_penalty,
        "derived_penalty": effective_penalty,
        "authority_subtotal_after_penalty": authority_before_penalty * effective_penalty,
        "lambda_authority": lambda_authority,
        "final_score": final_score,
        "relevance_coverage": relevance_mass / RELEVANCE_MASS,
        "authority_coverage": authority_mass / AUTHORITY_MASS,
        "missing_mass": availability["missing_mass"],
        "authority_blocked_mass": availability["authority_blocked_mass"],
        "provider_states": _provider_state_report(records, availability),
        "provider_contributions": contributions,
        "ranking_reason": "availability-aware six-provider formula; RRF and MMR excluded",
    }


def aggregate_both_policies(
    provider_records: Sequence[Mapping[str, Any]],
    derived_penalty_record: Mapping[str, Any],
    *,
    policy_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Return separate baseline and Shadow explanations; never mix policies."""

    baseline = aggregate_p00_baseline(
        provider_records, derived_penalty_record, policy_snapshot=policy_snapshot
    )
    shadow = aggregate_availability_aware(
        provider_records, derived_penalty_record, policy_snapshot=policy_snapshot
    )
    if baseline["candidate_id"] != shadow["candidate_id"]:
        raise IdentityConflict("policy results unexpectedly differ in candidate identity")
    return {
        "candidate_id": baseline["candidate_id"],
        "baseline": baseline,
        "availability_aware": shadow,
    }


def rank_score_explanations(
    explanations: Sequence[Mapping[str, Any]],
    *,
    policy_snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Rank explanations by score, then stable candidate ID for exact ties."""

    snapshot = validate_retrieval_policy_snapshot(policy_snapshot)
    allowed_policy_ids = {
        snapshot["aggregation_policy"]["baseline_policy_id"],
        snapshot["aggregation_policy"]["shadow_policy_id"],
    }

    if isinstance(explanations, (str, bytes)) or not isinstance(explanations, Sequence):
        raise ContractViolation("explanations must be a sequence")
    checked: list[dict[str, Any]] = []
    candidate_ids: set[str] = set()
    policy_ids: set[str] = set()
    for raw in explanations:
        if not isinstance(raw, Mapping):
            raise ContractViolation("score explanation must be an object")
        item = deepcopy(dict(raw))
        candidate_id = item.get("candidate_id")
        policy_id = item.get("policy_id")
        score = item.get("final_score")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ContractViolation("score explanation candidate_id is invalid")
        if candidate_id in candidate_ids:
            raise IdentityConflict("score explanations contain duplicate candidate_id")
        candidate_ids.add(candidate_id)
        if not isinstance(policy_id, str) or not policy_id:
            raise ContractViolation("score explanation policy_id is invalid")
        if policy_id not in allowed_policy_ids:
            raise ContractViolation("score explanation policy_id is outside the frozen snapshot")
        if item.get("retrieval_policy_hash") != snapshot["content_hash"]:
            raise ContractViolation("score explanation retrieval policy hash drift")
        policy_ids.add(policy_id)
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise ContractViolation("score explanation final_score must be finite")
        checked.append(item)
    if len(policy_ids) > 1:
        raise ContractViolation("rank_score_explanations may not mix aggregation policies")
    checked.sort(key=lambda item: (-float(item["final_score"]), item["candidate_id"]))
    return [{**item, "rank": ordinal} for ordinal, item in enumerate(checked, 1)]
