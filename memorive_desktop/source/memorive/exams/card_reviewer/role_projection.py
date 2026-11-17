"""Scoring of the card reviewer role: score_card_reviewer_role_projection turns a scored form into the
role's operational verdict.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping
from memorive.exams.outcomes import clean_control_residual
from memorive.exams.card_reviewer.policy import (
    CARD_REVIEWER_OPERATIONAL_PASSING_SCORE,
    CARD_REVIEWER_OPERATIONAL_POLICY_REVISION,
    CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION,
    CARD_REVIEWER_SCORE_SEMANTICS,
    CORE_CARD_REVIEWER_LIFECYCLE_REVISION,
)


def score_card_reviewer_role_projection(
    *,
    exam: Any,
    forms: Mapping[str, Mapping[str, Any]],
    golds: Mapping[str, Mapping[str, Any]],
    original_scores: Mapping[str, Mapping[str, Any]],
    merged_responses: Mapping[str, Mapping[str, Any]],
    schema: Mapping[str, Any],
    scoring_protocol: Mapping[str, Any],
    repair_contract: Mapping[str, Any],
    score_form_fn: Callable[..., Mapping[str, Any]] | None = None,
    call_legacy_whole_exam_scorer: bool = True,
) -> dict[str, Any]:
    """Expose Reviewer ability, repair outcome, and qualification separately.

    Every numeric coefficient comes from the frozen Card Reviewer scorer.  The
    Card Distiller residual-error weights are deliberately excluded because a
    score is comparable only inside its own role, pack, scorer, and projection
    cohort.  The frozen r1.1 whole-exam scorer is still called first so its
    exact-set and hash checks remain fail-closed; its zero on residual errors is
    retained only as a legacy qualification trace, never as the role score.
    """

    error_type = getattr(exam, "ReviewerExamError", ValueError)
    if call_legacy_whole_exam_scorer:
        historical = exam.score_exam_after_directed_repair(
            forms=forms,
            golds=golds,
            original_scores=original_scores,
            merged_responses=merged_responses,
            schema=schema,
            scoring_protocol=scoring_protocol,
            repair_contract=repair_contract,
        )
        historical_status = historical.get("status")
        if historical_status not in {
            "DIRECTED_REPAIR_SUCCEEDED",
            "DIRECTED_REPAIR_FAILED",
        }:
            raise error_type("CARD_REVIEWER_ROLE_PROJECTION_STATUS_INVALID")
    else:
        historical = {}
        historical_status = "NOT_APPLICABLE_SUCCESSOR_BALANCED_SAMPLE"
    scorer = score_form_fn or exam.score_form

    exact_sets = repair_contract.get("wrong_item_exact_set")
    if not isinstance(exact_sets, Mapping):
        raise error_type("EXAM_REPAIR_EXACT_SETS_REQUIRED")
    reviewer_contract = getattr(exam, "EXAM_REPAIR_PENALTY_CONTRACT", None)
    if not isinstance(reviewer_contract, Mapping):
        raise error_type("EXAM_REPAIR_PENALTY_CONTRACT_REQUIRED")
    ceiling = reviewer_contract.get("successful_repair_ceiling")
    if (
        isinstance(ceiling, bool)
        or not isinstance(ceiling, (int, float))
        or float(ceiling) <= 0.0
    ):
        raise error_type("EXAM_REPAIR_SUCCESSFUL_CEILING_INVALID")

    post_scores: dict[str, dict[str, Any]] = {}
    remaining: dict[str, list[str]] = {}
    new_wrong_items: dict[str, list[str]] = {}
    form_results: dict[str, dict[str, Any]] = {}
    role_ability_form_scores: dict[str, float] = {}
    repair_outcome_form_scores: dict[str, float] = {}
    initial_penalties: dict[str, dict[str, Any]] = {}
    residual_diagnostics: dict[str, dict[str, Any]] = {}
    operational_residual_diagnostics: dict[str, list[dict[str, Any]]] = {}
    form_case_counts: dict[str, int] = {}
    case_score_deficit_by_form: dict[str, dict[str, Any]] = {}
    repair_metadata_collision_suspects_by_form: dict[str, list[str]] = {}
    residual_disposition_penalties: dict[str, dict[str, Any]] = {}
    governed_disposition_case_ids_by_form: dict[str, list[str]] = {}

    for form_id in ("A", "B"):
        form = forms.get(form_id)
        gold = golds.get(form_id)
        merged = merged_responses.get(form_id)
        original_score = original_scores.get(form_id)
        if not all(
            isinstance(item, Mapping)
            for item in (form, gold, merged, original_score)
        ):
            raise error_type(
                f"CARD_REVIEWER_ROLE_PROJECTION_INPUT_REQUIRED:{form_id}"
            )
        post = scorer(
            form=form,
            final_response=merged,
            gold=gold,
            schema=schema,
            scoring_protocol=scoring_protocol,
            repair_metrics={
                "form_case_count": len(exam.form_cases(form)[1]),
                "repair_turns": 0,
                "directed_repair_case_count": 0,
                "all_repairs_succeeded": True,
            },
        )
        post_scores[form_id] = post
        original_details = {
            item["case_id"]: item
            for item in exam.reviewer_exam_wrong_details(original_score)
        }
        target_ids = list(exact_sets.get(form_id, []))
        if any(case_id not in original_details for case_id in target_ids):
            raise error_type(
                f"CONTINUOUS_REPAIR_TARGET_NOT_ORIGINALLY_WRONG:{form_id}"
            )
        issue_categories: set[str] = set()
        severity_categories: set[str] = set()
        original_failed_fields_by_case: dict[str, set[str]] = {}
        repair_issue_priority_by_case: dict[str, str] = {}
        for case_id in target_ids:
            issues, failed_fields, severity = (
                exam.reviewer_exam_issue_metadata(
                    original_details[case_id]
                )
            )
            issue_categories.update(issues)
            severity_categories.add(severity)
            original_failed_fields_by_case[case_id] = set(failed_fields)
            repair_issue_priority_by_case[case_id] = severity
        form_case_count = len(exam.form_cases(form)[1])
        form_case_counts[form_id] = form_case_count
        initial_penalty = exam.reviewer_exam_form_penalty(
            form_case_count=form_case_count,
            wrong_case_count=len(target_ids),
            issue_categories=issue_categories,
            severity_categories=severity_categories,
        )
        initial_penalties[form_id] = initial_penalty

        post_wrong_details = exam.reviewer_exam_wrong_details(post)
        post_wrong_ids = {item["case_id"] for item in post_wrong_details}
        original_wrong_ids = set(target_ids)
        remaining[form_id] = sorted(post_wrong_ids & original_wrong_ids)
        new_wrong_items[form_id] = sorted(
            post_wrong_ids - original_wrong_ids
        )
        gold_by_id = {
            str(item["case_id"]): item for item in gold.get("cases", [])
        }
        row_by_id = {
            str(item["case_id"]): item for item in merged.get("reviews", [])
        }
        severity_counts = {"major": 0, "critical": 0}
        issue_counts: dict[str, int] = {}
        residual_case_ids: list[str] = []
        risk_rows: list[dict[str, Any]] = []
        collision_suspect_case_ids: list[str] = []
        residual_issue_categories: set[str] = set()
        residual_severity_categories: set[str] = set()
        governed_disposition_case_ids: list[str] = []
        for detail in post_wrong_details:
            issues, failed_fields, severity = (
                exam.reviewer_exam_issue_metadata(detail)
            )
            case_id = str(detail["case_id"])
            gold_case = gold_by_id.get(case_id)
            response_row = row_by_id.get(case_id)
            if not isinstance(gold_case, Mapping) or not isinstance(
                response_row, Mapping
            ):
                raise error_type(
                    f"CARD_REVIEWER_OPERATIONAL_RISK_INPUT_MISSING:{form_id}:{case_id}"
                )
            severity_counts[severity] += 1
            residual_severity_categories.add(severity)
            residual_case_ids.append(case_id)
            for issue in issues:
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
                residual_issue_categories.add(issue)
            safety_reasons: list[str] = []
            calibration_reasons: list[str] = []
            expected_verdict = str(gold_case.get("expected_verdict"))
            observed_verdict = str(response_row.get("verdict"))
            is_clean = expected_verdict == "PASS"
            is_critical = gold_case.get("critical") is True
            target_match = response_row.get("target_pointer") in list(
                gold_case.get("accepted_target_pointers") or []
            )
            required_evidence = set(
                str(item) for item in gold_case.get("required_evidence_refs") or []
            )
            observed_evidence = set(
                str(item) for item in response_row.get("evidence_refs") or []
            )
            missing_required_evidence = sorted(
                required_evidence - observed_evidence
            )
            severity_rank = {
                "none": 0,
                "minor": 1,
                "major": 2,
                "critical": 3,
            }
            expected_severity = str(gold_case.get("severity"))
            observed_severity = str(response_row.get("severity"))
            observed_action = str(response_row.get("action"))
            accepted_actions = {
                str(item) for item in gold_case.get("accepted_actions") or []
            }
            repair_issue_priority = repair_issue_priority_by_case.get(
                case_id
            )
            collision_suspect = (
                case_id in original_wrong_ids
                and "severity"
                in original_failed_fields_by_case.get(case_id, set())
                and "severity" in failed_fields
                and repair_issue_priority == "major"
                and expected_severity == "minor"
                and severity_rank.get(observed_severity, -1)
                > severity_rank["minor"]
            )
            if collision_suspect:
                collision_suspect_case_ids.append(case_id)
            if is_clean and observed_verdict != "PASS":
                clean_risk = clean_control_residual(
                    expected_verdict=expected_verdict,
                    observed_verdict=observed_verdict,
                    observed_action=observed_action,
                )
                if clean_risk["safety_blocking"]:
                    safety_reasons.append(str(clean_risk["reason_code"]))
                else:
                    calibration_reasons.append(str(clean_risk["reason_code"]))
            elif not is_clean and observed_verdict != "FAIL":
                safety_reasons.append("DEFECT_NOT_FLAGGED")
            if (
                not is_clean
                and severity_rank.get(observed_severity, -1)
                < severity_rank.get(expected_severity, -1)
            ):
                safety_reasons.append("SEVERITY_UNDERCALL")
            if is_critical and not target_match:
                safety_reasons.append("CRITICAL_TARGET_NOT_LOCALIZED")
            if is_critical and missing_required_evidence:
                safety_reasons.append("CRITICAL_REQUIRED_EVIDENCE_MISSING")
            if not safety_reasons:
                if (
                    expected_severity == "minor"
                    and severity_rank.get(observed_severity, -1)
                    > severity_rank["minor"]
                ):
                    calibration_reasons.append("CONSERVATIVE_SEVERITY_OVERCALL")
                if collision_suspect:
                    calibration_reasons.append(
                        "REPAIR_TARGET_SEVERITY_METADATA_COLLISION_SUSPECT"
                    )
                if not target_match:
                    calibration_reasons.append("TARGET_POINTER_PRECISION")
                for field in failed_fields:
                    reason = f"FIELD_CALIBRATION:{field}"
                    if reason not in calibration_reasons:
                        calibration_reasons.append(reason)
                if not calibration_reasons:
                    calibration_reasons.append("NONBLOCKING_EXACT_MATCH_RESIDUAL")
            governed_disposition = (
                bool(safety_reasons)
                and not is_clean
                and observed_verdict == "FAIL"
                and target_match
                and not missing_required_evidence
                and observed_action in {"downgrade", "tombstone"}
                and observed_action in accepted_actions
            )
            if governed_disposition:
                governed_disposition_case_ids.append(case_id)
            risk_rows.append(
                {
                    "case_id": case_id,
                    "risk_class": (
                        "SAFETY_BLOCKING"
                        if safety_reasons
                        else "CALIBRATION_OR_PRECISION"
                    ),
                    "safety_reasons": safety_reasons,
                    "calibration_reasons": calibration_reasons,
                    "gold_critical": is_critical,
                    "gold_severity": expected_severity,
                    "observed_severity": observed_severity,
                    "repair_issue_priority": repair_issue_priority,
                    "repair_metadata_severity_collision_suspect": (
                        collision_suspect
                    ),
                    "failed_output_fields": sorted(failed_fields),
                    "issue_codes": sorted(issues),
                    "observed_action": observed_action,
                    "accepted_terminal_actions": sorted(
                        accepted_actions & {"downgrade", "tombstone"}
                    ),
                    "governed_disposition_eligible": governed_disposition,
                    "governed_disposition": (
                        observed_action.upper() if governed_disposition else None
                    ),
                    "core_terminal_semantics": (
                        "LOGICAL_DOWNGRADE_OR_TOMBSTONE_NOT_PHYSICAL_DELETE"
                    ),
                }
            )
        residual_disposition_penalty = exam.reviewer_exam_form_penalty(
            form_case_count=form_case_count,
            wrong_case_count=len(residual_case_ids),
            issue_categories=residual_issue_categories,
            severity_categories=residual_severity_categories,
        )
        residual_disposition_penalties[form_id] = residual_disposition_penalty
        governed_disposition_case_ids_by_form[form_id] = sorted(
            governed_disposition_case_ids
        )
        residual_diagnostic = {
            "wrong_item_count": len(residual_case_ids),
            "failed_exam_item_count": len(residual_case_ids),
            "case_ids": sorted(residual_case_ids),
            "severity_counts": severity_counts,
            "issue_counts": dict(sorted(issue_counts.items())),
            "numeric_penalty_applied": bool(residual_case_ids),
            "nonlinear_disposition_burden": residual_disposition_penalty,
            "reason": (
                "REUSE_Quality_CARD_REVIEWER_NONLINEAR_FORM_PENALTY_ON_"
                "RESIDUAL_GOVERNED_DISPOSITION_EXACT_SET"
            ),
        }
        residual_diagnostics[form_id] = residual_diagnostic
        operational_residual_diagnostics[form_id] = risk_rows
        repair_metadata_collision_suspects_by_form[form_id] = sorted(
            collision_suspect_case_ids
        )

        post_detail_by_id = {
            str(item["case_id"]): item for item in post.get("details", [])
        }
        if any(case_id not in post_detail_by_id for case_id in target_ids):
            raise error_type(
                f"CARD_REVIEWER_POST_DETAIL_MISSING:{form_id}"
            )
        initial_deficit_points = sum(
            100.0 - float(original_details[case_id]["score"])
            for case_id in target_ids
        )
        post_target_deficit_points = sum(
            100.0 - float(post_detail_by_id[case_id]["score"])
            for case_id in target_ids
        )
        post_new_deficit_points = sum(
            100.0 - float(post_detail_by_id[case_id]["score"])
            for case_id in new_wrong_items[form_id]
        )
        post_deficit_points = (
            post_target_deficit_points + post_new_deficit_points
        )
        recovered_deficit_points = max(
            0.0, initial_deficit_points - post_deficit_points
        )
        deficit_recovery_rate = (
            max(
                0.0,
                min(1.0, recovered_deficit_points / initial_deficit_points),
            )
            if initial_deficit_points
            else 1.0
        )
        case_score_deficit_by_form[form_id] = {
            "initial_points": round(initial_deficit_points, 6),
            "post_points": round(post_deficit_points, 6),
            "recovered_points": round(recovered_deficit_points, 6),
            "recovery_rate": round(deficit_recovery_rate, 9),
            "point_semantics": (
                "FROZEN_CARD_REVIEWER_CASE_COMPONENT_SCORE_DEFICIT"
            ),
        }

        initial_base = float(original_score["score"])
        post_base = float(post["score"])
        pre_penalty = (
            min(post_base, float(ceiling)) if target_ids else post_base
        )
        repair_outcome = max(
            0.0, pre_penalty - float(initial_penalty["total"])
        )
        role_ability_form_scores[form_id] = round(initial_base, 6)
        repair_outcome_form_scores[form_id] = round(repair_outcome, 6)
        residual_count = len(residual_case_ids)
        repair_completion_rate = (
            max(0.0, min(1.0, (len(target_ids) - residual_count) / len(target_ids)))
            if target_ids
            else 1.0
        )
        collision_adjusted_diagnostic_rate = (
            max(
                0.0,
                min(
                    1.0,
                    (
                        len(target_ids)
                        - residual_count
                        + len(collision_suspect_case_ids)
                    )
                    / len(target_ids),
                ),
            )
            if target_ids
            else 1.0
        )
        form_results[form_id] = {
            "initial_nonlinear_score": round(initial_base, 6),
            "post_repair_nonlinear_score": round(post_base, 6),
            "successful_repair_ceiling_applied": (
                bool(target_ids) and post_base > float(ceiling)
            ),
            "initial_repair_burden_penalty": initial_penalty,
            "residual_error_diagnostic": residual_diagnostic,
            "role_ability_form_score": round(initial_base, 6),
            "reviewer_repair_outcome_form_score": round(repair_outcome, 6),
            "repair_completion_rate": round(repair_completion_rate, 9),
            "exact_case_repair_rate": round(repair_completion_rate, 9),
            "case_score_deficit_recovery": case_score_deficit_by_form[
                form_id
            ],
            "repair_metadata_collision_suspect_count": len(
                collision_suspect_case_ids
            ),
            "repair_metadata_collision_suspect_case_ids": sorted(
                collision_suspect_case_ids
            ),
            "collision_adjusted_repair_diagnostic_rate": round(
                collision_adjusted_diagnostic_rate, 9
            ),
            "residual_governed_disposition_penalty": (
                residual_disposition_penalty
            ),
            "governed_disposition_case_ids": sorted(
                governed_disposition_case_ids
            ),
            "hard_gate_verdict": str(post["hard_gate_verdict"]),
        }

    remaining_count = sum(len(items) for items in remaining.values())
    new_wrong_count = sum(len(items) for items in new_wrong_items.values())
    failed_exam_item_count = sum(
        int(item["failed_exam_item_count"])
        for item in residual_diagnostics.values()
    )
    residual_wrong_count = remaining_count + new_wrong_count
    if failed_exam_item_count != residual_wrong_count:
        raise error_type("CARD_REVIEWER_ROLE_PROJECTION_FAILED_ITEM_COUNT_MISMATCH")
    role_ability_score = round(
        sum(role_ability_form_scores.values()) / len(role_ability_form_scores),
        6,
    )
    reviewer_repair_outcome_score = round(
        sum(repair_outcome_form_scores.values())
        / len(repair_outcome_form_scores),
        6,
    )
    post_repair_score = round(
        sum(float(item["score"]) for item in post_scores.values())
        / len(post_scores),
        6,
    )
    initial_nonlinear_score = round(
        sum(float(item["score"]) for item in original_scores.values())
        / len(original_scores),
        6,
    )
    initial_penalty_mean = round(
        sum(float(item["total"]) for item in initial_penalties.values())
        / len(initial_penalties),
        6,
    )
    initial_wrong_count = int(repair_contract["wrong_item_count"])
    if initial_wrong_count != sum(
        len(list(exact_sets.get(form_id, []))) for form_id in ("A", "B")
    ):
        raise error_type("CARD_REVIEWER_ROLE_PROJECTION_WRONG_COUNT_MISMATCH")
    repair_completion_rate = (
        max(
            0.0,
            min(
                1.0,
                (initial_wrong_count - residual_wrong_count)
                / initial_wrong_count,
            ),
        )
        if initial_wrong_count
        else 1.0
    )
    initial_case_score_deficit_points = sum(
        float(item["initial_points"])
        for item in case_score_deficit_by_form.values()
    )
    post_case_score_deficit_points = sum(
        float(item["post_points"])
        for item in case_score_deficit_by_form.values()
    )
    recovered_case_score_deficit_points = max(
        0.0,
        initial_case_score_deficit_points - post_case_score_deficit_points,
    )
    case_score_deficit_recovery_rate = (
        max(
            0.0,
            min(
                1.0,
                recovered_case_score_deficit_points
                / initial_case_score_deficit_points,
            ),
        )
        if initial_case_score_deficit_points
        else 1.0
    )
    repair_metadata_collision_suspect_case_ids = sorted(
        case_id
        for case_ids in repair_metadata_collision_suspects_by_form.values()
        for case_id in case_ids
    )
    collision_adjusted_repair_diagnostic_rate = (
        max(
            0.0,
            min(
                1.0,
                (
                    initial_wrong_count
                    - residual_wrong_count
                    + len(repair_metadata_collision_suspect_case_ids)
                )
                / initial_wrong_count,
            ),
        )
        if initial_wrong_count
        else 1.0
    )
    hard_gate = (
        "PASS"
        if all(
            item["hard_gate_verdict"] == "PASS"
            for item in post_scores.values()
        )
        else "FAIL"
    )
    qualification_verdict = (
        "DISQUALIFIED"
        if residual_wrong_count or hard_gate != "PASS"
        else "PASS"
    )
    safety_blocking_case_ids = sorted(
        item["case_id"]
        for rows in operational_residual_diagnostics.values()
        for item in rows
        if item["risk_class"] == "SAFETY_BLOCKING"
    )
    calibration_case_ids = sorted(
        item["case_id"]
        for rows in operational_residual_diagnostics.values()
        for item in rows
        if item["risk_class"] == "CALIBRATION_OR_PRECISION"
    )
    governed_disposition_case_ids = sorted(
        case_id
        for case_ids in governed_disposition_case_ids_by_form.values()
        for case_id in case_ids
    )
    undisposed_safety_case_ids = sorted(
        set(safety_blocking_case_ids) - set(governed_disposition_case_ids)
    )
    if residual_wrong_count == 0 and hard_gate == "PASS":
        core_closure_verdict = "DELTA_PASS"
    elif (
        hard_gate == "PASS"
        and new_wrong_count == 0
        and not safety_blocking_case_ids
    ):
        core_closure_verdict = "PASS_WITH_CALIBRATION"
    elif (
        hard_gate == "PASS"
        and new_wrong_count == 0
        and safety_blocking_case_ids
        and not undisposed_safety_case_ids
    ):
        core_closure_verdict = "PASS_WITH_GOVERNED_DISPOSITION"
    else:
        core_closure_verdict = "HUMAN_REQUIRED"
    operational_safety_gate = (
        "PASS"
        if core_closure_verdict != "HUMAN_REQUIRED"
        else "FAIL"
    )
    if operational_safety_gate != "PASS":
        operational_eligibility = "NOT_RECOMMENDED"
    elif role_ability_score < 60.0:
        operational_eligibility = "REJECT"
    elif role_ability_score < CARD_REVIEWER_OPERATIONAL_PASSING_SCORE:
        operational_eligibility = "NOT_RECOMMENDED"
    elif core_closure_verdict == "PASS_WITH_GOVERNED_DISPOSITION":
        operational_eligibility = "SUITABLE_WITH_GOVERNED_DISPOSITION"
    elif residual_wrong_count:
        operational_eligibility = "SUITABLE_WITH_CALIBRATION"
    else:
        operational_eligibility = "SUITABLE"
    status = (
        "DIRECTED_REPAIR_SCORED_WITH_RESIDUAL_ERRORS"
        if residual_wrong_count
        else (
            "DIRECTED_REPAIR_HARD_GATE_FAILED"
            if hard_gate != "PASS"
            else "DIRECTED_REPAIR_SUCCEEDED"
        )
    )
    residual_disposition_penalty_mean = round(
        sum(float(item["total"]) for item in residual_disposition_penalties.values())
        / len(residual_disposition_penalties),
        6,
    )
    core_closure_score = round(
        max(
            0.0,
            reviewer_repair_outcome_score
            - residual_disposition_penalty_mean,
        ),
        6,
    )
    return {
        "schema_version": "DesktopCardReviewerRoleProjection-v5",
        "status": status,
        "qualification_verdict": qualification_verdict,
        "quality_exact_repair_qualification_verdict": qualification_verdict,
        "operational_eligibility_verdict": operational_eligibility,
        "operational_safety_gate": operational_safety_gate,
        "operational_policy_revision": (
            CARD_REVIEWER_OPERATIONAL_POLICY_REVISION
        ),
        "core_lifecycle_revision": CORE_CARD_REVIEWER_LIFECYCLE_REVISION,
        "core_closure_verdict": core_closure_verdict,
        "semantic_repair_rounds": 1,
        "second_semantic_repair_allowed": False,
        "controlled_transport_retry_is_semantic_repair": False,
        "governed_disposition_case_count": len(
            governed_disposition_case_ids
        ),
        "governed_disposition_case_ids": governed_disposition_case_ids,
        "undisposed_safety_case_ids": undisposed_safety_case_ids,
        "operational_passing_score": CARD_REVIEWER_OPERATIONAL_PASSING_SCORE,
        "safety_blocking_residual_count": len(safety_blocking_case_ids),
        "safety_blocking_case_ids": safety_blocking_case_ids,
        "calibration_residual_count": len(calibration_case_ids),
        "calibration_case_ids": calibration_case_ids,
        "operational_residual_diagnostics": operational_residual_diagnostics,
        "legacy_quality_repair_status": historical_status,
        "legacy_quality_final_score": historical.get("final_score"),
        "initial_wrong_item_count": initial_wrong_count,
        "remaining_wrong_items": remaining,
        "remaining_initial_wrong_item_count": remaining_count,
        "new_wrong_items": new_wrong_items,
        "new_wrong_item_count": new_wrong_count,
        "remaining_wrong_item_count": residual_wrong_count,
        "failed_exam_item_count": failed_exam_item_count,
        "failed_exam_item_count_by_form": {
            form_id: int(
                residual_diagnostics[form_id]["failed_exam_item_count"]
            )
            for form_id in ("A", "B")
        },
        "total_exam_item_count": sum(form_case_counts.values()),
        "role_ability_score": role_ability_score,
        "score_semantics": CARD_REVIEWER_SCORE_SEMANTICS,
        "initial_nonlinear_score": initial_nonlinear_score,
        "post_repair_nonlinear_score": post_repair_score,
        "reviewer_repair_outcome_score": reviewer_repair_outcome_score,
        "core_closure_score": core_closure_score,
        "repair_completion_rate": round(repair_completion_rate, 9),
        "repair_completion_rate_semantics": (
            "ORIGINAL_WRONG_CASE_REACHES_EXACT_SCORE_100_WITH_NEW_WRONGS_COUNTED"
        ),
        "exact_case_repair_rate": round(repair_completion_rate, 9),
        "case_score_deficit_recovery_rate": round(
            case_score_deficit_recovery_rate, 9
        ),
        "case_score_deficit_recovery": {
            "initial_points": round(
                initial_case_score_deficit_points, 6
            ),
            "post_points": round(post_case_score_deficit_points, 6),
            "recovered_points": round(
                recovered_case_score_deficit_points, 6
            ),
            "recovery_rate": round(
                case_score_deficit_recovery_rate, 9
            ),
            "by_form": case_score_deficit_by_form,
            "point_semantics": (
                "FROZEN_CARD_REVIEWER_CASE_COMPONENT_SCORE_DEFICIT"
            ),
        },
        "repair_metadata_collision_suspect_count": len(
            repair_metadata_collision_suspect_case_ids
        ),
        "repair_metadata_collision_suspect_case_ids": (
            repair_metadata_collision_suspect_case_ids
        ),
        "repair_metadata_collision_suspects_by_form": (
            repair_metadata_collision_suspects_by_form
        ),
        "collision_adjusted_repair_diagnostic_rate": round(
            collision_adjusted_repair_diagnostic_rate, 9
        ),
        "collision_adjusted_repair_diagnostic_only": True,
        "collision_adjusted_repair_qualification_effect": "NONE",
        "reviewer_repair_burden": {
            "initial_burden_by_form": initial_penalties,
            "initial_burden_total_mean": initial_penalty_mean,
            "residual_errors_by_form": residual_diagnostics,
            "governed_disposition_burden_by_form": (
                residual_disposition_penalties
            ),
            "governed_disposition_burden_total_mean": (
                residual_disposition_penalty_mean
            ),
            "burden_order": [
                "ONE_SEMANTIC_REPAIR",
                "DELTA_RECHECK",
                "GOVERNED_DISPOSITION_IF_NEEDED",
            ],
            "governed_disposition_is_second_repair": False,
        },
        "forms": form_results,
        "role_ability_form_scores": role_ability_form_scores,
        "repair_outcome_form_scores": repair_outcome_form_scores,
        "role_ability_min": round(min(role_ability_form_scores.values()), 6),
        "role_ability_gap": round(
            abs(
                role_ability_form_scores["A"]
                - role_ability_form_scores["B"]
            ),
            6,
        ),
        "quality_hard_gate": hard_gate,
        "role_ability_score_formula": (
            "MEAN(FROZEN_FIRST_PASS_FORM_NONLINEAR_SCORE)"
        ),
        "repair_outcome_score_formula": (
            "MEAN(MAX(0,MIN(POST_REPAIR_NONLINEAR_SCORE,"
            "SUCCESSFUL_REPAIR_CEILING)-INITIAL_REPAIR_BURDEN))"
        ),
        "scoring_axes": {
            "ability": "Quality_CARD_REVIEWER_NONLINEAR_PIECEWISE",
            "repair_outcome": "Quality_CARD_REVIEWER_R1_1",
            "exact_case_repair": (
                "Quality_CARD_REVIEWER_CASE_SCORE_100_EXACT_SET"
            ),
            "case_score_deficit_recovery": (
                "Quality_CARD_REVIEWER_FROZEN_CASE_COMPONENT_POINTS"
            ),
            "repair_prompt_collision_diagnostic": (
                CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION
            ),
            "qualification": (
                "Quality_CARD_REVIEWER_R1_1_DISQUALIFY_ON_RESIDUAL"
            ),
            "operational_eligibility": (
                CARD_REVIEWER_OPERATIONAL_POLICY_REVISION
            ),
            "core_closure": CORE_CARD_REVIEWER_LIFECYCLE_REVISION,
            "comparison": "EXACT_SAME_CATEGORY_ROLE_PACK_SCORER_PROJECTION_ONLY",
        },
        "provider_blind_formula": True,
        "numeric_score_is_not_qualification_verdict": True,
        "cross_category_coefficients_reused": False,
        "global_ranking_forbidden": True,
    }
