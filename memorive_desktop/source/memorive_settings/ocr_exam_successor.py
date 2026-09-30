from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import re
import statistics
from typing import Any


SCORING_PROJECTION_REVISION = "Desktop_OCR_WORKFLOW_POSITION_RECOVERABILITY_V3"
SAMPLE_REVISION = "OCR_BALANCED_9_PLUS_2_REPEAT_V2"

_OUTER_MARKDOWN_FENCE = re.compile(
    r"\A\s*```(?:[A-Za-z0-9_.+-]+)?[ \t]*\n(?P<body>.*?)\n```[ \t]*\s*\Z",
    flags=re.DOTALL,
)
_MARKDOWN_HORIZONTAL_RULE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_MARKDOWN_TABLE_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")

MAIN_CASE_IDS = (
    "OCR-T-A-01-02",
    "OCR-T-B-02-04",
    "OCR-T-A-03-05",
    "OCR-T-B-04-05",
    "OCR-T-A-05-05",
    "OCR-T-B-06-03",
    "OCR-T-A-07-01",
    "OCR-T-B-08-02",
    "OCR-T-A-02-05",
)
STABILITY_CASE_ID = "OCR-T-A-02-05"
STABILITY_REPEAT_COUNT = 2


def deterministic_postprocess_text(exam: Any, value: str) -> str:
    """Return the Gold-free deterministic cleanup view used by the workflow.

    Only a provably outer code fence, Markdown control-only rules/table
    delimiters, HTML line breaks, and narrow scan-spacing variants are removed.
    Missing text is never inferred and a value/sign/unit is never corrected.
    """

    outer = _OUTER_MARKDOWN_FENCE.fullmatch(value)
    if outer is not None:
        value = outer.group("body")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    cleaned_lines: list[str] = []
    for line in value.splitlines():
        stripped = line.strip()
        if _MARKDOWN_HORIZONTAL_RULE.fullmatch(stripped):
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped[1:-1].split("|")]
            if cells and all(
                _MARKDOWN_TABLE_SEPARATOR_CELL.fullmatch(cell) for cell in cells
            ):
                continue
            cleaned_lines.extend(cell for cell in cells if cell)
            continue
        cleaned_lines.append(line)
    text = exam.normalize_layout_text("\n".join(cleaned_lines))
    # A blank around a degree mark and a line-wrap blank after an intact lexical
    # hyphen are presentation variants, not evidence that a unit or word changed.
    text = re.sub(r"\s*°\s*(?=[A-Za-z])", "°", text)
    text = re.sub(r"(?<=[A-Za-z])-[ ]+(?=[A-Za-z])", "-", text)
    return text


def presentation_match_text(exam: Any, value: str) -> str:
    """Backward-compatible name for the deterministic workflow view."""

    return deterministic_postprocess_text(exam, value)


def _failure_buckets(
    detail: Mapping[str, Any],
) -> dict[str, list[str]]:
    token_by_failure = {
        f"CRITICAL_TOKEN_MISMATCH:{row['token_type']}:{row['token']}": row
        for row in detail.get("critical_tokens", [])
        if isinstance(row, Mapping)
    }
    segment_by_failure = {
        f"CRITICAL_READING_ORDER:{row['segment_id']}": row
        for row in detail.get("ordered_segments", [])
        if isinstance(row, Mapping)
    }
    boundary_by_failure = {
        "CRITICAL_ROLE_BOUNDARY:"
        f"{row['before_segment_id']}->{row['after_segment_id']}": row
        for row in detail.get("role_boundaries", [])
        if isinstance(row, Mapping)
    }
    buckets: dict[str, list[str]] = {
        "material_safety": [],
        "structural": [],
        "coverage": [],
        "calibration": [],
    }

    def token_context_present(token: Mapping[str, Any]) -> bool:
        observed = str(
            detail.get("expected_observed", {}).get("observed_text", "")
        ).casefold()
        token_text = str(token.get("token", ""))
        relevant_segments = [
            row
            for row in detail.get("ordered_segments", [])
            if isinstance(row, Mapping)
            and token_text.casefold() in str(row.get("expected", "")).casefold()
        ]
        contexts = (
            [str(row.get("expected", "")) for row in relevant_segments]
            or [str(detail.get("expected_observed", {}).get("expected_text", ""))]
        )
        for context in contexts:
            without_token = context.casefold().replace(token_text.casefold(), " ")
            anchors = {
                item
                for item in re.findall(r"[^\W_]{4,}", without_token, flags=re.UNICODE)
                if not item.isdigit()
            }
            if sum(int(anchor in observed) for anchor in anchors) >= 2:
                return True
        return False

    for failure in detail.get("successor_hard_failures", []):
        if failure in {
            "SOURCE_AS_PRINTED_SILENT_CORRECTION",
            "UNCERTAINTY_CONFIDENT_HALLUCINATION",
        }:
            buckets["material_safety"].append(failure)
            continue
        token = token_by_failure.get(failure)
        if token is not None:
            if token.get("token_type") in {
                "numeric",
                "direction",
                "unit",
                "symbol",
                "negation",
            } and token_context_present(token):
                buckets["material_safety"].append(failure)
            else:
                buckets["coverage"].append(failure)
            continue
        segment = segment_by_failure.get(failure)
        if segment is not None:
            bucket = "structural" if segment.get("observed_found") else "coverage"
            buckets[bucket].append(failure)
            continue
        boundary = boundary_by_failure.get(failure)
        if boundary is not None:
            both_present = (
                boundary.get("before_line") is not None
                and boundary.get("after_line") is not None
            )
            buckets["structural" if both_present else "coverage"].append(failure)
            continue
        if failure == "CLEAN_FALSE_UNCERTAINTY":
            buckets["calibration"].append(failure)
        else:
            buckets["coverage"].append(failure)
    return {key: sorted(set(value)) for key, value in buckets.items()}


def score_case_successor(
    *,
    exam: Any,
    form_case: Mapping[str, Any],
    gold_case: Mapping[str, Any],
    output: Mapping[str, Any],
) -> dict[str, Any]:
    """Preserve Quality scoring while correcting only presentation false gates."""

    quality_detail = exam.score_case(
        form_case=form_case,
        gold_case=gold_case,
        output=output,
    )
    raw_observed_text = str(output["output_text"])
    observed_view = deterministic_postprocess_text(exam, raw_observed_text)
    expected_view = deterministic_postprocess_text(
        exam, str(gold_case["expected_text"])
    )
    baseline_observed_view = exam.normalize_layout_text(raw_observed_text)
    deterministic_normalization_applied = bool(
        observed_view != baseline_observed_view
        and exam.normalize_text(raw_observed_text)
        != exam.normalize_text(str(gold_case["expected_text"]))
    )
    deterministic_literal = exam.character_similarity(expected_view, observed_view)
    adjusted_tokens: list[dict[str, Any]] = []
    matched = 0
    numeric_matched = 0
    numeric_total = 0
    resolved_failures: list[str] = []
    for row in quality_detail["critical_tokens"]:
        adjusted = dict(row)
        token_view = deterministic_postprocess_text(exam, str(row["token"]))
        observed_exact = bool(token_view and token_view in observed_view)
        adjusted["observed_exact"] = observed_exact
        adjusted["quality_observed_exact"] = bool(row["observed_exact"])
        matched += int(observed_exact)
        if row["token_type"] in {"numeric", "direction", "unit", "symbol", "negation"}:
            numeric_total += 1
            numeric_matched += int(observed_exact)
        failure = f"CRITICAL_TOKEN_MISMATCH:{row['token_type']}:{row['token']}"
        if not row["observed_exact"] and observed_exact:
            resolved_failures.append(failure)
        adjusted_tokens.append(adjusted)

    total = len(adjusted_tokens)
    critical_rate = matched / total if total else 1.0
    numeric_rate = numeric_matched / numeric_total if numeric_total else 1.0

    adjusted_segments: list[dict[str, Any]] = []
    cursor = 0
    found_count = 0
    ordered_count = 0
    gold_segment_by_id = {
        str(row.get("segment_id")): row
        for row in gold_case.get("ordered_segments", [])
        if isinstance(row, Mapping)
    }
    for row in quality_detail["ordered_segments"]:
        adjusted = dict(row)
        segment_view = deterministic_postprocess_text(
            exam, str(row.get("expected", ""))
        )
        absolute_position = observed_view.find(segment_view) if segment_view else -1
        ordered_position = (
            observed_view.find(segment_view, cursor) if segment_view else -1
        )
        observed_found = absolute_position >= 0
        observed_in_order = ordered_position >= 0
        adjusted["quality_observed_found"] = bool(row["observed_found"])
        adjusted["quality_observed_in_order"] = bool(row["observed_in_order"])
        adjusted["observed_found"] = observed_found
        adjusted["observed_in_order"] = observed_in_order
        found_count += int(observed_found)
        ordered_count += int(observed_in_order)
        if observed_in_order:
            cursor = ordered_position + len(segment_view)
        segment_id = str(row["segment_id"])
        failure = f"CRITICAL_READING_ORDER:{segment_id}"
        if (
            bool(gold_segment_by_id.get(segment_id, {}).get("hard_gate", False))
            and not row["observed_in_order"]
            and observed_in_order
        ):
            resolved_failures.append(failure)
        adjusted_segments.append(adjusted)
    if adjusted_segments:
        layout_score = (
            found_count / len(adjusted_segments)
            + ordered_count / len(adjusted_segments)
        ) / 2.0
    else:
        layout_score = 1.0
    clean_wrapper_only = bool(
        "CLEAN_SUBSTANTIVE_OMISSION_OR_INSERTION"
        in quality_detail["hard_failures"]
        and deterministic_literal >= 0.98
        and critical_rate == 1.0
        and layout_score == 1.0
        and float(quality_detail["role_boundary_rate"]) == 1.0
        and float(quality_detail["faithfulness_rate"]) == 1.0
    )
    if clean_wrapper_only:
        resolved_failures.append("CLEAN_SUBSTANTIVE_OMISSION_OR_INSERTION")
    successor_hard_failures = sorted(
        set(quality_detail["hard_failures"]) - set(resolved_failures)
    )
    case_score = 100.0 * (
        0.30 * critical_rate
        + 0.30 * float(quality_detail["literal_similarity"])
        + 0.15 * layout_score
        + 0.10 * float(quality_detail["role_boundary_rate"])
        + 0.15 * float(quality_detail["faithfulness_rate"])
    )
    deterministic_postprocess_score = 100.0 * (
        0.30 * critical_rate
        + 0.30 * deterministic_literal
        + 0.15 * layout_score
        + 0.10 * float(quality_detail["role_boundary_rate"])
        + 0.15 * float(quality_detail["faithfulness_rate"])
    )
    detail = {
        **dict(quality_detail),
        "schema_version": "WorkflowOcrCaseScoreSuccessor-v3",
        "quality_case_score": quality_detail["case_score"],
        "quality_hard_failures": list(quality_detail["hard_failures"]),
        "first_pass_case_score": round(
            max(0.0, min(100.0, case_score)), 6
        ),
        "deterministic_postprocess_case_score": round(
            max(0.0, min(100.0, deterministic_postprocess_score)), 6
        ),
        "deterministic_postprocess_literal_similarity": round(
            deterministic_literal, 8
        ),
        "deterministic_normalization_applied": (
            deterministic_normalization_applied or bool(resolved_failures)
        ),
        "critical_token_rate": round(critical_rate, 8),
        "numeric_token_rate": round(numeric_rate, 8),
        "layout_order_rate": round(layout_score, 8),
        "critical_tokens": adjusted_tokens,
        "ordered_segments": adjusted_segments,
        "case_score": round(max(0.0, min(100.0, case_score)), 6),
        "successor_hard_failures": successor_hard_failures,
        "presentation_resolutions": sorted(resolved_failures),
        "presentation_matching_only": True,
        "raw_provider_output_unchanged": True,
        "literal_similarity_unchanged": True,
        "scoring_projection_revision": SCORING_PROJECTION_REVISION,
    }
    buckets = _failure_buckets(detail)
    detail["residual_classification"] = buckets
    if buckets["material_safety"]:
        disposition = "MATERIAL_REVIEW_REQUIRED"
        recovery_class = "SOURCE_COMPARISON_GUARD"
        verified_recovery_status = "NOT_AUTO_RECOVERABLE"
    elif buckets["structural"]:
        disposition = "STRUCTURAL_REVIEW_REQUIRED"
        recovery_class = "SOURCE_STRUCTURE_REVIEW"
        verified_recovery_status = "NOT_AUTO_RECOVERABLE"
    elif buckets["coverage"]:
        disposition = "COVERAGE_RETRY_RECOMMENDED"
        recovery_class = "BOUNDED_PAGE_RECOVERY"
        verified_recovery_status = "UNVERIFIED_REQUIRES_NEW_OCR_OUTPUT"
    elif buckets["calibration"]:
        disposition = "CALIBRATION_ONLY"
        recovery_class = "CALIBRATION_ONLY"
        verified_recovery_status = "NOT_NEEDED"
    elif detail["deterministic_normalization_applied"]:
        disposition = "PASS_AFTER_DETERMINISTIC_NORMALIZATION"
        recovery_class = "DETERMINISTIC_NORMALIZATION"
        verified_recovery_status = "VERIFIED_DETERMINISTIC"
    else:
        disposition = "PASS"
        recovery_class = "NONE"
        verified_recovery_status = "NOT_NEEDED"
    detail["case_disposition"] = disposition
    detail["workflow_recovery_class"] = recovery_class
    detail["verified_recovery_status"] = verified_recovery_status
    detail["downstream_release_blocked"] = bool(
        buckets["material_safety"]
        or buckets["structural"]
        or buckets["coverage"]
    )
    detail["detail_sha256"] = exam.sha256_json(
        {key: value for key, value in detail.items() if key != "detail_sha256"}
    )
    return detail


def score_balanced_sample(
    *,
    exam: Any,
    main_details: Sequence[Mapping[str, Any]],
    repeat_details: Sequence[Mapping[str, Any]],
    stability_texts: Sequence[str],
) -> dict[str, Any]:
    if len(main_details) != len(MAIN_CASE_IDS):
        raise ValueError("OCR_SUCCESSOR_MAIN_CASE_COUNT_MISMATCH")
    if len(repeat_details) != STABILITY_REPEAT_COUNT:
        raise ValueError("OCR_SUCCESSOR_REPEAT_CASE_COUNT_MISMATCH")
    if len(stability_texts) != 1 + STABILITY_REPEAT_COUNT:
        raise ValueError("OCR_SUCCESSOR_STABILITY_TEXT_COUNT_MISMATCH")

    by_family: dict[str, list[float]] = defaultdict(list)
    deterministic_by_family: dict[str, list[float]] = defaultdict(list)
    for detail in main_details:
        family = str(detail["family"])
        by_family[family].append(float(detail["case_score"]))
        deterministic_by_family[family].append(
            float(
                detail.get(
                    "deterministic_postprocess_case_score",
                    detail["case_score"],
                )
            )
        )
    if set(by_family) != set(exam.FAMILIES):
        raise ValueError("OCR_SUCCESSOR_FAMILY_COVERAGE_MISMATCH")
    family_means = {
        family: statistics.fmean(by_family[family]) for family in exam.FAMILIES
    }
    balanced_score = statistics.fmean(family_means.values())
    deterministic_family_means = {
        family: statistics.fmean(deterministic_by_family[family])
        for family in exam.FAMILIES
    }
    deterministic_score = statistics.fmean(
        deterministic_family_means.values()
    )
    hard_scores = [
        float(detail["case_score"])
        for detail in main_details
        if detail.get("difficulty") == "hard"
    ]
    hard_case_mean = statistics.fmean(hard_scores) if hard_scores else 100.0

    critical_rows = [
        token
        for detail in main_details
        for token in detail.get("critical_tokens", [])
        if isinstance(token, Mapping)
    ]
    critical_rate = (
        statistics.fmean(float(bool(row["observed_exact"])) for row in critical_rows)
        if critical_rows
        else 1.0
    )
    repeatability_values = [
        exam.character_similarity(stability_texts[left], stability_texts[right])
        for left, right in ((0, 1), (0, 2), (1, 2))
    ]
    repeatability = statistics.fmean(repeatability_values) * 100.0

    def case_ids(bucket: str) -> list[str]:
        return sorted(
            {
                str(detail["case_id"])
                for detail in main_details
                if detail.get("residual_classification", {}).get(bucket)
            }
        )

    material_ids = case_ids("material_safety")
    structural_ids = case_ids("structural")
    coverage_ids = case_ids("coverage")
    calibration_ids = case_ids("calibration")
    auto_normalization_ids = sorted(
        str(detail["case_id"])
        for detail in main_details
        if detail.get("workflow_recovery_class")
        == "DETERMINISTIC_NORMALIZATION"
    )
    bounded_recovery_ids = sorted(
        str(detail["case_id"])
        for detail in main_details
        if detail.get("residual_classification", {}).get("coverage")
        and not detail.get("residual_classification", {}).get(
            "material_safety"
        )
        and not detail.get("residual_classification", {}).get("structural")
    )
    source_guard_ids = sorted(set(material_ids) | set(structural_ids))

    recovery_ceiling_by_family: dict[str, list[float]] = defaultdict(list)
    for detail in main_details:
        case_id = str(detail["case_id"])
        measured = float(
            detail.get(
                "deterministic_postprocess_case_score",
                detail["case_score"],
            )
        )
        recovery_ceiling_by_family[str(detail["family"])].append(
            100.0 if case_id in bounded_recovery_ids else measured
        )
    recovery_ceiling_family_means = {
        family: statistics.fmean(recovery_ceiling_by_family[family])
        for family in exam.FAMILIES
    }
    unverified_recovery_ceiling = statistics.fmean(
        recovery_ceiling_family_means.values()
    )
    repeat_material = [
        bool(detail.get("residual_classification", {}).get("material_safety"))
        for detail in repeat_details
    ]
    main_stability = next(
        detail for detail in main_details if detail["case_id"] == STABILITY_CASE_ID
    )
    repeated_material_proven = bool(
        main_stability.get("residual_classification", {}).get("material_safety")
    ) and all(repeat_material)

    def score_band(value: float) -> str:
        if value < 60.0:
            return "NOT_RECOMMENDED"
        if value < 80.0:
            return "USE_WITH_CAUTION"
        return "RECOMMENDED"

    model_fail_established = deterministic_score < 60.0 and (
        len(material_ids) >= 2 or repeated_material_proven
    )
    if model_fail_established:
        model_fail_verdict = "FAIL"
        guarded_eligibility = "NOT_RECOMMENDED"
        workflow_disposition = "MODEL_FAIL_ESTABLISHED"
        workflow_eligibility = "NOT_RECOMMENDED"
    elif deterministic_score < 60.0:
        model_fail_verdict = "NOT_ESTABLISHED"
        guarded_eligibility = "NOT_RECOMMENDED_DIAGNOSTIC_ONLY"
        workflow_disposition = "DIAGNOSTIC_ONLY"
        workflow_eligibility = "NOT_RECOMMENDED_DIAGNOSTIC_ONLY"
    elif material_ids:
        model_fail_verdict = "NOT_ESTABLISHED"
        guarded_eligibility = "PASS_WITH_SOURCE_COMPARISON_GUARD"
        workflow_disposition = "SOURCE_COMPARISON_REQUIRED_BEFORE_RELEASE"
        workflow_eligibility = "ELIGIBLE_WITH_SOURCE_COMPARISON_GUARD"
    elif structural_ids:
        model_fail_verdict = "NOT_ESTABLISHED"
        guarded_eligibility = "PASS_WITH_STRUCTURE_GUARD"
        workflow_disposition = "STRUCTURE_REVIEW_REQUIRED_BEFORE_RELEASE"
        workflow_eligibility = "ELIGIBLE_WITH_STRUCTURE_GUARD"
    elif coverage_ids:
        model_fail_verdict = "NOT_ESTABLISHED"
        guarded_eligibility = "PASS_WITH_BOUNDED_PAGE_RECOVERY"
        workflow_disposition = "PAGE_RECOVERY_REQUIRED_BEFORE_RELEASE"
        workflow_eligibility = "ELIGIBLE_AFTER_VERIFIED_PAGE_RECOVERY"
    elif auto_normalization_ids:
        model_fail_verdict = "NOT_ESTABLISHED"
        guarded_eligibility = "RECOMMENDED_AFTER_DETERMINISTIC_NORMALIZATION"
        workflow_disposition = "READY_AFTER_DETERMINISTIC_NORMALIZATION"
        workflow_eligibility = "ELIGIBLE_AFTER_DETERMINISTIC_NORMALIZATION"
    else:
        model_fail_verdict = "NOT_ESTABLISHED"
        guarded_eligibility = "RECOMMENDED"
        workflow_disposition = "READY"
        workflow_eligibility = "ELIGIBLE"

    unrestricted = (
        "ELIGIBLE"
        if deterministic_score >= 80.0
        and not material_ids
        and not structural_ids
        and not coverage_ids
        and not auto_normalization_ids
        else "NOT_ELIGIBLE"
    )
    return {
        "schema_version": "WorkflowOcrBalancedDiagnosticScore-v3",
        "status": "SCORED",
        "score": round(balanced_score, 6),
        "first_pass_score": round(balanced_score, 6),
        "first_pass_score_semantics": (
            "MEASURED_BALANCED_MEAN_OF_EIGHT_OCR_FAMILIES_BEFORE_"
            "WORKFLOW_RECOVERY"
        ),
        "deterministic_postprocess_score": round(
            deterministic_score, 6
        ),
        "deterministic_postprocess_score_semantics": (
            "MEASURED_GOLD_FREE_PRESENTATION_NORMALIZATION_ONLY"
        ),
        "verified_recovery_score": None,
        "unverified_recovery_ceiling": round(
            unverified_recovery_ceiling, 6
        ),
        "unverified_recovery_ceiling_is_achieved_score": False,
        "recovery_credit_policy": (
            "NO_PAGE_RECOVERY_CREDIT_WITHOUT_NEW_FROZEN_OUTPUT_AND_"
            "LOCAL_VERIFICATION"
        ),
        "score_semantics": "FIRST_PASS_BALANCED_MEAN_OF_EIGHT_OCR_FAMILIES",
        "score_band": score_band(balanced_score),
        "deterministic_postprocess_score_band": score_band(
            deterministic_score
        ),
        "hard_case_mean": round(hard_case_mean, 6),
        "critical_token_exact_rate": round(critical_rate, 8),
        "repeatability": round(repeatability, 6),
        "repeatability_pair_count": len(repeatability_values),
        "family_means": {
            family: round(value, 6) for family, value in family_means.items()
        },
        "deterministic_postprocess_family_means": {
            family: round(value, 6)
            for family, value in deterministic_family_means.items()
        },
        "unverified_recovery_ceiling_family_means": {
            family: round(value, 6)
            for family, value in recovery_ceiling_family_means.items()
        },
        "model_fail_verdict": model_fail_verdict,
        "model_fail_established": model_fail_established,
        "model_fail_proof_rule": (
            "deterministic_score_below_60_and_two_distinct_material_cases_or_"
            "three_generation_repeat_material_failure"
        ),
        "guarded_eligibility": guarded_eligibility,
        "unrestricted_eligibility": unrestricted,
        "workflow_eligibility": workflow_eligibility,
        "workflow_disposition": workflow_disposition,
        "material_safety_case_ids": material_ids,
        "structural_case_ids": structural_ids,
        "coverage_case_ids": coverage_ids,
        "calibration_case_ids": calibration_ids,
        "auto_normalization_case_ids": auto_normalization_ids,
        "bounded_recovery_case_ids": bounded_recovery_ids,
        "source_guard_case_ids": source_guard_ids,
        "repeated_material_failure_proven": repeated_material_proven,
        "case_details": [dict(detail) for detail in main_details],
        "repeat_case_details": [dict(detail) for detail in repeat_details],
        "scoring_projection_revision": SCORING_PROJECTION_REVISION,
        "reference_regression_only": True,
        "qualification_eligible": False,
        "full_quality_score_comparison_eligible": False,
        "cross_category_comparison_forbidden": True,
    }
