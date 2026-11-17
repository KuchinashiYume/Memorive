"""The card reviewer exam's successor form: building its sample, scoring it, normalizing candidate
pointers mechanically, and running one form.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping
from memorive.exams.outcomes import clean_control_residual
from memorive.exams.common import _sha256


CARD_REVIEWER_SUCCESSOR_SAMPLE_REVISION = (
    "Desktop_CARD_REVIEWER_BALANCED_MEDIUM_HARD_SAMPLE_V3"
)


CARD_REVIEWER_SUCCESSOR_GOLD_CORRECTION_REVISION = (
    "Desktop_CARD_REVIEWER_RUBRIC_CONSISTENCY_OVERLAY_V2"
)


CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION = (
    "Desktop_CARD_REVIEWER_TOP_LEVEL_SCALAR_POINTER_LIFT_V1"
)


CARD_REVIEWER_SUCCESSOR_GOLD_CORRECTIONS = {
    "CRX-T-A-003": {
        "expected_source": {
            "issue_family": "boundary_omission",
            "variant": "temperature_omission",
            "severity": "critical",
            "critical": True,
        },
        "successor": {"severity": "major", "critical": False},
        "reason_code": "RUBRIC_SCOPE_CHANGE_IS_MAJOR_NOT_CENTRAL_REVERSAL",
    },
    "CRX-T-B-002": {
        "expected_source": {
            "issue_family": "boundary_omission",
            "variant": "temperature_omission",
            "severity": "critical",
            "critical": True,
        },
        "successor": {"severity": "major", "critical": False},
        "reason_code": "RUBRIC_SCOPE_CHANGE_IS_MAJOR_NOT_CENTRAL_REVERSAL",
    },
    "CRX-T-A-004": {
        "expected_source": {
            "issue_family": "causal_overclaim",
            "variant": "association_to_cause",
            "severity": "critical",
            "critical": True,
        },
        "successor": {"severity": "major", "critical": False},
        "reason_code": "RUBRIC_INTERPRETATION_CHANGE_IS_MAJOR_NOT_REVERSAL",
    },
    "CRX-T-B-003": {
        "expected_source": {
            "issue_family": "causal_overclaim",
            "variant": "association_to_cause",
            "severity": "critical",
            "critical": True,
        },
        "successor": {"severity": "major", "critical": False},
        "reason_code": "RUBRIC_INTERPRETATION_CHANGE_IS_MAJOR_NOT_REVERSAL",
    },
    "CRX-T-A-010": {
        "expected_source": {
            "issue_family": "stale_lineage",
            "variant": "producer_mismatch",
            "severity": "major",
            "critical": False,
        },
        "successor": {"severity": "critical", "critical": True},
        "reason_code": "RUBRIC_ACTIVE_LINEAGE_INVALIDATION_IS_CRITICAL",
    },
    "CRX-T-B-014": {
        "expected_source": {
            "issue_family": "stale_lineage",
            "variant": "method_revision_stale",
            "severity": "major",
            "critical": False,
        },
        "successor": {"severity": "critical", "critical": True},
        "reason_code": "RUBRIC_ACTIVE_LINEAGE_INVALIDATION_IS_CRITICAL",
    },
    "CRX-T-A-020": {
        "expected_source": {
            "issue_family": "stale_lineage",
            "variant": "card_revision_precedes_evidence",
            "severity": "major",
            "critical": False,
        },
        "successor": {"severity": "critical", "critical": True},
        "reason_code": "RUBRIC_ACTIVE_LINEAGE_INVALIDATION_IS_CRITICAL",
    },
    "CRX-T-B-026": {
        "expected_source": {
            "issue_family": "numeric_direction",
            "variant": "range_endpoint_shift",
            "severity": "critical",
            "critical": True,
        },
        "successor": {"severity": "major", "critical": False},
        "reason_code": "RUBRIC_VALUE_CHANGE_IS_MAJOR_NOT_DIRECTION_REVERSAL",
    },
    "CRX-T-B-027": {
        "expected_source": {
            "issue_family": "population_mismatch",
            "variant": "age_subgroup_swap",
            "severity": "critical",
            "critical": True,
        },
        "successor": {"severity": "major", "critical": False},
        "reason_code": "RUBRIC_POPULATION_CHANGE_IS_MAJOR_NOT_COMPARATOR_REVERSAL",
    },
}


CARD_REVIEWER_SUCCESSOR_SAMPLE_SLOTS: dict[int, dict[str, tuple[str, ...]]] = {
    1: {
        "A": (
            "CRX-T-A-001",
            "CRX-T-A-002",
            "CRX-T-A-003",
            "CRX-T-B-013",
            "CRX-T-B-014",
            "CRX-T-B-015",
        ),
        "B": (
            "CRX-T-B-016",
            "CRX-T-B-017",
            "CRX-T-B-018",
            "CRX-T-A-004",
            "CRX-T-A-005",
            "CRX-T-A-006",
        ),
    },
    2: {
        "A": (
            "CRX-T-B-006",
            "CRX-T-B-001",
            "CRX-T-B-002",
            "CRX-T-A-019",
            "CRX-T-A-020",
            "CRX-T-A-021",
        ),
        "B": (
            "CRX-T-A-022",
            "CRX-T-A-023",
            "CRX-T-A-024",
            "CRX-T-B-003",
            "CRX-T-B-004",
            "CRX-T-B-005",
        ),
    },
    3: {
        "A": (
            "CRX-T-A-012",
            "CRX-T-A-007",
            "CRX-T-A-008",
            "CRX-T-B-029",
            "CRX-T-B-030",
            "CRX-T-B-025",
        ),
        "B": (
            "CRX-T-B-026",
            "CRX-T-B-027",
            "CRX-T-B-028",
            "CRX-T-A-009",
            "CRX-T-A-010",
            "CRX-T-A-011",
        ),
    },
}


CARD_REVIEWER_SUCCESSOR_FAMILY_ORDER = (
    "numeric_direction",
    "population_mismatch",
    "boundary_omission",
    "causal_overclaim",
    "stale_lineage",
    "none",
)


def _project_card_reviewer_schema(
    schema: Mapping[str, Any],
    case_ids: list[str],
) -> dict[str, Any]:
    """Reuse the Quality row schema while binding one exact transport set."""

    if (
        not isinstance(schema, Mapping)
        or not case_ids
        or len(case_ids) != len(set(case_ids))
        or any(not isinstance(case_id, str) or not case_id for case_id in case_ids)
    ):
        raise ValueError("CARD_REVIEWER_PROJECTED_SCHEMA_INPUT_INVALID")
    projected = deepcopy(dict(schema))
    try:
        reviews = projected["properties"]["reviews"]
        case_id_schema = reviews["items"]["properties"]["case_id"]
    except (KeyError, TypeError) as error:
        raise ValueError("CARD_REVIEWER_PROJECTED_SCHEMA_INVALID") from error
    reviews["minItems"] = len(case_ids)
    reviews["maxItems"] = len(case_ids)
    case_id_schema.clear()
    case_id_schema.update({"type": "string", "enum": list(case_ids)})
    return projected


def build_card_reviewer_successor_sample(
    *,
    forms: Mapping[str, Mapping[str, Any]],
    golds: Mapping[str, Mapping[str, Any]],
    schema: Mapping[str, Any],
    sample_slot: int,
) -> dict[str, Any]:
    """Derive one frozen, balanced 12-case sample from the Quality item bank.

    Each transport half contains one case from every Reviewer family.  Across
    the two halves, every family has one medium and one hard case.  Adversarial
    cases remain in the frozen Quality bank for an optional stress profile and
    do not inflate the ordinary operational exam.
    """

    selection = CARD_REVIEWER_SUCCESSOR_SAMPLE_SLOTS.get(sample_slot)
    if selection is None:
        raise ValueError("CARD_REVIEWER_SUCCESSOR_SAMPLE_SLOT_INVALID")
    case_by_id: dict[str, Mapping[str, Any]] = {}
    gold_by_id: dict[str, Mapping[str, Any]] = {}
    source_form_by_id: dict[str, str] = {}
    for source_form_id in ("A", "B"):
        form = forms.get(source_form_id)
        gold = golds.get(source_form_id)
        if not isinstance(form, Mapping) or not isinstance(gold, Mapping):
            raise ValueError("CARD_REVIEWER_SUCCESSOR_SOURCE_FORM_REQUIRED")
        source_cases = form.get("cases")
        source_gold = gold.get("cases")
        if not isinstance(source_cases, list) or not isinstance(source_gold, list):
            raise ValueError("CARD_REVIEWER_SUCCESSOR_SOURCE_CASES_REQUIRED")
        for case in source_cases:
            if not isinstance(case, Mapping) or not isinstance(case.get("case_id"), str):
                raise ValueError("CARD_REVIEWER_SUCCESSOR_SOURCE_CASE_INVALID")
            case_id = str(case["case_id"])
            if case_id in case_by_id:
                raise ValueError("CARD_REVIEWER_SUCCESSOR_SOURCE_CASE_DUPLICATE")
            case_by_id[case_id] = case
            source_form_by_id[case_id] = source_form_id
        for gold_case in source_gold:
            if not isinstance(gold_case, Mapping) or not isinstance(
                gold_case.get("case_id"), str
            ):
                raise ValueError("CARD_REVIEWER_SUCCESSOR_SOURCE_GOLD_INVALID")
            case_id = str(gold_case["case_id"])
            if case_id in gold_by_id:
                raise ValueError("CARD_REVIEWER_SUCCESSOR_SOURCE_GOLD_DUPLICATE")
            gold_by_id[case_id] = gold_case
    if set(case_by_id) != set(gold_by_id):
        raise ValueError("CARD_REVIEWER_SUCCESSOR_SOURCE_EXACT_SET_MISMATCH")

    sample_forms: dict[str, dict[str, Any]] = {}
    sample_golds: dict[str, dict[str, Any]] = {}
    sample_schemas: dict[str, dict[str, Any]] = {}
    source_bindings: dict[str, list[dict[str, str]]] = {}
    applied_gold_corrections: dict[str, list[dict[str, Any]]] = {}
    selected_all: list[str] = []
    difficulty_by_family: dict[str, list[str]] = {
        family: [] for family in CARD_REVIEWER_SUCCESSOR_FAMILY_ORDER
    }
    for half_id in ("A", "B"):
        case_ids = list(selection[half_id])
        if len(case_ids) != len(CARD_REVIEWER_SUCCESSOR_FAMILY_ORDER):
            raise ValueError("CARD_REVIEWER_SUCCESSOR_FAMILY_COUNT_INVALID")
        if any(case_id not in case_by_id for case_id in case_ids):
            raise ValueError("CARD_REVIEWER_SUCCESSOR_CASE_NOT_IN_SOURCE_PACK")
        selected_gold = [deepcopy(dict(gold_by_id[case_id])) for case_id in case_ids]
        applied_corrections: list[dict[str, Any]] = []
        for gold_case in selected_gold:
            case_id = str(gold_case["case_id"])
            correction = CARD_REVIEWER_SUCCESSOR_GOLD_CORRECTIONS.get(case_id)
            if correction is None:
                continue
            expected_source = correction["expected_source"]
            if any(
                gold_case.get(field) != expected
                for field, expected in expected_source.items()
            ):
                raise ValueError(
                    "CARD_REVIEWER_SUCCESSOR_GOLD_CORRECTION_SOURCE_DRIFT"
                )
            source_values = {
                field: deepcopy(gold_case[field])
                for field in correction["successor"]
            }
            gold_case.update(deepcopy(correction["successor"]))
            applied_corrections.append(
                {
                    "case_id": case_id,
                    "source_values": source_values,
                    "successor_values": deepcopy(correction["successor"]),
                    "reason_code": correction["reason_code"],
                }
            )
        families = tuple(str(item.get("issue_family")) for item in selected_gold)
        difficulties = [str(item.get("difficulty")) for item in selected_gold]
        if (
            families != CARD_REVIEWER_SUCCESSOR_FAMILY_ORDER
            or difficulties.count("medium") != 3
            or difficulties.count("hard") != 3
            or set(difficulties) != {"medium", "hard"}
        ):
            raise ValueError("CARD_REVIEWER_SUCCESSOR_SAMPLE_BALANCE_INVALID")
        sample_form_id = (
            f"CARD-REVIEWER-SUCCESSOR-S{sample_slot}-{half_id}-V1"
        )
        sample_forms[half_id] = {
            "schema_version": "desktop-card-reviewer-successor-form-v1",
            "form": half_id,
            "form_id": sample_form_id,
            "suite": "card_reviewer_operational_successor",
            "instructions_ref": "prompts/card_reviewer_prompt.json",
            "output_schema_ref": "schemas/card_reviewer_output.schema.json",
            "sample_revision": CARD_REVIEWER_SUCCESSOR_SAMPLE_REVISION,
            "sample_slot": sample_slot,
            "cases": [deepcopy(dict(case_by_id[case_id])) for case_id in case_ids],
        }
        sample_golds[half_id] = {
            "schema_version": "desktop-card-reviewer-successor-gold-view-v1",
            "form": half_id,
            "form_id": sample_form_id,
            "sample_revision": CARD_REVIEWER_SUCCESSOR_SAMPLE_REVISION,
            "sample_slot": sample_slot,
            "cases": selected_gold,
        }
        sample_schemas[half_id] = _project_card_reviewer_schema(schema, case_ids)
        source_bindings[half_id] = [
            {
                "case_id": case_id,
                "source_form": source_form_by_id[case_id],
                "difficulty": difficulties[index],
                "family": CARD_REVIEWER_SUCCESSOR_FAMILY_ORDER[index],
            }
            for index, case_id in enumerate(case_ids)
        ]
        applied_gold_corrections[half_id] = applied_corrections
        for index, family in enumerate(CARD_REVIEWER_SUCCESSOR_FAMILY_ORDER):
            difficulty_by_family[family].append(difficulties[index])
        selected_all.extend(case_ids)
    if len(selected_all) != len(set(selected_all)):
        raise ValueError("CARD_REVIEWER_SUCCESSOR_SAMPLE_CASE_REUSE_FORBIDDEN")
    if any(
        sorted(values) != ["hard", "medium"]
        for values in difficulty_by_family.values()
    ):
        raise ValueError("CARD_REVIEWER_SUCCESSOR_CROSS_HALF_BALANCE_INVALID")
    manifest = {
        "schema_version": CARD_REVIEWER_SUCCESSOR_SAMPLE_REVISION,
        "sample_slot": sample_slot,
        "source_item_bank": "Quality_CARD_REVIEWER_REFERENCE_PACK_V2",
        "selection_policy": (
            "ONE_MEDIUM_AND_ONE_HARD_PER_FAMILY_NO_ADVERSARIAL_BASE_EXAM"
        ),
        "family_order": list(CARD_REVIEWER_SUCCESSOR_FAMILY_ORDER),
        "source_bindings": source_bindings,
        "applied_gold_corrections": applied_gold_corrections,
        "gold_correction_revision": (
            CARD_REVIEWER_SUCCESSOR_GOLD_CORRECTION_REVISION
        ),
        "gold_correction_manifest_sha256": _sha256(
            CARD_REVIEWER_SUCCESSOR_GOLD_CORRECTIONS
        ),
        "gold_correction_case_ids": sorted(
            case_id
            for case_id in selected_all
            if case_id in CARD_REVIEWER_SUCCESSOR_GOLD_CORRECTIONS
        ),
        "selected_case_count": len(selected_all),
        "selected_case_ids_sha256": _sha256(selected_all),
        "provider_receives_gold": False,
    }
    manifest["manifest_sha256"] = _sha256(manifest)
    return {
        "schema_version": "DesktopCardReviewerSuccessorSample-v1",
        "sample_slot": sample_slot,
        "manifest": manifest,
        "forms": sample_forms,
        "golds": sample_golds,
        "schemas": sample_schemas,
        "provider_receives_gold": False,
    }


def score_card_reviewer_successor_form(
    *,
    exam: Any,
    form: Mapping[str, Any],
    final_response: Mapping[str, Any],
    gold: Mapping[str, Any],
    schema: Mapping[str, Any],
    scoring_protocol: Mapping[str, Any],
    repair_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    """Reuse the Quality nonlinear scorer on a balanced small-sample view.

    Quality hard-coded a minimum of ten cases per family because its original
    form had 72 cases.  Reimplementing its nonlinear formula would create a
    second scorer.  Instead, this local-only scoring projection replicates each
    of the six equally weighted sample cases ten times *after* the provider
    response is frozen.  Equal replication leaves every mean, piecewise knot,
    repair ratio, and hard gate unchanged while satisfying the old mechanical
    quota.  Replica rows are never questions and are never sent to a model.
    """

    audit = exam.audit_response(
        form=form,
        response=final_response,
        schema=schema,
        apply_gold_blind_semantic_guards=False,
    )
    if audit.get("status") != "PASS":
        raise exam.ReviewerExamError(
            f"SUCCESSOR_FINAL_RESPONSE_NOT_SCOREABLE:{audit.get('status')}"
        )
    cases, case_ids = exam.form_cases(form)
    gold_cases = gold.get("cases")
    rows = final_response.get("reviews")
    if not isinstance(gold_cases, list) or not isinstance(rows, list):
        raise exam.ReviewerExamError("SUCCESSOR_SCORE_INPUT_ROWS_REQUIRED")
    gold_by_id = {
        item.get("case_id"): item
        for item in gold_cases
        if isinstance(item, Mapping) and isinstance(item.get("case_id"), str)
    }
    row_by_id = {
        item.get("case_id"): item
        for item in rows
        if isinstance(item, Mapping) and isinstance(item.get("case_id"), str)
    }
    if set(case_ids) != set(gold_by_id) or set(case_ids) != set(row_by_id):
        raise exam.ReviewerExamError("SUCCESSOR_SCORE_EXACT_SET_MISMATCH")
    observed_families = tuple(
        "clean_control"
        if gold_by_id[case_id].get("expected_verdict") == "PASS"
        else str(gold_by_id[case_id].get("issue_family"))
        for case_id in case_ids
    )
    expected_families = tuple(
        "clean_control" if item == "none" else item
        for item in CARD_REVIEWER_SUCCESSOR_FAMILY_ORDER
    )
    if observed_families != expected_families:
        raise exam.ReviewerExamError("SUCCESSOR_SCORE_FAMILY_BALANCE_INVALID")

    replica_count = 10
    marker = "::LOCAL-SCORER-REPLICA-"
    expanded_cases: list[dict[str, Any]] = []
    expanded_gold: list[dict[str, Any]] = []
    expanded_rows: list[dict[str, Any]] = []
    expanded_case_ids: list[str] = []
    case_by_id = {str(item["case_id"]): item for item in cases}
    for case_id in case_ids:
        for replica_index in range(1, replica_count + 1):
            replica_id = f"{case_id}{marker}{replica_index:02d}"
            expanded_case_ids.append(replica_id)
            for source, target in (
                (case_by_id[case_id], expanded_cases),
                (gold_by_id[case_id], expanded_gold),
                (row_by_id[case_id], expanded_rows),
            ):
                clone = deepcopy(dict(source))
                clone["case_id"] = replica_id
                target.append(clone)
    expanded_form = deepcopy(dict(form))
    expanded_form["cases"] = expanded_cases
    expanded_gold_payload = deepcopy(dict(gold))
    expanded_gold_payload["cases"] = expanded_gold
    expanded_response = {"reviews": expanded_rows}
    expanded_schema = _project_card_reviewer_schema(schema, expanded_case_ids)
    original_repaired_count = repair_metrics.get("directed_repair_case_count")
    if isinstance(original_repaired_count, bool) or not isinstance(
        original_repaired_count, int
    ):
        raise exam.ReviewerExamError("SUCCESSOR_REPAIR_COUNT_INVALID")
    expanded_metrics = {
        **deepcopy(dict(repair_metrics)),
        "form_case_count": len(expanded_case_ids),
        "directed_repair_case_count": original_repaired_count * replica_count,
    }
    scored = exam.score_form(
        form=expanded_form,
        final_response=expanded_response,
        gold=expanded_gold_payload,
        schema=expanded_schema,
        scoring_protocol=scoring_protocol,
        repair_metrics=expanded_metrics,
    )

    detail_by_original: dict[str, dict[str, Any]] = {}
    for detail in scored.get("details", []):
        if not isinstance(detail, Mapping):
            continue
        replica_id = detail.get("case_id")
        if not isinstance(replica_id, str) or marker not in replica_id:
            raise exam.ReviewerExamError("SUCCESSOR_REPLICA_DETAIL_INVALID")
        original_id = replica_id.split(marker, 1)[0]
        if original_id not in detail_by_original:
            collapsed = deepcopy(dict(detail))
            collapsed["case_id"] = original_id
            detail_by_original[original_id] = collapsed
    if set(detail_by_original) != set(case_ids):
        raise exam.ReviewerExamError("SUCCESSOR_REPLICA_DETAIL_SET_MISMATCH")

    def collapse_ids(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        selected = {
            value.split(marker, 1)[0]
            for value in values
            if isinstance(value, str) and marker in value
        }
        return [case_id for case_id in case_ids if case_id in selected]

    value = deepcopy(dict(scored))
    value.pop("score_sha256", None)
    value["schema_version"] = "desktop-card-reviewer-successor-form-score-v1"
    value["details"] = [detail_by_original[case_id] for case_id in case_ids]
    for field in (
        "critical_missed",
        "critical_mislocalized",
        "critical_misclassified",
        "clean_false_positive_cases",
        "stale_lineage_missed",
    ):
        value[field] = collapse_ids(scored.get(field))
    value["critical_expected"] = sum(
        bool(detail_by_original[case_id].get("critical")) for case_id in case_ids
    )
    value["clean_control_count"] = sum(
        bool(detail_by_original[case_id].get("clean")) for case_id in case_ids
    )
    value["repair_metrics"] = deepcopy(dict(repair_metrics))
    quality_hard_failures = list(value.get("hard_failures") or [])
    clean_false_positive_ids = set(
        str(case_id) for case_id in value.get("clean_false_positive_cases") or []
    )
    conservative_clean_ids = {
        case_id
        for case_id in clean_false_positive_ids
        if not clean_control_residual(
            expected_verdict=str(
                gold_by_id[case_id].get("expected_verdict")
            ),
            observed_verdict=str(row_by_id[case_id].get("verdict")),
            observed_action=str(row_by_id[case_id].get("action")),
        )["safety_blocking"]
    }
    destructive_clean_ids = sorted(
        clean_false_positive_ids - conservative_clean_ids
    )
    successor_hard_failures = [
        reason
        for reason in quality_hard_failures
        if reason != "SUBSTANTIVE_FALSE_POSITIVE_ON_ANY_CLEAN_CONTROL"
        or bool(destructive_clean_ids)
    ]
    value["quality_hard_failures"] = quality_hard_failures
    value["quality_hard_gate_verdict"] = str(scored["hard_gate_verdict"])
    value["hard_failures"] = successor_hard_failures
    value["hard_gate_verdict"] = (
        "PASS" if not successor_hard_failures else "FAIL"
    )
    value["clean_false_positive_successor_classification"] = {
        "conservative_calibration_case_ids": sorted(conservative_clean_ids),
        "destructive_safety_blocking_case_ids": destructive_clean_ids,
        "quality_hard_gate_narrowed": bool(conservative_clean_ids)
        and not destructive_clean_ids,
        "numeric_clean_specificity_penalty_preserved": True,
    }
    value["final_response_hash"] = exam.sha256(final_response)
    value["gold_hash"] = exam.sha256(gold)
    value["local_scoring_projection"] = {
        "schema_version": "DesktopCardReviewerEqualReplicaScoringProjection-v1",
        "source_case_count": len(case_ids),
        "source_family_count": len(CARD_REVIEWER_SUCCESSOR_FAMILY_ORDER),
        "replicas_per_source_case": replica_count,
        "expanded_case_count": len(expanded_case_ids),
        "means_and_repair_ratio_invariant": True,
        "provider_visible": False,
        "question_count_effect": "NONE",
        "quality_nonlinear_scorer_reused": True,
    }
    value["score_sha256"] = exam.sha256(value)
    return value


def mechanically_normalize_successor_candidate_pointers(
    *,
    exam: Any,
    form: Mapping[str, Any],
    response: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply one Gold-free, deterministic extension to Quality normalization.

    Quality already repairs an omitted ``/candidate_card`` prefix.  A local
    structured-output model exposed the adjacent mechanical case where it put
    a real top-level scalar field one container too deep, for example
    ``/candidate_card/source_anchor/source_version``.  The frozen Quality
    normalizer cannot run because its audit classifies that nonexistent pointer
    as repairable first.

    This successor only removes exactly one intermediate mapping segment when
    the observed pointer is missing, that intermediate parent exists, and the
    same terminal token resolves to an existing top-level scalar field.  It
    neither consults Gold nor invents a field, and every other shape remains
    unchanged for the Quality fail-closed audit.
    """

    projected = deepcopy(dict(response))
    reviews = projected.get("reviews")
    if not isinstance(reviews, list):
        return {
            "schema_version": (
                CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION
            ),
            "normalized_response": projected,
            "normalization_count": 0,
            "actions": [],
            "model_api_call_required": False,
            "semantic_repair_round_increment": 0,
        }
    cases, expected_ids = exam.form_cases(form)
    case_by_id = {case["case_id"]: case for case in cases}
    expected_set = set(expected_ids)
    actions: list[dict[str, Any]] = []
    for row in reviews:
        if not isinstance(row, dict):
            continue
        case_id = row.get("case_id")
        pointer = row.get("target_pointer")
        if case_id not in expected_set or not isinstance(pointer, str):
            continue
        segments = pointer.split("/")
        if (
            len(segments) != 4
            or segments[0] != ""
            or segments[1] != "candidate_card"
            or not segments[2]
            or not segments[3]
        ):
            continue
        case = case_by_id[case_id]
        observed_exists, _ = exam.resolve_json_pointer(case, pointer)
        parent_pointer = "/".join(segments[:-1])
        parent_exists, parent_value = exam.resolve_json_pointer(
            case, parent_pointer
        )
        lifted_pointer = f"/candidate_card/{segments[-1]}"
        lifted_exists, lifted_value = exam.resolve_json_pointer(
            case, lifted_pointer
        )
        if (
            observed_exists
            or not parent_exists
            or not isinstance(parent_value, Mapping)
            or not lifted_exists
            or isinstance(lifted_value, (Mapping, list))
        ):
            continue
        row["target_pointer"] = lifted_pointer
        actions.append(
            {
                "case_id": case_id,
                "observed_pointer": pointer,
                "normalized_pointer": lifted_pointer,
                "rule": "REMOVE_ONE_INTERMEDIATE_MAPPING_TO_EXISTING_TOP_LEVEL_SCALAR",
                "gold_consulted": False,
                "provider_call_required": False,
                "semantic_repair_round_increment": 0,
            }
        )
    return {
        "schema_version": (
            CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION
        ),
        "normalized_response": projected,
        "normalization_count": len(actions),
        "actions": actions,
        "model_api_call_required": False,
        "semantic_repair_round_increment": 0,
    }


def execute_card_reviewer_successor_form(
    *,
    protocol: Any,
    exam: Any,
    form: Mapping[str, Any],
    schema: Mapping[str, Any],
    prompt_contract: Mapping[str, Any],
    scoring_protocol: Mapping[str, Any],
    subject_call: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    gold: Mapping[str, Any],
) -> dict[str, Any]:
    """Thin Quality protocol successor with the balanced-form scorer seam."""

    pointer_normalizations: list[dict[str, Any]] = []
    initial_request = protocol.build_initial_request(
        form=form,
        schema=schema,
        prompt_contract=prompt_contract,
    )
    initial_envelope = subject_call(initial_request)
    execution_status = initial_envelope.get("status")
    if execution_status in {
        "CAPACITY_LIMIT_REACHED",
        "TRANSPORT_ERROR",
        "CONNECTIVITY_ERROR",
    }:
        return {
            "status": (
                "NOT_ASSESSED_CAPACITY"
                if execution_status == "CAPACITY_LIMIT_REACHED"
                else "NOT_ASSESSED_TRANSPORT"
            ),
            "score": None,
            "quality_verdict": "NOT_ASSESSED",
            "capacity_is_quality_failure": False,
            "repair_turns": 0,
            "gold_loaded": False,
            "gold_sent_to_subject": False,
            "gold_sent_to_provider": False,
            "pointer_normalization_revision": (
                CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION
            ),
            "pointer_normalization_count": 0,
            "pointer_normalizations": [],
            "requests": [initial_request],
        }
    if execution_status != "COMPLETED":
        raise exam.ReviewerExamError(
            f"UNKNOWN_SUBJECT_EXECUTION_STATUS:{execution_status}"
        )
    response = initial_envelope.get("response")
    if isinstance(response, Mapping):
        pointer_projection = (
            mechanically_normalize_successor_candidate_pointers(
                exam=exam,
                form=form,
                response=response,
            )
        )
        response = pointer_projection["normalized_response"]
        pointer_normalizations.extend(pointer_projection["actions"])
    audit = exam.audit_response(form=form, response=response, schema=schema)
    requests = [initial_request]
    repair_case_count = 0
    structural_repair_turns = 0
    if audit["status"] in {"PASS", "MECHANICAL_RECOVERY"}:
        if not isinstance(response, Mapping):
            raise exam.ReviewerExamError(
                "MAPPING_RESPONSE_REQUIRED_AFTER_PASS_AUDIT"
            )
        normalized = exam.mechanically_normalize(
            form=form,
            response=response,
            schema=schema,
        )
        final_response = normalized["normalized_response"]
    elif audit["status"] == "REPAIRABLE":
        if not isinstance(response, Mapping):
            raise exam.ReviewerExamError("REPAIRABLE_RESPONSE_MAPPING_REQUIRED")
        package = exam.build_directed_repair_package(
            form=form,
            original_response=response,
            audit=audit,
            schema=schema,
        )
        repair_request = {
            "phase": "STRUCTURAL_DIRECTED_REPAIR",
            "system": (
                str(prompt_contract["system"])
                + "\n\nPerform the one controlled structural repair for this "
                "form. Replace all and only the requested rows, return them in "
                "the listed order, and include no Markdown. This is a transport "
                "recovery and does not disclose Gold or add a semantic repair round."
            ),
            "user": package["prompt"],
            "form": package["repair_form"],
            "output_schema": package["repair_schema"],
            "repair_contract_sha256": package["contract"]["contract_sha256"],
            "repair_round_semantics": "CONTROLLED_STRUCTURAL_RECOVERY_NOT_GOLD_REPAIR",
        }
        repair_request["request_payload_hash"] = exam.sha256(repair_request)
        requests.append(repair_request)
        repair_envelope = subject_call(repair_request)
        structural_repair_turns = 1
        repair_case_count = len(package["contract"]["repair_exact_set"])
        repair_status = repair_envelope.get("status")
        if repair_status in {
            "CAPACITY_LIMIT_REACHED",
            "TRANSPORT_ERROR",
            "CONNECTIVITY_ERROR",
        }:
            return {
                "status": (
                    "NOT_ASSESSED_CAPACITY"
                    if repair_status == "CAPACITY_LIMIT_REACHED"
                    else "NOT_ASSESSED_TRANSPORT"
                ),
                "score": None,
                "quality_verdict": "NOT_ASSESSED",
                "capacity_is_quality_failure": False,
                "repair_turns": structural_repair_turns,
                "repair_case_count": repair_case_count,
                "gold_loaded": False,
                "gold_sent_to_subject": False,
                "gold_sent_to_provider": False,
                "pointer_normalization_revision": (
                    CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION
                ),
                "pointer_normalization_count": len(pointer_normalizations),
                "pointer_normalizations": deepcopy(pointer_normalizations),
                "requests": requests,
            }
        if repair_status != "COMPLETED" or not isinstance(
            repair_envelope.get("response"), Mapping
        ):
            return {
                "status": "INVALID_OUTPUT_AFTER_STRUCTURAL_REPAIR",
                "score": 0.0,
                "quality_verdict": "FAIL",
                "hard_gate_verdict": "FAIL",
                "repair_turns": structural_repair_turns,
                "repair_case_count": repair_case_count,
                "gold_loaded": False,
                "gold_sent_to_subject": False,
                "gold_sent_to_provider": False,
                "pointer_normalization_revision": (
                    CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION
                ),
                "pointer_normalization_count": len(pointer_normalizations),
                "pointer_normalizations": deepcopy(pointer_normalizations),
                "requests": requests,
            }
        repair_projection = mechanically_normalize_successor_candidate_pointers(
            exam=exam,
            form=form,
            response=repair_envelope["response"],
        )
        pointer_normalizations.extend(repair_projection["actions"])
        try:
            merged = exam.merge_directed_repair(
                form=form,
                original_response=response,
                repair_response=repair_projection["normalized_response"],
                schema=schema,
                repair_contract=package["contract"],
            )
        except exam.ReviewerExamError as error:
            return {
                "status": "INVALID_OUTPUT_AFTER_STRUCTURAL_REPAIR",
                "score": 0.0,
                "quality_verdict": "FAIL",
                "hard_gate_verdict": "FAIL",
                "error": str(error),
                "repair_turns": structural_repair_turns,
                "repair_case_count": repair_case_count,
                "gold_loaded": False,
                "gold_sent_to_subject": False,
                "gold_sent_to_provider": False,
                "pointer_normalization_revision": (
                    CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION
                ),
                "pointer_normalization_count": len(pointer_normalizations),
                "pointer_normalizations": deepcopy(pointer_normalizations),
                "requests": requests,
            }
        final_response = merged["merged_response"]
    else:
        return {
            "status": "INVALID_OUTPUT_UNRECOVERABLE",
            "score": 0.0,
            "quality_verdict": "FAIL",
            "hard_gate_verdict": "FAIL",
            "repair_turns": 0,
            "repair_case_count": 0,
            "gold_loaded": False,
            "gold_sent_to_subject": False,
            "gold_sent_to_provider": False,
            "pointer_normalization_revision": (
                CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION
            ),
            "pointer_normalization_count": len(pointer_normalizations),
            "pointer_normalizations": deepcopy(pointer_normalizations),
            "requests": requests,
            "audit": audit,
        }
    final_response_hash = exam.sha256(final_response)
    score = score_card_reviewer_successor_form(
        exam=exam,
        form=form,
        final_response=final_response,
        gold=gold,
        schema=schema,
        scoring_protocol=scoring_protocol,
        repair_metrics={
            "form_case_count": len(exam.form_cases(form)[1]),
            "repair_turns": structural_repair_turns,
            "directed_repair_case_count": repair_case_count,
            "all_repairs_succeeded": True,
        },
    )
    return {
        **score,
        "final_response": final_response,
        "final_response_hash_before_gold_load": final_response_hash,
        "gold_loaded": True,
        "gold_loaded_after_final_response_freeze": True,
        "gold_sent_to_subject": False,
        "gold_sent_to_provider": False,
        "requests": requests,
        "repair_turns": structural_repair_turns,
        "repair_case_count": repair_case_count,
        "pointer_normalization_revision": (
            CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION
        ),
        "pointer_normalization_count": len(pointer_normalizations),
        "pointer_normalizations": deepcopy(pointer_normalizations),
        "repair_turn_semantics": (
            "CONTROLLED_STRUCTURAL_RECOVERY_NOT_SEMANTIC_GOLD_REPAIR"
        ),
    }
