from __future__ import annotations

import pytest

from memorive.exams.outcomes import (
    CategoryBinding,
    ExecutionOutcome,
    Scorability,
    TerminalDisposition,
    admit_quality_fail,
    analysis_cohort_model_fail_decision,
    analysis_material_repair_targets,
    analysis_repair_acceptance,
    build_analysis_panel_cohort,
    build_cache_binding,
    build_repair_round,
    cache_reuse_status,
    category_comparison_allowed,
    classify_execution_outcome,
    clean_control_residual,
    cumulative_category_repair_penalty,
    determine_scorability,
    terminal_disposition,
)


SHA_A = "A" * 64
SHA_B = "B" * 64
SHA_C = "C" * 64
SHA_D = "D" * 64


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("API_AUTH_ERROR", ExecutionOutcome.AUTH_ERROR),
        ("HTTP_429_RATE_LIMITED", ExecutionOutcome.RATE_LIMIT),
        ("PROVIDER_CAPACITY_OVERLOADED", ExecutionOutcome.CAPACITY),
        ("CLI_STRUCTURED_CHAT_TIMEOUT", ExecutionOutcome.TIMEOUT),
        ("POLICY_REFUSAL", ExecutionOutcome.SAFETY_REFUSAL),
        ("STRUCTURED_CHAT_JSON_TRUNCATED", ExecutionOutcome.OUTPUT_TRUNCATED),
        ("STRUCTURED_CHAT_RESPONSE_EMPTY", ExecutionOutcome.EMPTY_RESPONSE),
        ("STRUCTURED_CHAT_RESPONSE_INVALID", ExecutionOutcome.INVALID_RESPONSE),
        ("UNSUPPORTED_CAPABILITY", ExecutionOutcome.CAPABILITY_MISMATCH),
        ("REFERENCE_PACK_UNAVAILABLE", ExecutionOutcome.PACK_UNAVAILABLE),
        ("CLI_NONZERO_EXIT", ExecutionOutcome.PROCESS_FAILURE),
        ("API_NETWORK_OR_RESPONSE_FAILURE", ExecutionOutcome.TRANSPORT_ERROR),
    ],
)
def test_execution_failures_never_default_to_quality_fail(reason, expected):
    outcome = classify_execution_outcome({"status": "NOT_RUN", "reason": reason})
    assert outcome is expected
    assert determine_scorability(
        execution_outcome=outcome,
        response_complete=False,
        schema_valid=False,
        exact_case_set=False,
    ) is Scorability.NOT_ASSESSED


def test_complete_response_must_be_schema_and_exact_set_valid_to_score():
    assert determine_scorability(
        execution_outcome="COMPLETED",
        response_complete=True,
        schema_valid=True,
        exact_case_set=True,
    ) is Scorability.SCOREABLE
    assert determine_scorability(
        execution_outcome="COMPLETED",
        response_complete=True,
        schema_valid=False,
        exact_case_set=True,
        deterministic_mechanical_repair_available=True,
    ) is Scorability.MECHANICALLY_RECOVERABLE
    assert determine_scorability(
        execution_outcome="COMPLETED",
        response_complete=False,
        schema_valid=True,
        exact_case_set=False,
    ) is Scorability.PARTIAL


def test_clean_control_conservative_review_is_calibration_not_safety_block():
    conservative = clean_control_residual(
        expected_verdict="PASS",
        observed_verdict="FAIL",
        observed_action="REVIEW",
    )
    assert conservative == {
        "classification": "CALIBRATION",
        "safety_blocking": False,
        "reason_code": "CONSERVATIVE_FALSE_POSITIVE_ON_CLEAN_CONTROL",
    }
    destructive = clean_control_residual(
        expected_verdict="PASS",
        observed_verdict="FAIL",
        observed_action="TOMBSTONE",
    )
    assert destructive["safety_blocking"] is True
    assert destructive["reason_code"] == "DESTRUCTIVE_FALSE_POSITIVE_ON_CLEAN_CONTROL"


def test_round_two_is_gold_blind_residual_subset_and_separately_hashed():
    first = build_repair_round(
        round_number=1,
        category_id="CARD_REVIEWER",
        targets_by_form={"A": ["A-1", "A-2"], "B": ["B-1"]},
        prior_targets_by_form=None,
        prompt_projection={"failed_fields": ["verdict", "evidence_refs"]},
        unchanged_surface_sha256=SHA_A,
    )
    second = build_repair_round(
        round_number=2,
        category_id="CARD_REVIEWER",
        targets_by_form={"A": ["A-2"]},
        prior_targets_by_form=first["wrong_item_exact_set"],
        prompt_projection={"failed_fields": ["evidence_refs"], "residual_only": True},
        unchanged_surface_sha256=SHA_A,
    )
    assert second["wrong_item_exact_set"] == {"A": ["A-2"]}
    assert second["prior_wrong_item_exact_set_sha256"] == first["wrong_item_exact_set_sha256"]
    assert second["provider_receives_gold"] is False
    assert second["contract_sha256"] != first["contract_sha256"]
    with pytest.raises(ValueError, match="ROUND2_MUST_BE_ROUND1_RESIDUAL_SUBSET"):
        build_repair_round(
            round_number=2,
            category_id="CARD_REVIEWER",
            targets_by_form={"A": ["A-3"]},
            prior_targets_by_form=first["wrong_item_exact_set"],
            prompt_projection={"failed_fields": ["verdict"]},
            unchanged_surface_sha256=SHA_A,
        )
    with pytest.raises(ValueError, match="GOLD_OR_ANSWER_KEY"):
        build_repair_round(
            round_number=1,
            category_id="CARD_REVIEWER",
            targets_by_form={"A": ["A-1"]},
            prior_targets_by_form=None,
            prompt_projection={"answer_key": {"A-1": "PASS"}},
            unchanged_surface_sha256=SHA_A,
        )


def test_round_two_penalty_is_cumulative_stricter_without_cross_category_weights():
    value = cumulative_category_repair_penalty(
        [
            {
                "round_number": 1,
                "category_id": "ANALYSIS_REVIEWER",
                "nonlinear_category_penalty": 3.5,
                "fixed_penalty": 1.0,
                "volume_penalty": 0.5,
            },
            {
                "round_number": 2,
                "category_id": "ANALYSIS_REVIEWER",
                "nonlinear_category_penalty": 2.0,
                "fixed_penalty": 1.0,
                "volume_penalty": 1.0,
            },
        ]
    )
    assert value["rounds"][1]["cumulative_penalty"] > value["rounds"][0]["cumulative_penalty"]
    assert value["cross_category_coefficients_reused"] is False
    with pytest.raises(ValueError, match="CROSS_CATEGORY"):
        cumulative_category_repair_penalty(
            [
                {"round_number": 1, "category_id": "CARD_REVIEWER", "fixed_penalty": 1.0},
                {"round_number": 2, "category_id": "ANALYSIS_REVIEWER", "fixed_penalty": 1.0},
            ]
        )


def test_quality_fail_requires_completed_scoreable_evidence_and_two_round_exhaustion():
    blocking = [
        {
            "case_id": "A-1",
            "failed_fields": ["evidence_refs"],
            "evidence_anchor": "score/forms/A/details/A-1",
            "blocking": True,
        }
    ]
    admitted = admit_quality_fail(
        execution_outcome="COMPLETED",
        scorability="SCOREABLE",
        repair_rounds_used=2,
        repair_round_limit=2,
        blocking_failures=blocking,
    )
    assert admitted["quality_verdict"] == "FAIL"
    assert admitted["fail_admitted"] is True
    refused = admit_quality_fail(
        execution_outcome="SAFETY_REFUSAL",
        scorability="NOT_ASSESSED",
        repair_rounds_used=0,
        repair_round_limit=2,
        blocking_failures=blocking,
    )
    assert refused["quality_verdict"] == "NOT_ASSESSED"
    assert "EXECUTION_NOT_COMPLETED" in refused["reason_codes"]


def test_quality_fail_allows_explicit_early_cycle_close_but_never_assumes_it():
    blocking = [
        {
            "case_id": "AGGREGATE",
            "failed_fields": ["repair_burden_below_category_threshold"],
            "evidence_anchor": "exam_result/aggregate",
            "blocking": True,
        }
    ]
    implicit = admit_quality_fail(
        execution_outcome="COMPLETED",
        scorability="SCOREABLE",
        repair_rounds_used=1,
        repair_round_limit=2,
        blocking_failures=blocking,
    )
    assert implicit["quality_verdict"] == "NOT_ASSESSED"
    assert "SEMANTIC_REPAIR_CYCLE_NOT_CLOSED" in implicit["reason_codes"]

    explicit = admit_quality_fail(
        execution_outcome="COMPLETED",
        scorability="SCOREABLE",
        repair_rounds_used=1,
        repair_round_limit=2,
        repair_cycle_closed=True,
        blocking_failures=blocking,
    )
    assert explicit["quality_verdict"] == "FAIL"
    assert explicit["repair_cycle_closed"] is True
    assert explicit["repair_rounds_exhausted"] is False


def test_terminal_disposition_is_not_binary_fail_for_calibration_or_governed_close():
    assert terminal_disposition(
        scorability="SCOREABLE",
        residual_count=2,
        safety_blocking_residual_count=0,
        calibration_residual_count=2,
        governed_disposition_count=0,
    ) is TerminalDisposition.SUITABLE_WITH_CALIBRATION
    assert terminal_disposition(
        scorability="SCOREABLE",
        residual_count=1,
        safety_blocking_residual_count=0,
        calibration_residual_count=0,
        governed_disposition_count=1,
    ) is TerminalDisposition.SUITABLE_WITH_GOVERNED_DISPOSITION
    assert terminal_disposition(
        scorability="SCOREABLE",
        residual_count=1,
        safety_blocking_residual_count=1,
        calibration_residual_count=0,
        governed_disposition_count=0,
    ) is TerminalDisposition.MANUAL_REVIEW_REQUIRED


def _binding(category="CARD_REVIEWER", sample="SLOT_1"):
    return CategoryBinding(
        category_id=category,
        role_id=category,
        reference_pack_id="PACK",
        reference_pack_revision="r1",
        reference_pack_sha256=SHA_A,
        scoring_protocol_revision="score-r1",
        scoring_protocol_sha256=SHA_B,
        scoring_projection_revision="projection-r1",
        executor_ref="executor-r1",
        sample_revision=sample,
    )


def test_cache_is_reusable_only_under_exact_category_cohort_binding():
    first = build_cache_binding(
        binding=_binding(),
        model_identity={
            "profile_kind": "API",
            "requested_model": "deepseek-v4-flash",
            "returned_model": "deepseek-v4-flash",
        },
        executor_sha256=SHA_C,
    )
    same = build_cache_binding(
        binding=_binding(),
        model_identity={
            "profile_kind": "API",
            "requested_model": "deepseek-v4-flash",
            "returned_model": "deepseek-v4-flash",
        },
        executor_sha256=SHA_C,
    )
    drifted = build_cache_binding(
        binding=_binding(sample="SLOT_2"),
        model_identity={
            "profile_kind": "API",
            "requested_model": "deepseek-v4-flash",
            "returned_model": "deepseek-v4-flash",
        },
        executor_sha256=SHA_C,
    )
    edited_exam_code = build_cache_binding(
        binding=_binding(),
        model_identity={
            "profile_kind": "API",
            "requested_model": "deepseek-v4-flash",
            "returned_model": "deepseek-v4-flash",
        },
        executor_sha256=SHA_D,
    )
    assert cache_reuse_status(first, same) == "REUSABLE"
    assert cache_reuse_status(first, edited_exam_code) == "REUSABLE"
    assert edited_exam_code["executor_sha256"] == SHA_D
    assert cache_reuse_status(first, drifted) == "STALE_BINDING_MISMATCH"
    assert category_comparison_allowed(
        first["category_binding"], same["category_binding"]
    )
    assert not category_comparison_allowed(
        first["category_binding"], drifted["category_binding"]
    )
    assert first["cross_category_comparison_forbidden"] is True


def _analysis_detail(
    case_id: str,
    *,
    family: str,
    difficulty: str,
    score: float,
    critical_reasons: list[str] | None = None,
) -> dict:
    return {
        "case_id": case_id,
        "family": family,
        "difficulty": difficulty,
        "score": score,
        "critical_reasons": list(critical_reasons or []),
        "axes": {
            "literal_fidelity": 20.0,
            "evidence_scope_coverage": 25.0,
            "cross_artifact_consistency": 20.0,
            "scientific_plausibility": 25.0,
            "lineage_freshness": 10.0,
        },
    }


def test_analysis_repair_targets_only_material_cases_not_every_nonperfect_case():
    scores = {
        "A": {
            "details": [
                _analysis_detail(
                    "A-calibration",
                    family="F1",
                    difficulty="easy",
                    score=92.0,
                ),
                _analysis_detail(
                    "A-material",
                    family="F2",
                    difficulty="medium",
                    score=79.5,
                ),
                _analysis_detail(
                    "A-critical",
                    family="F3",
                    difficulty="hard",
                    score=88.0,
                    critical_reasons=["CAUSAL_OVERCLAIM"],
                ),
            ]
        },
        "B": {
            "details": [
                _analysis_detail(
                    "B-perfect",
                    family="F1",
                    difficulty="easy",
                    score=100.0,
                )
            ]
        },
    }
    result = analysis_material_repair_targets(scores)
    assert result["targets_by_form"] == {
        "A": ["A-material", "A-critical"],
        "B": [],
    }
    assert result["calibration_only_by_form"] == {
        "A": ["A-calibration"],
        "B": [],
    }
    assert result["threshold_exclusive"] == 80.0


def test_analysis_repair_acceptance_is_case_local_and_never_accepts_regression():
    before = {
        "A": {
            "details": [
                _analysis_detail(
                    "A-improves", family="F1", difficulty="hard", score=62.0
                ),
                _analysis_detail(
                    "A-regresses", family="F2", difficulty="hard", score=78.0
                ),
                _analysis_detail(
                    "A-new-critical", family="F3", difficulty="hard", score=70.0
                ),
            ]
        },
        "B": {"details": []},
    }
    after = {
        "A": {
            "details": [
                _analysis_detail(
                    "A-improves", family="F1", difficulty="hard", score=85.0
                ),
                _analysis_detail(
                    "A-regresses", family="F2", difficulty="hard", score=77.0
                ),
                _analysis_detail(
                    "A-new-critical",
                    family="F3",
                    difficulty="hard",
                    score=90.0,
                    critical_reasons=["NEW_CRITICAL"],
                ),
            ]
        },
        "B": {"details": []},
    }
    result = analysis_repair_acceptance(
        before_scores=before,
        candidate_scores=after,
        targets_by_form={
            "A": ["A-improves", "A-regresses", "A-new-critical"],
            "B": [],
        },
    )
    assert result["accepted_by_form"] == {"A": ["A-improves"], "B": []}
    rejected = {row["case_id"]: row["reason_code"] for row in result["rejected"]}
    assert rejected == {
        "A-regresses": "CASE_SCORE_NOT_IMPROVED",
        "A-new-critical": "NEW_CRITICAL_REASON_INTRODUCED",
    }


def _analysis_panel(slot: int, difficulty: str) -> dict:
    families = ["F1", "F2", "F3", "F4", "F5", "F6"]
    selection: dict[str, list[dict]] = {}
    raw_scores: dict[str, dict] = {}
    for form_id in ("A", "B"):
        rows = [
            _analysis_detail(
                f"{form_id}-S{slot}-{index}",
                family=family,
                difficulty=difficulty,
                score=70.0 + slot + index,
            )
            for index, family in enumerate(families, start=1)
        ]
        selection[form_id] = [
            {
                "case_id": row["case_id"],
                "family": row["family"],
                "difficulty": row["difficulty"],
            }
            for row in rows
        ]
        raw_scores[form_id] = {"details": rows}
    return {
        "panel_id": f"panel-{slot}",
        "sample_slot": slot,
        "cohort_basis": {
            "category_id": "ANALYSIS_PRIMARY",
            "reference_pack_sha256": SHA_A,
            "sample_revision": "ROTATION_V2",
            "subject_projection_revision": "PROJECTION_V2",
            "executor_ref": "EXECUTOR_V4",
        },
        "model_identity": {
            "profile_kind": "CLI",
            "requested_model": "gpt-5.6-sol",
            "returned_model": "gpt-5.6-sol",
            "thinking_mode": "xhigh",
        },
        "selection": selection,
        "raw_scores": raw_scores,
    }


def test_analysis_score_is_cacheable_only_after_three_nonoverlapping_panels():
    panels = [
        _analysis_panel(1, "hard"),
        _analysis_panel(2, "medium"),
        _analysis_panel(3, "easy"),
    ]
    cohort = build_analysis_panel_cohort(panels)
    assert cohort["cache_eligible"] is True
    assert cohort["panel_count"] == 3
    assert cohort["sample_slots"] == [1, 2, 3]
    assert cohort["aggregate_case_count_per_form"] == 18
    assert len(cohort["aggregate_raw_scores"]["A"]["details"]) == 18
    assert cohort["difficulty_coverage_by_form_family"]["A"]["F1"] == [
        "easy",
        "hard",
        "medium",
    ]
    with pytest.raises(ValueError, match="ANALYSIS_PANEL_COHORT_INCOMPLETE"):
        build_analysis_panel_cohort(panels[:2])
    duplicate = [panels[0], panels[0], panels[2]]
    with pytest.raises(ValueError, match="ANALYSIS_PANEL_SLOT_DUPLICATED"):
        build_analysis_panel_cohort(duplicate)


def test_analysis_low_score_alone_or_one_family_cannot_fail_model():
    same_family = [
        {
            "form_id": "A",
            "case_id": "case-1",
            "panel_id": "panel-1",
            "family": "F1",
        },
        {
            "form_id": "B",
            "case_id": "case-2",
            "panel_id": "panel-2",
            "family": "F1",
        },
    ]
    decision = analysis_cohort_model_fail_decision(
        score=55.0,
        material_residuals=same_family,
        quality_hard_gate_pass=False,
    )
    assert decision["status"] == "NOT_ASSESSED"
    assert decision["model_fail_verdict"] == "NOT_ASSESSED"
    assert decision["model_fail_established"] is False
    assert decision["low_score_alone_cannot_establish_model_fail"] is True
    assert decision["distinct_material_panel_count"] == 2
    assert decision["distinct_material_family_count"] == 1


def test_analysis_fail_requires_two_cases_across_two_panels_and_families():
    corroborated = [
        {
            "form_id": "A",
            "case_id": "case-1",
            "panel_id": "panel-1",
            "family": "F1",
        },
        {
            "form_id": "B",
            "case_id": "case-2",
            "panel_id": "panel-2",
            "family": "F2",
        },
    ]
    decision = analysis_cohort_model_fail_decision(
        score=55.0,
        material_residuals=corroborated,
        quality_hard_gate_pass=False,
    )
    assert decision["status"] == "FAIL"
    assert decision["model_fail_verdict"] == "FAIL"
    assert decision["model_fail_established"] is True
    assert decision["corroborated_material_failure"] is True
