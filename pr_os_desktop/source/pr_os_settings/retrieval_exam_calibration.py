from __future__ import annotations

import hashlib
import json
import statistics
from typing import Any, Mapping, Sequence


RERANKER_TWO_SLOT_POLICY: dict[str, Any] = {
    "revision": "P08_RERANKER_TWO_SLOT_ROLE_ADEQUACY_CALIBRATION_V1",
    "required_distinct_slots": 2,
    "minimum_series_score": 80.0,
    "minimum_slot_score": 78.0,
    "maximum_slot_spread": 5.0,
    "minimum_family_series_mean": 65.0,
    "minimum_repeatability_mean": 80.0,
    "preferred_series_score": 90.0,
    "preferred_minimum_slot_score": 88.0,
    "preferred_maximum_slot_spread": 3.0,
    "preferred_minimum_family_series_mean": 85.0,
    "preferred_repeatability_mean": 95.0,
    "formal_qualification_eligible": False,
    "single_slot_may_establish_quality_verdict": False,
    "aggregate_quality_fail_may_establish_model_fail": False,
}


EMBEDDING_ROLE_ADEQUACY_POLICY: dict[str, Any] = {
    "revision": "P08_EMBEDDING_FIRST_STAGE_RECALL_ROLE_ADEQUACY_V1",
    "minimum_production_recall_score": 90.0,
    "minimum_critical_evidence_recall_at_15": 98.0,
    "minimum_facet_coverage_at_15": 99.0,
    "minimum_clean_control_recall_at_10": 98.0,
    "minimum_exact_top10_repeatability_percent": 90.0,
    "minimum_vector_cosine_repeatability": 0.998,
    "preferred_exact_top10_repeatability_percent": 99.5,
    "preferred_vector_cosine_repeatability": 0.999999,
    "source_scope_integrity_is_admission_gate": False,
    "source_scope_integrity_owner": "RERANKER_AND_DOWNSTREAM_SOURCE_GUARD",
    "adversarial_stress_score_combined_into_primary": False,
    "formal_qualification_eligible": False,
    "single_reference_regression_may_establish_model_fail": False,
}


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest().upper()


def _number(value: Any, field: str, *, minimum: float = 0.0) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not minimum <= float(value) <= 100.0
    ):
        raise ValueError(f"RETRIEVAL_CALIBRATION_NUMBER_INVALID:{field}")
    return float(value)


def _not_assessed_reranker(reason: str) -> dict[str, Any]:
    result = {
        "schema_version": "RerankerTwoSlotRoleAdequacy-v1",
        "status": "NOT_ASSESSED",
        "reason": reason,
        "score_exact": None,
        "quality_verdict": "NOT_ASSESSED",
        "operational_eligibility_verdict": "NOT_ASSESSED",
        "formal_qualification_eligible": False,
        "model_fail_verdict": "NOT_ESTABLISHED",
        "model_fail_established": False,
        "policy": dict(RERANKER_TWO_SLOT_POLICY),
    }
    result["assessment_sha256"] = _canonical_sha256(result)
    return result


def calibrate_reranker_series(
    slots: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Assess two disjoint reranker samples without promoting a model FAIL.

    A single slot remains descriptive.  Two complete, distinct slots may
    establish current-configuration role adequacy, but a failed aggregate is
    still not sufficient evidence that the underlying model itself has failed.
    """

    if len(slots) != int(RERANKER_TWO_SLOT_POLICY["required_distinct_slots"]):
        return _not_assessed_reranker("TWO_DISTINCT_SAMPLE_SLOTS_REQUIRED")
    rows = [dict(row) for row in slots]
    if any(
        row.get("execution_outcome") != "COMPLETED"
        or row.get("scorability") != "SCOREABLE"
        for row in rows
    ):
        return _not_assessed_reranker("COMPLETE_SCOREABLE_SLOTS_REQUIRED")
    slot_numbers = [row.get("sample_slot") for row in rows]
    manifests = [row.get("sample_manifest_sha256") for row in rows]
    if (
        sorted(slot_numbers) != [1, 2]
        or len(set(manifests)) != 2
        or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(ch not in "0123456789abcdefABCDEF" for ch in value)
            for value in manifests
        )
    ):
        raise ValueError("RERANKER_TWO_SLOT_IDENTITY_INVALID")

    slot_scores = [
        _number(row.get("score_exact"), "score_exact") for row in rows
    ]
    form_scores: list[float] = []
    repeatability: list[float] = []
    family_values: dict[str, list[float]] = {
        family: [] for family in ("RR1", "RR2", "RR3", "RR4", "RR5", "RR6", "RR7")
    }
    for row in rows:
        forms = row.get("form_scores")
        repeats = row.get("repeatability_by_form")
        families = row.get("family_means_by_form")
        if (
            not isinstance(forms, Mapping)
            or set(forms) != {"A", "B"}
            or not isinstance(repeats, Mapping)
            or set(repeats) != {"A", "B"}
            or not isinstance(families, Mapping)
            or set(families) != {"A", "B"}
        ):
            raise ValueError("RERANKER_TWO_SLOT_COMPONENTS_INVALID")
        for form_id in ("A", "B"):
            form_scores.append(
                _number(forms[form_id], f"form_scores.{form_id}")
            )
            repeatability.append(
                _number(repeats[form_id], f"repeatability.{form_id}")
            )
            family_row = families[form_id]
            if not isinstance(family_row, Mapping) or set(family_row) != set(
                family_values
            ):
                raise ValueError("RERANKER_TWO_SLOT_FAMILY_SET_INVALID")
            for family, value in family_row.items():
                family_values[str(family)].append(
                    _number(value, f"family.{family}")
                )

    score_exact = round(statistics.fmean(slot_scores), 6)
    minimum_slot = min(slot_scores)
    spread = round(max(slot_scores) - minimum_slot, 6)
    repeatability_mean = round(statistics.fmean(repeatability), 6)
    family_series_means = {
        family: round(statistics.fmean(values), 6)
        for family, values in family_values.items()
    }
    minimum_family = min(family_series_means.values())
    failed_thresholds: list[str] = []
    if score_exact < RERANKER_TWO_SLOT_POLICY["minimum_series_score"]:
        failed_thresholds.append("SERIES_SCORE")
    if minimum_slot < RERANKER_TWO_SLOT_POLICY["minimum_slot_score"]:
        failed_thresholds.append("MINIMUM_SLOT_SCORE")
    if spread > RERANKER_TWO_SLOT_POLICY["maximum_slot_spread"]:
        failed_thresholds.append("SLOT_SPREAD")
    if (
        minimum_family
        < RERANKER_TWO_SLOT_POLICY["minimum_family_series_mean"]
    ):
        failed_thresholds.append("FAMILY_SERIES_MEAN")
    if (
        repeatability_mean
        < RERANKER_TWO_SLOT_POLICY["minimum_repeatability_mean"]
    ):
        failed_thresholds.append("REPEATABILITY")

    quality = "FAIL" if failed_thresholds else "PASS"
    if quality == "FAIL":
        operational = "NOT_RECOMMENDED_CURRENT_CONFIGURATION"
    elif (
        score_exact >= RERANKER_TWO_SLOT_POLICY["preferred_series_score"]
        and minimum_slot
        >= RERANKER_TWO_SLOT_POLICY["preferred_minimum_slot_score"]
        and spread
        <= RERANKER_TWO_SLOT_POLICY["preferred_maximum_slot_spread"]
        and minimum_family
        >= RERANKER_TWO_SLOT_POLICY[
            "preferred_minimum_family_series_mean"
        ]
        and repeatability_mean
        >= RERANKER_TWO_SLOT_POLICY["preferred_repeatability_mean"]
    ):
        operational = "SUITABLE"
    elif (
        score_exact >= RERANKER_TWO_SLOT_POLICY["preferred_series_score"]
        and minimum_family
        >= RERANKER_TWO_SLOT_POLICY[
            "preferred_minimum_family_series_mean"
        ]
    ):
        operational = "SUITABLE_WITH_STABILITY_MONITORING"
    else:
        operational = "SUITABLE_WITH_DOMAIN_GUARD"

    calibration_residuals: list[str] = []
    if quality == "PASS":
        if score_exact < RERANKER_TWO_SLOT_POLICY["preferred_series_score"]:
            calibration_residuals.append("BELOW_PREFERRED_SERIES_SCORE")
        if (
            minimum_family
            < RERANKER_TWO_SLOT_POLICY[
                "preferred_minimum_family_series_mean"
            ]
        ):
            calibration_residuals.append("DOMAIN_FAMILY_GUARD_REQUIRED")
        if (
            repeatability_mean
            < RERANKER_TWO_SLOT_POLICY["preferred_repeatability_mean"]
        ):
            calibration_residuals.append("STABILITY_MONITORING_REQUIRED")

    result = {
        "schema_version": "RerankerTwoSlotRoleAdequacy-v1",
        "status": "ASSESSED",
        "score_exact": score_exact,
        "slot_scores": [
            {
                "sample_slot": int(row["sample_slot"]),
                "sample_manifest_sha256": str(
                    row["sample_manifest_sha256"]
                ).upper(),
                "score_exact": round(score, 6),
            }
            for row, score in sorted(
                zip(rows, slot_scores),
                key=lambda pair: int(pair[0]["sample_slot"]),
            )
        ],
        "minimum_slot_score": round(minimum_slot, 6),
        "slot_score_spread": spread,
        "minimum_form_score": round(min(form_scores), 6),
        "repeatability_mean": repeatability_mean,
        "repeatability_minimum": round(min(repeatability), 6),
        "family_series_means": family_series_means,
        "minimum_family_series_mean": round(minimum_family, 6),
        "failed_thresholds": failed_thresholds,
        "calibration_residuals": calibration_residuals,
        "quality_verdict": quality,
        "operational_eligibility_verdict": operational,
        "formal_qualification_eligible": False,
        "model_fail_verdict": "NOT_ESTABLISHED",
        "model_fail_established": False,
        "policy": dict(RERANKER_TWO_SLOT_POLICY),
    }
    result["assessment_sha256"] = _canonical_sha256(result)
    return result


def assess_embedding_role_adequacy(
    production_score: Mapping[str, Any],
    repeatability: Mapping[str, Any],
) -> dict[str, Any]:
    """Assess first-stage retrieval without importing reranker responsibilities."""

    metrics = {
        "production_recall_score": _number(
            production_score.get("production_recall_score"),
            "production_recall_score",
        ),
        "critical_evidence_recall_at_15": _number(
            production_score.get("critical_evidence_recall_at_15"),
            "critical_evidence_recall_at_15",
        ),
        "facet_coverage_at_15": _number(
            production_score.get("facet_coverage_at_15"),
            "facet_coverage_at_15",
        ),
        "source_scope_integrity_at_15": _number(
            production_score.get("source_scope_integrity_at_15"),
            "source_scope_integrity_at_15",
        ),
        "clean_control_recall_at_10": _number(
            production_score.get("clean_control_recall_at_10"),
            "clean_control_recall_at_10",
        ),
    }
    exact_rate_value = repeatability.get("exact_top10_rate_percent")
    cosine_value = repeatability.get("minimum_vector_cosine")
    exact_rate = (
        None
        if exact_rate_value is None
        else _number(exact_rate_value, "exact_top10_rate_percent")
    )
    if cosine_value is None:
        minimum_cosine = None
    elif (
        isinstance(cosine_value, bool)
        or not isinstance(cosine_value, (int, float))
        or not -1.0 <= float(cosine_value) <= 1.0
    ):
        raise ValueError(
            "RETRIEVAL_CALIBRATION_NUMBER_INVALID:minimum_vector_cosine"
        )
    else:
        minimum_cosine = float(cosine_value)
    if exact_rate is None and minimum_cosine is None:
        result = {
            "schema_version": "EmbeddingFirstStageRoleAdequacy-v1",
            "status": "NOT_ASSESSED",
            "reason": "REPEATABILITY_EVIDENCE_REQUIRED",
            "score_exact": round(metrics["production_recall_score"], 6),
            "quality_verdict": "NOT_ASSESSED",
            "operational_eligibility_verdict": "NOT_ASSESSED",
            "formal_qualification_eligible": False,
            "model_fail_verdict": "NOT_ESTABLISHED",
            "model_fail_established": False,
            "policy": dict(EMBEDDING_ROLE_ADEQUACY_POLICY),
        }
        result["assessment_sha256"] = _canonical_sha256(result)
        return result

    failed_thresholds: list[str] = []
    threshold_pairs = (
        (
            "PRODUCTION_RECALL_SCORE",
            metrics["production_recall_score"],
            EMBEDDING_ROLE_ADEQUACY_POLICY[
                "minimum_production_recall_score"
            ],
        ),
        (
            "CRITICAL_EVIDENCE_RECALL_AT_15",
            metrics["critical_evidence_recall_at_15"],
            EMBEDDING_ROLE_ADEQUACY_POLICY[
                "minimum_critical_evidence_recall_at_15"
            ],
        ),
        (
            "FACET_COVERAGE_AT_15",
            metrics["facet_coverage_at_15"],
            EMBEDDING_ROLE_ADEQUACY_POLICY[
                "minimum_facet_coverage_at_15"
            ],
        ),
        (
            "CLEAN_CONTROL_RECALL_AT_10",
            metrics["clean_control_recall_at_10"],
            EMBEDDING_ROLE_ADEQUACY_POLICY[
                "minimum_clean_control_recall_at_10"
            ],
        ),
    )
    for name, value, threshold in threshold_pairs:
        if value < threshold:
            failed_thresholds.append(name)
    repeatability_pass = (
        exact_rate is not None
        and exact_rate
        >= EMBEDDING_ROLE_ADEQUACY_POLICY[
            "minimum_exact_top10_repeatability_percent"
        ]
    ) or (
        minimum_cosine is not None
        and minimum_cosine
        >= EMBEDDING_ROLE_ADEQUACY_POLICY[
            "minimum_vector_cosine_repeatability"
        ]
    )
    if not repeatability_pass:
        failed_thresholds.append("REPEATABILITY")

    quality = "FAIL" if failed_thresholds else "PASS"
    preferred_stability = (
        exact_rate is not None
        and exact_rate
        >= EMBEDDING_ROLE_ADEQUACY_POLICY[
            "preferred_exact_top10_repeatability_percent"
        ]
    ) or (
        exact_rate is None
        and minimum_cosine is not None
        and minimum_cosine
        >= EMBEDDING_ROLE_ADEQUACY_POLICY[
            "preferred_vector_cosine_repeatability"
        ]
    )
    if quality == "FAIL":
        operational = "NOT_RECOMMENDED_CURRENT_CONFIGURATION"
    elif preferred_stability:
        operational = "SUITABLE_WITH_RERANKER_AND_SOURCE_GUARD"
    else:
        operational = (
            "SUITABLE_WITH_RERANKER_STABILITY_MONITORING_AND_SOURCE_GUARD"
        )

    result = {
        "schema_version": "EmbeddingFirstStageRoleAdequacy-v1",
        "status": "ASSESSED",
        "score_exact": round(metrics["production_recall_score"], 6),
        "metrics": {key: round(value, 6) for key, value in metrics.items()},
        "repeatability": {
            "exact_top10_rate_percent": (
                None if exact_rate is None else round(exact_rate, 6)
            ),
            "minimum_vector_cosine": (
                None
                if minimum_cosine is None
                else round(minimum_cosine, 12)
            ),
            "threshold_pass": repeatability_pass,
        },
        "failed_thresholds": failed_thresholds,
        "quality_verdict": quality,
        "operational_eligibility_verdict": operational,
        "source_scope_integrity_is_admission_gate": False,
        "adversarial_stress_score_combined_into_primary": False,
        "formal_qualification_eligible": False,
        "model_fail_verdict": "NOT_ESTABLISHED",
        "model_fail_established": False,
        "policy": dict(EMBEDDING_ROLE_ADEQUACY_POLICY),
    }
    result["assessment_sha256"] = _canonical_sha256(result)
    return result


__all__ = [
    "EMBEDDING_ROLE_ADEQUACY_POLICY",
    "RERANKER_TWO_SLOT_POLICY",
    "assess_embedding_role_adequacy",
    "calibrate_reranker_series",
]
