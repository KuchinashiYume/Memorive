from __future__ import annotations

from memorive_settings.retrieval_exam_calibration import (
    EMBEDDING_ROLE_ADEQUACY_POLICY,
    RERANKER_TWO_SLOT_POLICY,
    assess_embedding_role_adequacy,
    calibrate_reranker_series,
)


def _reranker_slot(
    slot: int,
    score: float,
    *,
    form_a: float,
    form_b: float,
    repeat_a: float,
    repeat_b: float,
    weakest_family: float,
) -> dict[str, object]:
    families = {
        "RR1": weakest_family,
        "RR2": 96.0,
        "RR3": 94.0,
        "RR4": 98.0,
        "RR5": 96.0,
        "RR6": 90.0,
        "RR7": 99.0,
    }
    return {
        "sample_slot": slot,
        "sample_manifest_sha256": str(slot) * 64,
        "score_exact": score,
        "form_scores": {"A": form_a, "B": form_b},
        "family_means_by_form": {
            "A": dict(families),
            "B": dict(families),
        },
        "repeatability_by_form": {"A": repeat_a, "B": repeat_b},
        "execution_outcome": "COMPLETED",
        "scorability": "SCOREABLE",
    }


def test_reranker_two_slot_calibration_separates_quality_from_operational_tier() -> None:
    dedicated = calibrate_reranker_series(
        [
            _reranker_slot(
                1,
                82.793205,
                form_a=86.238787,
                form_b=79.347624,
                repeat_a=100.0,
                repeat_b=100.0,
                weakest_family=67.0,
            ),
            _reranker_slot(
                2,
                81.282802,
                form_a=78.591370,
                form_b=83.974233,
                repeat_a=100.0,
                repeat_b=100.0,
                weakest_family=67.0,
            ),
        ]
    )
    assert dedicated["status"] == "ASSESSED"
    assert dedicated["score_exact"] == 82.038004
    assert dedicated["quality_verdict"] == "PASS"
    assert dedicated["operational_eligibility_verdict"] == (
        "SUITABLE_WITH_DOMAIN_GUARD"
    )
    assert dedicated["model_fail_established"] is False

    general_cli = calibrate_reranker_series(
        [
            _reranker_slot(
                1,
                92.595384,
                form_a=93.012380,
                form_b=92.178388,
                repeat_a=85.777778,
                repeat_b=78.666667,
                weakest_family=89.0,
            ),
            _reranker_slot(
                2,
                93.745409,
                form_a=93.023141,
                form_b=94.467677,
                repeat_a=82.222222,
                repeat_b=84.0,
                weakest_family=89.0,
            ),
        ]
    )
    assert general_cli["score_exact"] == 93.170396
    assert general_cli["quality_verdict"] == "PASS"
    assert general_cli["operational_eligibility_verdict"] == (
        "SUITABLE_WITH_STABILITY_MONITORING"
    )

    local = calibrate_reranker_series(
        [
            _reranker_slot(
                1,
                93.514865,
                form_a=93.833846,
                form_b=93.195884,
                repeat_a=100.0,
                repeat_b=100.0,
                weakest_family=90.0,
            ),
            _reranker_slot(
                2,
                94.239489,
                form_a=94.263757,
                form_b=94.215221,
                repeat_a=100.0,
                repeat_b=100.0,
                weakest_family=90.0,
            ),
        ]
    )
    assert local["score_exact"] == 93.877177
    assert local["operational_eligibility_verdict"] == "SUITABLE"


def test_reranker_one_slot_or_unstable_series_does_not_establish_model_fail() -> None:
    slot = _reranker_slot(
        1,
        92.0,
        form_a=92.0,
        form_b=92.0,
        repeat_a=100.0,
        repeat_b=100.0,
        weakest_family=90.0,
    )
    incomplete = calibrate_reranker_series([slot])
    assert incomplete["status"] == "NOT_ASSESSED"
    assert incomplete["quality_verdict"] == "NOT_ASSESSED"
    assert incomplete["model_fail_verdict"] == "NOT_ESTABLISHED"

    unstable = calibrate_reranker_series(
        [
            slot,
            _reranker_slot(
                2,
                75.0,
                form_a=75.0,
                form_b=75.0,
                repeat_a=70.0,
                repeat_b=70.0,
                weakest_family=50.0,
            ),
        ]
    )
    assert unstable["quality_verdict"] == "FAIL"
    assert unstable["model_fail_established"] is False
    assert unstable["operational_eligibility_verdict"] == (
        "NOT_RECOMMENDED_CURRENT_CONFIGURATION"
    )


def test_embedding_role_score_uses_recall_and_keeps_stress_score_separate() -> None:
    local = assess_embedding_role_adequacy(
        {
            "production_recall_score": 95.136905,
            "critical_evidence_recall_at_15": 99.583333,
            "facet_coverage_at_15": 100.0,
            "source_scope_integrity_at_15": 2.916667,
            "clean_control_recall_at_10": 100.0,
        },
        {
            "exact_top10_rate_percent": 100.0,
            "minimum_vector_cosine": 1.0,
        },
    )
    assert local["quality_verdict"] == "PASS"
    assert local["score_exact"] == 95.136905
    assert local["operational_eligibility_verdict"] == (
        "SUITABLE_WITH_RERANKER_AND_SOURCE_GUARD"
    )
    assert local["source_scope_integrity_is_admission_gate"] is False
    assert local["adversarial_stress_score_combined_into_primary"] is False
    assert local["model_fail_established"] is False

    api = assess_embedding_role_adequacy(
        {
            "production_recall_score": 95.171131,
            "critical_evidence_recall_at_15": 99.635417,
            "facet_coverage_at_15": 100.0,
            "source_scope_integrity_at_15": 2.875,
            "clean_control_recall_at_10": 100.0,
        },
        {
            "exact_top10_rate_percent": 91.0,
            "minimum_vector_cosine": 0.998530798902,
        },
    )
    assert api["quality_verdict"] == "PASS"
    assert api["operational_eligibility_verdict"] == (
        "SUITABLE_WITH_RERANKER_STABILITY_MONITORING_AND_SOURCE_GUARD"
    )


def test_embedding_role_threshold_failure_is_configuration_not_model_failure() -> None:
    result = assess_embedding_role_adequacy(
        {
            "production_recall_score": 89.9,
            "critical_evidence_recall_at_15": 97.9,
            "facet_coverage_at_15": 98.9,
            "source_scope_integrity_at_15": 100.0,
            "clean_control_recall_at_10": 97.9,
        },
        {"exact_top10_rate_percent": 89.9},
    )
    assert result["quality_verdict"] == "FAIL"
    assert result["model_fail_verdict"] == "NOT_ESTABLISHED"
    assert result["model_fail_established"] is False


def test_retrieval_thresholds_are_role_based_and_formal_qualification_stays_false() -> None:
    assert RERANKER_TWO_SLOT_POLICY["minimum_series_score"] == 80.0
    assert RERANKER_TWO_SLOT_POLICY["required_distinct_slots"] == 2
    assert EMBEDDING_ROLE_ADEQUACY_POLICY[
        "minimum_critical_evidence_recall_at_15"
    ] == 98.0
    assert EMBEDDING_ROLE_ADEQUACY_POLICY[
        "formal_qualification_eligible"
    ] is False
