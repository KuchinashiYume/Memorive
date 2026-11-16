"""Card reviewer exams.
"""
from __future__ import annotations

from memorive.models.provider_aliases import matches_result_model
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import re
from time import monotonic
from typing import Any, Callable, Mapping
import uuid
from model_gateway.resource_limits import resolve_call_limits
from memorive_settings.exam_successor import (
    ExecutionOutcome,
    Scorability,
    admit_quality_fail,
    build_repair_round,
    classify_execution_outcome,
    clean_control_residual,
    cumulative_category_repair_penalty,
    workflow_exam_structured_output_policy,
)
from memorive.models.output_limits import (
    WORKFLOW_EXAM_REPLY_TOKEN_CEILING,
    workflow_exam_output_limit_contract,
)

from memorive_settings.workflow_exam.common import (
    AUTHORIZATION_SCHEMA,
    EXECUTOR_REF,
    MAX_OUTPUT_TOKENS,
    PLAN_SCHEMA,
    REQUEST_TIMEOUT_SECONDS,
    RESULT_SCHEMA,
    SAFE_PACKAGE,
    _compact,
    _effective_structured_prompt_metrics,
    _file_sha256,
    _load_json,
    _not_available,
    _not_run,
    _public_forms,
    _serialized_request,
    _sha256,
    _write_json_create_only,
)


CARD_REVIEWER_EXAM_CATEGORY_ID = "Quality_CARD_REVIEWER_REFERENCE_REGRESSION"


CARD_REVIEWER_ROLE_ID = "CARD_REVIEWER"


CARD_REVIEWER_SCORING_PROJECTION_REVISION = (
    "Desktop_CARD_REVIEWER_ROLE_PROJECTION_V5"
)


CARD_REVIEWER_SCORE_SEMANTICS = "FIRST_PASS_NONLINEAR_ROLE_ABILITY"


CARD_REVIEWER_OPERATIONAL_POLICY_REVISION = (
    "Desktop_CARD_REVIEWER_OPERATIONAL_ELIGIBILITY_V2_Core_CLOSURE"
)


CARD_REVIEWER_OPERATIONAL_PASSING_SCORE = 80.0


CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION = (
    "Desktop_CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_V6"
)


CORE_CARD_REVIEWER_LIFECYCLE_REVISION = (
    "Core_FULL_ONE_REPAIR_DELTA_GOVERNED_DISPOSITION_V1"
)


CORE_CARD_REVIEWER_WORKLOAD_PROFILE_REVISION = (
    "Desktop_Core_CORE19_ACTUAL_CLEANMD_MEAN_X2_V2"
)


Core_CORE_FINAL_SUPPORTED_PAPER_COUNT = 19


Core_CORE_FINAL_CLEANMD_TOTAL_CHARS = 1_306_218


Core_CORE_FINAL_CLEANMD_MEAN_CHARS = 68_748.315789


CORE_CARD_REVIEWER_WORKLOAD_MULTIPLIER = 2.0


CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT = 137_497


CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_MULTIPLIER = 2.0


CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT = 137_497


CORE_CARD_REVIEWER_SOURCE_MANIFEST_SHA256 = (
    "65606B32990005793E563D9C2B0B7AA5CF96CA3C7CD56FA7A2440CB4B22418A8"
)


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


def core_card_reviewer_workload_profile() -> dict[str, Any]:
    """Return the aggregate-only Core workload reference bundled in the EXE.

    The source texts and private PDFs are deliberately not embedded.  The
    aggregate is bound to the formal Core publication manifest and uses the
    19 text-supported Core Final papers; the correctly blocked Guo2011 OCR case
    is excluded from the mean.
    """

    profile = {
        "schema_version": CORE_CARD_REVIEWER_WORKLOAD_PROFILE_REVISION,
        "source_manifest_sha256": CORE_CARD_REVIEWER_SOURCE_MANIFEST_SHA256,
        "paper_count": Core_CORE_FINAL_SUPPORTED_PAPER_COUNT,
        "length_unit": "UNICODE_CODE_POINTS_IN_CLEANMD",
        "total_cleanmd_chars": Core_CORE_FINAL_CLEANMD_TOTAL_CHARS,
        "mean_cleanmd_chars": Core_CORE_FINAL_CLEANMD_MEAN_CHARS,
        "exam_multiplier": CORE_CARD_REVIEWER_WORKLOAD_MULTIPLIER,
        "soft_effective_prompt_char_limit": (
            CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
        ),
        "effective_prompt_char_limit": (
            CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
        ),
        "absolute_prompt_multiplier": (
            CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_MULTIPLIER
        ),
        "absolute_effective_prompt_char_limit": (
            CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT
        ),
        "soft_limit_semantics": (
            "TARGET_FOR_TOTAL_SUBJECT_VISIBLE_EXAM_INPUT_AND_EACH_TRANSPORT_SHARD"
        ),
        "absolute_limit_semantics": (
            "IRREDUCIBLE_ATOMIC_EXCEPTION_ONLY_OTHERWISE_NOT_ASSESSED"
        ),
        "gold_or_source_text_embedded": False,
    }
    profile["profile_sha256"] = _sha256(profile)
    return profile


def _api_worst_case_request_reserve_cny(
    target: Mapping[str, Any],
    prompt_metrics: Mapping[str, Any],
    request_max_output_tokens: int | None,
) -> float | None:
    """Return a conservative pre-send reserve for explicitly opted-in APIs.

    The UTF-8 byte count is a safe upper bound for the tokenizer input count
    used by the supported byte-backed model APIs.  The output side uses the
    exact provider ceiling bound into the request, not an observed average.
    """

    if target.get("enforce_per_call_worst_case_reserve") is not True:
        return None
    pricing = target.get("pricing_estimate_cny_per_million")
    input_bytes = prompt_metrics.get("effective_prompt_utf8_bytes")
    if (
        not isinstance(pricing, Mapping)
        or isinstance(input_bytes, bool)
        or not isinstance(input_bytes, int)
        or input_bytes < 0
        or isinstance(request_max_output_tokens, bool)
        or not isinstance(request_max_output_tokens, int)
        or request_max_output_tokens <= 0
    ):
        raise ValueError("WORKFLOW_MODEL_EXAM_WORST_CASE_RESERVE_PROFILE_INVALID")
    input_rate = pricing.get("input")
    output_rate = pricing.get("output")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= float(value) <= 100_000
        for value in (input_rate, output_rate)
    ):
        raise ValueError("WORKFLOW_MODEL_EXAM_WORST_CASE_RESERVE_PROFILE_INVALID")
    return round(
        (
            input_bytes * float(input_rate)
            + request_max_output_tokens * float(output_rate)
        )
        / 1_000_000,
        9,
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


def card_reviewer_comparison_metadata(
    *,
    reference_pack_id: str,
    reference_pack_revision: str,
    reference_pack_sha256: str,
    scoring_protocol_revision: str,
    scoring_protocol_sha256: str,
    executor_ref: str,
) -> dict[str, Any]:
    """Build the exact same-category cohort binding for Reviewer scores."""

    values = {
        "exam_category_id": CARD_REVIEWER_EXAM_CATEGORY_ID,
        "role_id": CARD_REVIEWER_ROLE_ID,
        "reference_pack_id": reference_pack_id,
        "reference_pack_revision": reference_pack_revision,
        "reference_pack_sha256": reference_pack_sha256,
        "scoring_protocol_revision": scoring_protocol_revision,
        "scoring_protocol_sha256": scoring_protocol_sha256,
        "scoring_projection_revision": (
            CARD_REVIEWER_SCORING_PROJECTION_REVISION
        ),
        "score_semantics": CARD_REVIEWER_SCORE_SEMANTICS,
    }
    for key, value in values.items():
        if not isinstance(value, str) or not value:
            raise ValueError(f"WORKFLOW_MODEL_EXAM_COMPARISON_BINDING_INVALID:{key}")
    if not isinstance(executor_ref, str) or not executor_ref:
        raise ValueError(
            "WORKFLOW_MODEL_EXAM_COMPARISON_BINDING_INVALID:executor_ref"
        )
    for key in ("reference_pack_sha256", "scoring_protocol_sha256"):
        if not re.fullmatch(r"[A-F0-9]{64}", values[key]):
            raise ValueError(f"WORKFLOW_MODEL_EXAM_COMPARISON_BINDING_INVALID:{key}")
    binding_sha256 = _sha256(values)
    return {
        **{
            key: value
            for key, value in values.items()
            if key != "score_semantics"
        },
        "comparison_score_semantics": values["score_semantics"],
        "comparison_binding_sha256": binding_sha256,
        "comparison_cohort_id": f"CARD_REVIEWER:{binding_sha256}",
        "execution_adapter_ref": executor_ref,
        "horizontal_comparison_eligible": True,
        "horizontal_comparison_allowed_only_with_same_cohort": True,
        "cross_category_comparison_forbidden": True,
        "global_ranking_forbidden": True,
    }


_CARD_REVIEWER_OUTPUT_FIELDS = frozenset(
    {
        "verdict",
        "issue_family",
        "severity",
        "target_pointer",
        "status",
        "evidence_refs",
        "action",
        "reason",
    }
)


_CARD_REVIEWER_SCORER_FIELD_PROJECTION = {
    "evidence": ("evidence_refs",),
    "target_match": ("target_pointer",),
    "localized": ("target_pointer",),
    "detected": ("verdict",),
    "classified": ("issue_family",),
    "clean_integrity": (
        "verdict",
        "issue_family",
        "severity",
        "target_pointer",
        "status",
        "evidence_refs",
        "action",
    ),
    "clean_semantics": (
        "verdict",
        "issue_family",
        "severity",
        "target_pointer",
        "status",
        "evidence_refs",
        "action",
    ),
    "complete_review_row": tuple(sorted(_CARD_REVIEWER_OUTPUT_FIELDS)),
}


_CARD_REVIEWER_SEMANTIC_CHECK_NAMES = {
    "target_match": "TARGET_POINTER_MATCH",
    "localized": "TARGET_LOCALIZATION",
    "detected": "DEFECT_DETECTION",
    "classified": "ISSUE_FAMILY_CLASSIFICATION",
    "clean_integrity": "CLEAN_CONTROL_INTEGRITY",
    "clean_semantics": "CLEAN_CONTROL_SEMANTICS",
    "complete_review_row": "COMPLETE_REVIEW_ROW",
}


def _project_card_reviewer_failed_fields(
    values: Any,
) -> tuple[list[str], list[str]]:
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(item, str) or not item for item in values)
    ):
        raise ValueError("CARD_REVIEWER_FAILED_OUTPUT_FIELDS_INVALID")
    output_fields: set[str] = set()
    semantic_checks: set[str] = set()
    for field in values:
        if field in _CARD_REVIEWER_OUTPUT_FIELDS:
            output_fields.add(field)
            continue
        projected = _CARD_REVIEWER_SCORER_FIELD_PROJECTION.get(field)
        if projected is None:
            raise ValueError(
                f"CARD_REVIEWER_FAILED_SCORER_FIELD_UNMAPPED:{field}"
            )
        output_fields.update(projected)
        check = _CARD_REVIEWER_SEMANTIC_CHECK_NAMES.get(field)
        if check is not None:
            semantic_checks.add(check)
    if not output_fields:
        raise ValueError("CARD_REVIEWER_FAILED_OUTPUT_FIELDS_EMPTY")
    return sorted(output_fields), sorted(semantic_checks)


def project_card_reviewer_repair_prompt(
    package: Mapping[str, Any],
    *,
    reviewer_system_rubric: str,
) -> dict[str, Any]:
    """Project scorer diagnostics onto provider-safe repair instructions.

    Quality uses ``repair_targets[*].severity`` as scorer-side repair
    priority (major/critical), while each replacement review row also has an
    output field named ``severity`` (none/minor/major/critical).  Supplying or
    reversibly remapping that priority next to
    ``failed_output_fields=["severity"]`` steers the subject toward a local
    answer.  The Desktop transport projection therefore omits the scorer priority
    entirely; the frozen local package and merge contract retain it.  Quality
    also exposed scorer
    component names (for example ``evidence`` and ``target_match``) as if they
    were output fields.  This projection maps those names back to the real
    response-schema fields and keeps semantic checks separately labelled.  The
    original reviewer rubric is rebound for the otherwise stateless repair
    call.  The frozen package and contract remain unchanged and authoritative
    for exact-set merge and local Gold scoring.
    """

    if not isinstance(package, Mapping):
        raise ValueError("CARD_REVIEWER_REPAIR_PACKAGE_REQUIRED")
    if (
        not isinstance(reviewer_system_rubric, str)
        or not reviewer_system_rubric.strip()
        or "Use severity=critical" not in reviewer_system_rubric
        or "severity=none" not in reviewer_system_rubric
    ):
        raise ValueError("CARD_REVIEWER_SYSTEM_RUBRIC_INVALID")
    source_prompt = package.get("prompt")
    contract = package.get("contract")
    package_targets = package.get("repair_targets")
    if (
        not isinstance(source_prompt, str)
        or not isinstance(contract, Mapping)
        or not isinstance(package_targets, Mapping)
    ):
        raise ValueError("CARD_REVIEWER_REPAIR_PACKAGE_INVALID")
    try:
        prompt = json.loads(source_prompt)
    except json.JSONDecodeError as error:
        raise ValueError("CARD_REVIEWER_REPAIR_PROMPT_INVALID") from error
    if not isinstance(prompt, dict):
        raise ValueError("CARD_REVIEWER_REPAIR_PROMPT_INVALID")
    prompt_targets = prompt.get("repair_targets")
    if (
        not isinstance(prompt_targets, Mapping)
        or _sha256(prompt_targets) != _sha256(package_targets)
        or prompt.get("contract_sha256") != contract.get("contract_sha256")
    ):
        raise ValueError("CARD_REVIEWER_REPAIR_PROMPT_BINDING_MISMATCH")

    projected_targets: dict[str, list[dict[str, Any]]] = {}
    omitted_priority_count = 0
    projected_failed_field_count = 0
    projected_semantic_check_count = 0
    for form_id in ("A", "B"):
        targets = prompt_targets.get(form_id)
        if not isinstance(targets, list):
            raise ValueError(
                f"CARD_REVIEWER_REPAIR_TARGETS_INVALID:{form_id}"
            )
        projected_rows: list[dict[str, Any]] = []
        for raw in targets:
            if not isinstance(raw, Mapping):
                raise ValueError(
                    f"CARD_REVIEWER_REPAIR_TARGET_INVALID:{form_id}"
                )
            row = deepcopy(dict(raw))
            priority = row.pop("severity", None)
            if priority not in {"major", "critical"}:
                raise ValueError(
                    f"CARD_REVIEWER_REPAIR_PRIORITY_INVALID:{form_id}"
                )
            omitted_priority_count += 1
            output_fields, semantic_checks = _project_card_reviewer_failed_fields(
                row.get("failed_output_fields")
            )
            row["failed_output_fields"] = output_fields
            if semantic_checks:
                row["failed_semantic_checks"] = semantic_checks
            else:
                row.pop("failed_semantic_checks", None)
            projected_failed_field_count += len(output_fields)
            projected_semantic_check_count += len(semantic_checks)
            projected_rows.append(row)
        projected_targets[form_id] = projected_rows

    instruction = prompt.get("instruction")
    if not isinstance(instruction, str) or not instruction:
        raise ValueError("CARD_REVIEWER_REPAIR_INSTRUCTION_INVALID")
    prompt["instruction"] = (
        instruction
        + " The scorer-side repair priority has been deliberately omitted "
        "from every repair target. No repair-target metadata supplies an "
        "expected output value. Apply the original reviewer rubric "
        "again to source_case and re-derive every failed_output_field. "
        "failed_semantic_checks name local validation concepts, not response "
        "fields and not expected answers. In particular, evidence means the "
        "evidence_refs output field and target_match/localized mean the "
        "target_pointer output field. Re-apply the original rubric to the "
        "source evidence."
    )
    prompt["repair_targets"] = projected_targets
    prompt["subject_metadata_projection"] = {
        "schema_version": CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION,
        "source_field": "repair_targets.*.severity",
        "provider_projection": "OMITTED",
        "omitted_priority_field_count": omitted_priority_count,
        "projected_failed_output_field_count": projected_failed_field_count,
        "projected_semantic_check_count": projected_semantic_check_count,
        "scorer_field_projection": {
            key: list(value)
            for key, value in sorted(
                _CARD_REVIEWER_SCORER_FIELD_PROJECTION.items()
            )
        },
        "reviewer_system_rubric_sha256": hashlib.sha256(
            reviewer_system_rubric.encode("utf-8")
        ).hexdigest().upper(),
        "expected_output_values_disclosed": False,
        "source_package_and_contract_unchanged": True,
    }
    projected_prompt = json.dumps(
        prompt,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "schema_version": CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION,
        "prompt": projected_prompt,
        "source_prompt_sha256": hashlib.sha256(
            source_prompt.encode("utf-8")
        ).hexdigest().upper(),
        "projected_prompt_sha256": hashlib.sha256(
            projected_prompt.encode("utf-8")
        ).hexdigest().upper(),
        "contract_sha256": contract.get("contract_sha256"),
        "source_package_sha256": package.get("package_sha256"),
        "omitted_priority_field_count": omitted_priority_count,
        "projected_failed_output_field_count": projected_failed_field_count,
        "projected_semantic_check_count": projected_semantic_check_count,
        "reviewer_system_rubric_sha256": hashlib.sha256(
            reviewer_system_rubric.encode("utf-8")
        ).hexdigest().upper(),
        "provider_receives_answer_key": False,
    }


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


class QualityCardReviewerExamExecutor:
    """Run the frozen Quality Card Reviewer A/B reference regression.

    The same Gold-isolated protocol is used for API, subscription CLI, and an
    exact-digest loopback local model.  A score produced here is diagnostic
    only and cannot grant model qualification.
    """

    EXECUTOR_REF = (
        "SESSIONS_CARD_REVIEWER_EXECUTOR_R1_2_Quality_R1_1_CORE"
    )
    MODE = "LIVE_Quality_CARD_REVIEWER_REFERENCE_REGRESSION"
    Quality_SEMANTIC_CALL_LIMIT = 5
    API_MAXIMUM_REQUEST_ATTEMPTS = 15
    CLI_MAXIMUM_REQUEST_ATTEMPTS = 10
    LOCAL_MAXIMUM_REQUEST_ATTEMPTS = 10
    CASES_PER_CHUNK = 36
    TRANSIENT_RETRY_LIMIT = 1
    REQUEST_TIMEOUT_SECONDS = 600
    API_BUDGET_CAP_CNY = 6.0
    SEMANTIC_REPAIR_ROUND_LIMIT = 1

    def __init__(
        self,
        *,
        scratch_root: Path,
        quality_package: str,
        reference_pack_root: Path,
        pricing_profile_path: Path | None = None,
        api_request_cap: int | None = None,
        cli_request_cap: int | None = None,
        local_request_cap: int | None = None,
    ) -> None:
        if not isinstance(quality_package, str) or not SAFE_PACKAGE.fullmatch(
            quality_package
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_Quality_PACKAGE_INVALID")
        self.scratch_root = Path(scratch_root).resolve()
        self._quality_package = quality_package
        self._reference_pack_root = Path(reference_pack_root).resolve()
        default_pricing = (
            self._reference_pack_root.parents[2]
            / "card_distiller_exam"
            / "v1"
            / "deepseek_flash_to_pro_profile.public.json"
        )
        self._pricing_profile_path = (
            Path(pricing_profile_path).resolve()
            if pricing_profile_path is not None
            else default_pricing
        )
        self._api_request_cap = self._validated_request_cap(
            api_request_cap,
            maximum=self.API_MAXIMUM_REQUEST_ATTEMPTS,
        )
        self._cli_request_cap = self._validated_request_cap(
            cli_request_cap,
            maximum=self.CLI_MAXIMUM_REQUEST_ATTEMPTS,
        )
        self._local_request_cap = self._validated_request_cap(
            local_request_cap,
            maximum=self.LOCAL_MAXIMUM_REQUEST_ATTEMPTS,
        )
        self._modules: dict[str, Any] | None = None

    @staticmethod
    def _validated_request_cap(value: int | None, *, maximum: int) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("WORKFLOW_MODEL_EXAM_REQUEST_CAP_INVALID")
        return value

    def _request_cap(self, profile_kind: str) -> int:
        explicit = {'API': self._api_request_cap, 'CLI': self._cli_request_cap,
                    'LOCAL': self._local_request_cap}[profile_kind]
        if explicit is not None:
            return explicit  # preserve an explicitly authorized per-run limit
        forms = _public_forms(self._reference_pack_root)
        if hasattr(self, '_shards'):
            shards = sum(len(self._shards(form, form_id=key, artifact=artifact,
                                         profile_kind=profile_kind))
                         for key, form in forms.items()
                         for artifact in ('claim_map', 'analysis_summary'))
            # Existing analysis loop: initial + one structural repair; each may
            # use the existing transient retries. No additional call is added.
            return shards * 2 * (1 + self.TRANSIENT_RETRY_LIMIT)
        shards = sum(math.ceil(len(form['cases']) / (self.CASES_PER_CHUNK or len(form['cases'])))
                     for form in forms.values())
        return shards * self.Quality_SEMANTIC_CALL_LIMIT * (1 + self.TRANSIENT_RETRY_LIMIT)

    def _structured_output_policy(
        self,
        target: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int | None, str]:
        """Legacy executor policy; the successor overrides this per channel."""

        return deepcopy(dict(target)), MAX_OUTPUT_TOKENS, "EXPLICIT"

    def _subject_request_projection(
        self,
        target: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return the exact request visible to the selected subject."""

        del target
        return deepcopy(dict(request))

    def _quality(self) -> dict[str, Any]:
        if self._modules is None:
            names = {
                "protocol": "card_reviewer_exam_protocol",
                "exam": "card_reviewer_exam",
                "pack": "card_reviewer_exam_pack",
                "reference": "reference_exam_pack",
            }
            self._modules = {
                key: importlib.import_module(f"{self._quality_package}.{suffix}")
                for key, suffix in names.items()
            }
        return self._modules

    @staticmethod
    def _quality_asset_failure(stage: str, exc: Exception) -> dict[str, Any]:
        missing_module = getattr(exc, "name", None)
        missing_suffix = ""
        if isinstance(missing_module, str) and re.fullmatch(
            r"[A-Za-z0-9_.-]{1,128}", missing_module
        ):
            missing_suffix = "_MISSING_" + re.sub(
                r"[^A-Za-z0-9]", "_", missing_module
            ).upper()
        return _not_available(
            "WORKFLOW_MODEL_EXAM_Quality_"
            f"{stage}_{type(exc).__name__.upper()}{missing_suffix}"
        )

    def _exam_inputs(
        self,
        *,
        pack: Any,
        schema: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        del context
        return {
            "forms": {
                form_id: _load_json(pack.member(f"forms/form_{form_id}.json"))
                for form_id in ("A", "B")
            },
            "golds": {
                form_id: _load_json(pack.member(f"gold/form_{form_id}_gold.json"))
                for form_id in ("A", "B")
            },
            "schemas": {"A": deepcopy(dict(schema)), "B": deepcopy(dict(schema))},
            "execution_profile": "LEGACY_FULL_DUAL_FORM",
            "sample_metadata": None,
        }

    def _execute_form_protocol(
        self,
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
        del exam
        return protocol.execute_form(
            form=form,
            schema=schema,
            prompt_contract=prompt_contract,
            scoring_protocol=scoring_protocol,
            subject_call=subject_call,
            gold_loader=lambda: deepcopy(dict(gold)),
        )

    def _score_after_semantic_repair(
        self,
        *,
        exam: Any,
        forms: Mapping[str, Mapping[str, Any]],
        golds: Mapping[str, Mapping[str, Any]],
        original_scores: Mapping[str, Mapping[str, Any]],
        merged_responses: Mapping[str, Mapping[str, Any]],
        schema: Mapping[str, Any],
        scoring_protocol: Mapping[str, Any],
        repair_contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        return score_card_reviewer_role_projection(
            exam=exam,
            forms=forms,
            golds=golds,
            original_scores=original_scores,
            merged_responses=merged_responses,
            schema=schema,
            scoring_protocol=scoring_protocol,
            repair_contract=repair_contract,
        )

    def _score_current_responses(
        self,
        *,
        exam: Any,
        forms: Mapping[str, Mapping[str, Any]],
        golds: Mapping[str, Mapping[str, Any]],
        responses: Mapping[str, Mapping[str, Any]],
        schema: Mapping[str, Any],
        scoring_protocol: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        return {
            form_id: dict(
                exam.score_form(
                    form=forms[form_id],
                    final_response=responses[form_id],
                    gold=golds[form_id],
                    schema=schema,
                    scoring_protocol=scoring_protocol,
                    repair_metrics={
                        "form_case_count": len(
                            exam.form_cases(forms[form_id])[1]
                        ),
                        "repair_turns": 0,
                        "directed_repair_case_count": 0,
                        "all_repairs_succeeded": True,
                    },
                )
            )
            for form_id in ("A", "B")
        }

    @staticmethod
    def _wrong_item_count(scores: Mapping[str, Mapping[str, Any]]) -> int:
        return sum(
            1
            for form_id in ("A", "B")
            for detail in scores[form_id].get("details", [])
            if isinstance(detail, Mapping)
            and isinstance(detail.get("score"), (int, float))
            and not isinstance(detail.get("score"), bool)
            and float(detail["score"]) < 100.0
        )

    @staticmethod
    def _round_two_category_burden(
        *,
        exam: Any,
        forms: Mapping[str, Mapping[str, Any]],
        source_scores: Mapping[str, Mapping[str, Any]],
        exact_sets: Mapping[str, list[str]],
        scoring_protocol: Mapping[str, Any],
    ) -> dict[str, Any]:
        directed = scoring_protocol.get("directed_repair")
        if not isinstance(directed, Mapping):
            raise ValueError("CARD_REVIEWER_DIRECTED_REPAIR_SCORING_REQUIRED")
        fixed_points = float(directed.get("fixed_turn_penalty", 0.0))
        volume_knots = directed.get("volume_penalty_knots")
        if not isinstance(volume_knots, list):
            raise ValueError("CARD_REVIEWER_DIRECTED_REPAIR_KNOTS_REQUIRED")
        by_form: dict[str, dict[str, Any]] = {}
        for form_id in ("A", "B"):
            target_ids = list(exact_sets.get(form_id, []))
            details = {
                str(item.get("case_id")): item
                for item in source_scores[form_id].get("details", [])
                if isinstance(item, Mapping)
                and isinstance(item.get("case_id"), str)
            }
            if any(case_id not in details for case_id in target_ids):
                raise ValueError("ROUND2_RESIDUAL_DETAIL_MISSING")
            issue_categories: set[str] = set()
            severity_categories: set[str] = set()
            for case_id in target_ids:
                issues, _fields, severity = exam.reviewer_exam_issue_metadata(
                    details[case_id]
                )
                issue_categories.update(issues)
                severity_categories.add(severity)
            form_case_count = len(exam.form_cases(forms[form_id])[1])
            nonlinear = exam.reviewer_exam_form_penalty(
                form_case_count=form_case_count,
                wrong_case_count=len(target_ids),
                issue_categories=issue_categories,
                severity_categories=severity_categories,
            )
            fixed = fixed_points if target_ids else 0.0
            volume = (
                float(
                    exam.interpolate(
                        volume_knots,
                        len(target_ids) / form_case_count,
                    )
                )
                if target_ids
                else 0.0
            )
            total = float(nonlinear["total"]) + fixed + volume
            by_form[form_id] = {
                "target_case_ids": target_ids,
                "target_case_count": len(target_ids),
                "nonlinear_category_penalty": round(
                    float(nonlinear["total"]), 6
                ),
                "nonlinear_category_penalty_detail": nonlinear,
                "fixed_penalty": round(fixed, 6),
                "volume_penalty": round(volume, 6),
                "total": round(total, 6),
            }
        return {
            "schema_version": "DesktopCardReviewerRound2CategoryBurden-v1",
            "by_form": by_form,
            "nonlinear_category_penalty_mean": round(
                sum(
                    float(row["nonlinear_category_penalty"])
                    for row in by_form.values()
                )
                / 2.0,
                6,
            ),
            "fixed_penalty_mean": round(
                sum(float(row["fixed_penalty"]) for row in by_form.values())
                / 2.0,
                6,
            ),
            "volume_penalty_mean": round(
                sum(float(row["volume_penalty"]) for row in by_form.values())
                / 2.0,
                6,
            ),
            "total_mean": round(
                sum(float(row["total"]) for row in by_form.values()) / 2.0,
                6,
            ),
            "coefficient_source": (
                "Quality_CARD_REVIEWER_EXAM_REPAIR_PENALTY_AND_"
                "DIRECTED_REPAIR_CONTRACTS"
            ),
            "cross_category_coefficients_reused": False,
        }

    @staticmethod
    def _apply_round_two_burden(
        projection: Mapping[str, Any],
        burden: Mapping[str, Any],
    ) -> dict[str, Any]:
        value = deepcopy(dict(projection))
        form_scores = deepcopy(dict(value["repair_outcome_form_scores"]))
        forms = deepcopy(dict(value["forms"]))
        by_form = burden["by_form"]
        for form_id in ("A", "B"):
            adjusted = max(
                0.0,
                float(form_scores[form_id])
                - float(by_form[form_id]["total"]),
            )
            form_scores[form_id] = round(adjusted, 6)
            forms[form_id]["round_two_additional_burden"] = deepcopy(
                dict(by_form[form_id])
            )
            forms[form_id]["reviewer_repair_outcome_form_score"] = round(
                adjusted, 6
            )
        outcome = round(sum(form_scores.values()) / 2.0, 6)
        residual_burden = float(
            value.get("reviewer_repair_burden", {}).get(
                "governed_disposition_burden_total_mean", 0.0
            )
        )
        value["forms"] = forms
        value["repair_outcome_form_scores"] = form_scores
        value["reviewer_repair_outcome_score"] = outcome
        value["core_closure_score"] = round(
            max(0.0, outcome - residual_burden), 6
        )
        value["round_two_additional_burden"] = deepcopy(dict(burden))
        value["reviewer_repair_burden"]["round_two_additional_burden"] = (
            deepcopy(dict(burden))
        )
        value["reviewer_repair_burden"]["burden_order"] = [
            "ROUND1_CATEGORY_NONLINEAR_BURDEN",
            "ROUND1_DELTA_RECHECK",
            "ROUND2_RESIDUAL_CATEGORY_NONLINEAR_PLUS_FIXED_VOLUME_BURDEN",
            "ROUND2_DELTA_RECHECK",
            "GOVERNED_DISPOSITION_IF_NEEDED",
        ]
        value["reviewer_repair_burden"][
            "governed_disposition_is_second_repair"
        ] = False
        return value

    def _execute_semantic_repair_rounds(
        self,
        *,
        run_id: str,
        run_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        exam: Any,
        forms: Mapping[str, Mapping[str, Any]],
        golds: Mapping[str, Mapping[str, Any]],
        form_schemas: Mapping[str, Mapping[str, Any]],
        schema: Mapping[str, Any],
        scoring_protocol: Mapping[str, Any],
        prompt_contract: Mapping[str, Any],
        original_scores: Mapping[str, Mapping[str, Any]],
        original_responses: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        current_scores = {
            key: deepcopy(dict(value))
            for key, value in original_scores.items()
        }
        current_responses = {
            key: deepcopy(dict(value))
            for key, value in original_responses.items()
        }
        initial_contract: Mapping[str, Any] | None = None
        first_round_exact_sets: dict[str, list[str]] | None = None
        final_projection: dict[str, Any] | None = None
        prompt_projections: list[dict[str, Any]] = []
        lifecycle_contracts: list[dict[str, Any]] = []
        round_two_burden: dict[str, Any] | None = None
        unchanged_surface_sha256 = _sha256(
            {
                "forms": forms,
                "form_schemas": form_schemas,
                "scoring_protocol": scoring_protocol,
                "prompt_contract": prompt_contract,
            }
        )
        for round_number in range(1, self.SEMANTIC_REPAIR_ROUND_LIMIT + 1):
            if not self._wrong_item_count(current_scores):
                break
            source_scores = deepcopy(current_scores)
            if round_number == 2:
                assert first_round_exact_sets is not None
                for form_id in ("A", "B"):
                    allowed = set(first_round_exact_sets[form_id])
                    source_scores[form_id]["details"] = [
                        deepcopy(detail)
                        for detail in source_scores[form_id].get("details", [])
                        if isinstance(detail, Mapping)
                        and detail.get("case_id") in allowed
                        and isinstance(detail.get("score"), (int, float))
                        and not isinstance(detail.get("score"), bool)
                        and float(detail["score"]) < 100.0
                    ]
                if not any(
                    source_scores[form_id]["details"]
                    for form_id in ("A", "B")
                ):
                    break
            package = exam.build_exam_directed_repair_package(
                forms=forms,
                original_responses=current_responses,
                original_scores=source_scores,
                schema=schema,
            )
            if initial_contract is None:
                initial_contract = deepcopy(dict(package["contract"]))
            exact_sets = {
                form_id: list(case_ids)
                for form_id, case_ids in package["contract"][
                    "wrong_item_exact_set"
                ].items()
            }
            if first_round_exact_sets is None:
                first_round_exact_sets = deepcopy(exact_sets)
            repair_prompt_projection = project_card_reviewer_repair_prompt(
                package,
                reviewer_system_rubric=str(prompt_contract["system"]),
            )
            projected_prompt_payload = json.loads(
                repair_prompt_projection["prompt"]
            )
            lifecycle_contract = build_repair_round(
                round_number=round_number,
                category_id=CARD_REVIEWER_EXAM_CATEGORY_ID,
                targets_by_form=exact_sets,
                prior_targets_by_form=(
                    first_round_exact_sets
                    if round_number == 2
                    else None
                ),
                prompt_projection=projected_prompt_payload,
                unchanged_surface_sha256=unchanged_surface_sha256,
            )
            if round_number == 2:
                round_two_burden = self._round_two_category_burden(
                    exam=exam,
                    forms=forms,
                    source_scores=source_scores,
                    exact_sets=exact_sets,
                    scoring_protocol=scoring_protocol,
                )
            request = {
                "phase": "EXAM_DIRECTED_REPAIR",
                "semantic_repair_round": round_number,
                "semantic_repair_round_limit": (
                    self.SEMANTIC_REPAIR_ROUND_LIMIT
                ),
                "system": (
                    str(prompt_contract["system"])
                    + "\n\n"
                    + (
                        "Perform directed repair round 1. Replace all and only "
                        "the listed rows, return every listed case exactly once, "
                        "and include no Markdown."
                        if round_number == 1
                        else "Perform directed repair round 2 on the frozen "
                        "round-1 residual exact set only. Do not revisit any "
                        "unlisted row; return every listed case exactly once, "
                        "and include no Markdown."
                    )
                ),
                "user": repair_prompt_projection["prompt"],
                "form": {
                    "forms": [
                        form_id for form_id in ("A", "B") if exact_sets.get(form_id)
                    ],
                    "wrong_item_exact_set": exact_sets,
                },
                "output_schema": package["repair_schema"],
                "repair_contract_sha256": package["contract"][
                    "contract_sha256"
                ],
                "successor_repair_lifecycle_sha256": lifecycle_contract[
                    "contract_sha256"
                ],
                "repair_prompt_projection": {
                    key: value
                    for key, value in repair_prompt_projection.items()
                    if key != "prompt"
                },
            }
            request["request_payload_hash"] = exam.sha256(request)
            repair_response = self._chunked_provider_call(
                run_id=run_id,
                run_root=run_root,
                state=state,
                target=target,
                request=request,
                response_schema=package["repair_schema"],
                structured_chat=structured_chat,
                call_kind=f"WHOLE_EXAM_DIRECTED_REPAIR_ROUND_{round_number}",
            )
            merged = exam.merge_exam_directed_repair(
                forms=forms,
                original_responses=current_responses,
                repair_response=repair_response,
                schema=schema,
                repair_contract=package["contract"],
            )
            current_responses = {
                key: deepcopy(dict(value))
                for key, value in merged["responses"].items()
            }
            assert initial_contract is not None
            final_projection = self._score_after_semantic_repair(
                exam=exam,
                forms=forms,
                golds=golds,
                original_scores=original_scores,
                merged_responses=current_responses,
                schema=schema,
                scoring_protocol=scoring_protocol,
                repair_contract=initial_contract,
            )
            current_scores = self._score_current_responses(
                exam=exam,
                forms=forms,
                golds=golds,
                responses=current_responses,
                schema=schema,
                scoring_protocol=scoring_protocol,
            )
            prompt_projections.append(
                {
                    "semantic_repair_round": round_number,
                    **{
                        key: value
                        for key, value in repair_prompt_projection.items()
                        if key != "prompt"
                    },
                }
            )
            lifecycle_contracts.append(lifecycle_contract)

        if final_projection is None or initial_contract is None:
            raise ValueError("SEMANTIC_REPAIR_CYCLE_DID_NOT_EXECUTE")
        initial_burden = float(
            final_projection["reviewer_repair_burden"][
                "initial_burden_total_mean"
            ]
        )
        penalty_rounds: list[dict[str, Any]] = [
            {
                "round_number": 1,
                "category_id": CARD_REVIEWER_EXAM_CATEGORY_ID,
                "nonlinear_category_penalty": initial_burden,
                "fixed_penalty": 0.0,
                "volume_penalty": 0.0,
            }
        ]
        if round_two_burden is not None:
            penalty_rounds.append(
                {
                    "round_number": 2,
                    "category_id": CARD_REVIEWER_EXAM_CATEGORY_ID,
                    "nonlinear_category_penalty": round_two_burden[
                        "nonlinear_category_penalty_mean"
                    ],
                    "fixed_penalty": round_two_burden["fixed_penalty_mean"],
                    "volume_penalty": round_two_burden["volume_penalty_mean"],
                }
            )
            final_projection = self._apply_round_two_burden(
                final_projection,
                round_two_burden,
            )
        cumulative_penalty = cumulative_category_repair_penalty(
            penalty_rounds
        )
        final_projection["semantic_repair_rounds"] = len(
            lifecycle_contracts
        )
        final_projection["semantic_repair_round_limit"] = (
            self.SEMANTIC_REPAIR_ROUND_LIMIT
        )
        final_projection["second_semantic_repair_allowed"] = (
            self.SEMANTIC_REPAIR_ROUND_LIMIT >= 2
        )
        final_projection["semantic_repair_lifecycle"] = lifecycle_contracts
        final_projection["cumulative_category_repair_penalty"] = (
            cumulative_penalty
        )
        final_projection["transport_retry_is_semantic_repair"] = False
        return {
            "projection": final_projection,
            "responses": current_responses,
            "repair_rounds": len(lifecycle_contracts),
            "repair_prompt_projection": prompt_projections[-1],
            "repair_prompt_projections": prompt_projections,
            "semantic_repair_lifecycle": lifecycle_contracts,
            "cumulative_category_repair_penalty": cumulative_penalty,
        }

    def _flash_pricing_binding(self, target: Mapping[str, Any] | None = None) -> dict[str, Any]:
        profile = _load_json(self._pricing_profile_path)
        pro = target is not None and target.get("tier") == "pro"
        low = profile.get("high_cost" if pro else "low_cost")
        if (
            profile.get("schema_version")
            != "model_evaluation-model_evaluation-card-distiller-flash-to-pro-public-profile-v1"
            or not isinstance(low, Mapping)
            or low.get("model_id") != ("deepseek-v4-pro" if pro else "deepseek-v4-flash")
            or float(low.get("budget_cap_cny", -1)) <= 0
            or (not pro and low.get("request_max_output_tokens") != MAX_OUTPUT_TOKENS)
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_PRICING_PROFILE_INVALID")
        return {
            "snapshot_date": profile.get("profile_snapshot_date"),
            "sha256": _file_sha256(self._pricing_profile_path),
            "budget_cap_cny": float(low["budget_cap_cny"]),
        }

    @staticmethod
    def _profile_kind(target: Mapping[str, Any]) -> str | None:
        from memorive.models.cli.templates import is_custom,exam_eligible
        if is_custom(target):
            return "CLI" if exam_eligible(target) else None
        kind = target.get("kind")
        if kind == "API":
            if (
                re.fullmatch(
                    r"deepseek(?:apikey[a-z0-9]*)?",
                    _compact(target.get("provider")),
                )
                and (target.get("model_name"), target.get("tier")) in {("deepseek-v4-flash", "flash"), ("deepseek-flash", "flash"), ("deepseek-v4-pro", "pro"), ("deepseek-pro", "pro")}
                and target.get("thinking") is True
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "API"
            return None
        if kind == "CLI":
            if (
                _compact(target.get("adapter_id")) == "codexcli"
                and target.get("model_name") == "gpt-5.6-sol"
                and target.get("thinking_mode") == "high"
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "CLI"
        if kind == "LOCAL":
            if (
                target.get("endpoint_kind") == "ollama"
                and target.get("structured_chat_adapter")
                == "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1"
                and target.get("execution_eligible") is True
                and target.get("exact_identity_available") is True
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("model_name"), str)
                and bool(target.get("model_name"))
                and isinstance(target.get("model_digest"), str)
                and bool(re.fullmatch(r"[A-F0-9]{64}", target["model_digest"]))
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "LOCAL"
        return None

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        node_id = node.get("node_id")
        if node_id != "transport_review":
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        profile_kind = self._profile_kind(target)
        if profile_kind is None:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        try:
            modules = self._quality()
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            return self._quality_asset_failure("MODULE_IMPORT_FAILED", exc)
        try:
            verification = modules["pack"].verify_reference_pack(
                self._reference_pack_root
            )
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            return self._quality_asset_failure("PACK_VERIFY_FAILED", exc)
        try:
            pack = modules["reference"].load_card_reviewer_reference_exam_pack(
                self._reference_pack_root
            )
            manifest = dict(pack.manifest)
            scoring_protocol_path = pack.member(
                "scoring/scoring_protocol.json"
            )
            scoring_protocol = _load_json(scoring_protocol_path)
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            return self._quality_asset_failure("PACK_LOAD_FAILED", exc)
        try:
            pricing = (
                self._flash_pricing_binding(target)
                if profile_kind == "API"
                else None
            )
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            return self._quality_asset_failure("PRICING_LOAD_FAILED", exc)
        if (
            verification.get("status") != "PASS"
            or verification.get("reference_regression_only") is not True
            or verification.get("qualification_eligible") is not False
            or manifest.get("classification") != "REFERENCE_EXAM_PACK"
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_PACK_NOT_ELIGIBLE")
        comparison_metadata = card_reviewer_comparison_metadata(
            reference_pack_id=str(manifest.get("pack_id") or ""),
            reference_pack_revision=str(
                manifest.get("pack_revision") or ""
            ),
            reference_pack_sha256=str(verification["pack_fingerprint"]),
            scoring_protocol_revision=str(
                scoring_protocol.get("schema_version") or ""
            ),
            scoring_protocol_sha256=_file_sha256(scoring_protocol_path),
            executor_ref=self.EXECUTOR_REF,
        )
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA,
            "status": "READY",
            "mode": self.MODE,
            "reason": "Quality_REFERENCE_REGRESSION_READY",
            "node_id": node_id,
            "executor_ref": self.EXECUTOR_REF,
            "profile_kind": profile_kind,
            "profile_ref": target["config_id"],
            "model_name": target["model_name"],
            "thinking_mode": target.get("thinking_mode"),
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": manifest.get("pack_id"),
            "reference_pack_revision": manifest.get("pack_revision"),
            "reference_pack_sha256": verification["pack_fingerprint"],
            "comparison": comparison_metadata,
            **comparison_metadata,
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "forms": ["A", "B"],
            "maximum_model_calls": self._request_cap(profile_kind),
            "quality_semantic_call_limit": self.Quality_SEMANTIC_CALL_LIMIT,
            "case_chunk_limit": self.CASES_PER_CHUNK,
            "transient_retry_limit_per_chunk": self.TRANSIENT_RETRY_LIMIT,
            "budget_cap_cny": (
                pricing["budget_cap_cny"] if pricing is not None else None
            ),
            "cost_semantics": (
                "FROZEN_PROFILE_ESTIMATE_CAP"
                if profile_kind == "API"
                else (
                    ("CLI_MANAGED_UNKNOWN" if target.get("adapter_id")=="command_template" else "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE")
                    if profile_kind == "CLI"
                    else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
                )
            ),
            "model_digest": target.get("model_digest"),
            "pricing_snapshot_date": (
                pricing["snapshot_date"] if pricing is not None else None
            ),
            "pricing_profile_sha256": (
                pricing["sha256"] if pricing is not None else None
            ),
            "authorization_schema_version": AUTHORIZATION_SCHEMA,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }
        plan["plan_sha256"] = _sha256(plan)
        return plan

    @staticmethod
    def _authorization_reason(
        authorization: Mapping[str, Any] | None,
        plan: Mapping[str, Any],
    ) -> str | None:
        if not isinstance(authorization, Mapping):
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_REQUIRED"
        if set(authorization) != {
            "schema_version",
            "authorized",
            "plan_sha256",
            "cost_cap_cny",
            "acknowledged_subscription_no_per_call_price",
        }:
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_INVALID"
        if (
            authorization.get("schema_version") != AUTHORIZATION_SCHEMA
            or authorization.get("authorized") is not True
        ):
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_REQUIRED"
        if authorization.get("plan_sha256") != plan.get("plan_sha256"):
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_MISMATCH"
        if authorization.get("cost_cap_cny") != plan.get("budget_cap_cny"):
            return "WORKFLOW_MODEL_EXAM_COST_CAP_MISMATCH"
        expected_ack = plan.get("profile_kind") == "CLI"
        if (
            authorization.get("acknowledged_subscription_no_per_call_price")
            is not expected_ack
        ):
            return "WORKFLOW_MODEL_EXAM_COST_SEMANTICS_ACKNOWLEDGEMENT_REQUIRED"
        return None

    @staticmethod
    def _validate_call_result(
        result: Mapping[str, Any],
        *,
        requested_model: str,
        profile_kind: str,
    ) -> Mapping[str, Any]:
        is_api = profile_kind == "API"
        is_cli = profile_kind == "CLI"
        expected = {
            "provider_calls": 1 if is_api else 0,
            "external_network_calls": 1 if is_api else 0,
            "external_process_launches": 1 if is_cli else 0,
        }
        if (
            result.get("schema_version")
            != "SettingsStructuredChatRunnerResult-v1"
            or result.get("status") != "PASS"
            or result.get("requested_model") != requested_model
            or not matches_result_model(result, requested_model)
            or not isinstance(result.get("response"), Mapping)
            or result.get("external_model_calls") != 1
            or any(result.get(key) != value for key, value in expected.items())
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CALL_RESULT_INVALID")
        receipt = result.get("execution_receipt")
        if not isinstance(receipt, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_CALL_RECEIPT_MISSING")
        if (
            receipt.get("status") != "PASS"
            or receipt.get("profile_kind") != profile_kind
            or receipt.get("purpose") != "workflow_model_exam"
            or receipt.get("requested_model") != requested_model
            or not matches_result_model(receipt, requested_model)
            or any(
                not isinstance(receipt.get(field), str) or not receipt.get(field)
                for field in ("route", "region", "egress", "behavior_sha256")
            )
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CALL_RECEIPT_INVALID")
        from memorive.models.cli.templates import receipt_model_bound
        if is_cli and receipt_model_bound(receipt,requested_model):
            if receipt.get("actual_cost") is not None or receipt.get("estimated_cost_cny") is not None:
                raise ValueError("WORKFLOW_MODEL_EXAM_CLI_COST_EVIDENCE_INVALID")
            return receipt
        usage = receipt.get("token_usage")
        if not isinstance(usage, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_TOKEN_EVIDENCE_MISSING")
        for field in ("prompt_tokens", "completion_tokens"):
            value = usage.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("WORKFLOW_MODEL_EXAM_TOKEN_EVIDENCE_MISSING")
        if is_api:
            actual = receipt.get("actual_cost")
            estimate = receipt.get("estimated_cost_cny")
            if actual is None and (
                isinstance(estimate, bool) or not isinstance(estimate, (int, float))
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_COST_EVIDENCE_MISSING")
        elif is_cli and (
            receipt.get("actual_cost") is not None
            or receipt.get("estimated_cost_cny") is not None
            or receipt.get("cost_evidence")
            != "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE"
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CLI_COST_EVIDENCE_INVALID")
        elif not is_api and not is_cli and (
            receipt.get("actual_cost") is not None
            or receipt.get("estimated_cost_cny") != 0.0
            or receipt.get("cost_evidence")
            != "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_LOCAL_COST_EVIDENCE_INVALID")
        return receipt

    def _provider_call(
        self,
        *,
        run_id: str,
        run_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        request: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        call_kind: str,
    ) -> dict[str, Any]:
        profile_kind = str(target["kind"])
        if state["request_attempts"] >= self._request_cap(profile_kind):
            raise ValueError("WORKFLOW_MODEL_EXAM_CALL_LIMIT_REACHED")
        call_number = state["request_attempts"] + 1
        call_id = f"call-{call_number:02d}"
        model_name = str(target["model_name"])
        subject_request = self._subject_request_projection(target, request)
        prompt = json.dumps(
            subject_request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest().upper()
        prompt_metrics = _effective_structured_prompt_metrics(
            prompt,
            response_schema,
        )
        model_binding, request_max_output_tokens, output_limit_mode = (
            self._structured_output_policy(target)
        )
        is_api = profile_kind == "API"
        is_local = profile_kind == "LOCAL"
        limits = resolve_call_limits(profile_kind=profile_kind, service=target, model=target)
        if output_limit_mode == "PROVIDER_DEFAULT":
            limits["max_output_tokens"] = None
            limits["wire_output_policy"] = "PROVIDER_DEFAULT"
        worst_case_reserve_cny = (
            _api_worst_case_request_reserve_cny(
                target, prompt_metrics, request_max_output_tokens
            )
            if is_api
            else None
        )
        if worst_case_reserve_cny is not None:
            cost_cap = target.get("exam_budget_cap_cny")
            if (
                isinstance(cost_cap, bool)
                or not isinstance(cost_cap, (int, float))
                or float(cost_cap)
                < float(state["estimated_cost_cny"]) + worst_case_reserve_cny
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_PER_CALL_WORST_CASE_RESERVE_NOT_COVERED"
                )
        claim = {
            "schema_version": "WorkflowModelExamPreSendClaim-v1",
            "run_id": run_id,
            "call_id": call_id,
            "call_kind": call_kind,
            "profile_kind": profile_kind,
            "profile_ref": target["config_id"],
            "requested_model": model_name,
            "route": (
                "EXISTING_SETTINGS_API_TRANSPORT"
                if is_api
                else ("OLLAMA_LOOPBACK" if is_local else "DECLARATIVE_COMMAND_TEMPLATE" if target.get("adapter_id")=="command_template" else "CODEX_CLI_SUBSCRIPTION")
            ),
            "region": (
                "PROVIDER_MANAGED_UNDISCLOSED"
                if is_api
                else ("LOCAL_MACHINE" if is_local else "PROVIDER_MANAGED_UNDISCLOSED" if target.get("adapter_id")=="command_template" else "OPENAI_MANAGED_UNDISCLOSED")
            ),
            "egress": (
                "PROVIDER_API"
                if is_api
                else ("LOOPBACK_ONLY" if is_local else "CLI_PROVIDER_MANAGED" if target.get("adapter_id")=="command_template" else "CODEX_CLI")
            ),
            "behavior_sha256": prompt_sha256,
            "response_schema_sha256": _sha256(response_schema),
            "max_output_tokens": limits['max_output_tokens'],
            "timeout_seconds": limits['timeout_seconds'],
            "resource_limits": limits,
            "max_output_tokens_mode": output_limit_mode,
            "request_max_output_tokens": limits["max_output_tokens"],
            "effective_prompt_metrics": prompt_metrics,
            "pricing_profile_sha256": (
                (
                    target.get("pricing_profile_sha256")
                    or _file_sha256(self._pricing_profile_path)
                )
                if is_api
                else None
            ),
            "estimated_cost_cny_before_send": (
                round(float(state["estimated_cost_cny"]), 9)
                if is_api
                else None
            ),
            "per_call_worst_case_reserve_cny": worst_case_reserve_cny,
            "cost_cap_cny": target.get("exam_budget_cap_cny") if is_api else None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        claim_receipt = _write_json_create_only(
            run_root / "claims" / f"{call_id}.json", claim
        )
        state["request_attempts"] += 1
        result = dict(
            structured_chat(
                profile_kind=profile_kind,
                service=target,
                model=model_binding,
                prompt=prompt,
                response_schema=response_schema,
                purpose="workflow_model_exam",
                max_output_tokens=limits['max_output_tokens'],
                timeout_seconds=limits['timeout_seconds'],
            )
        )
        if result.get("status") != "PASS":
            receipt = result.get("execution_receipt")
            if not isinstance(receipt, Mapping):
                receipt = {
                    "schema_version": "SettingsStructuredChatExecutionReceipt-v2",
                    "status": "FAILED",
                    "profile_kind": profile_kind,
                    "purpose": "workflow_model_exam",
                    "requested_model": model_name,
                    "returned_model": result.get("returned_model"),
                    "token_usage": None,
                    "actual_cost": None,
                    "estimated_cost_cny": None,
                    "cost_evidence": "ATTEMPT_EVIDENCE_UNAVAILABLE",
                }
            _write_json_create_only(
                run_root / "receipts" / f"{call_id}.json",
                {
                    **deepcopy(dict(receipt)),
                    "run_id": run_id,
                    "call_id": call_id,
                    "call_kind": call_kind,
                    "profile_ref": target["config_id"],
                    "pre_send_claim_sha256": claim_receipt["sha256"],
                    "provider_output_sha256": None,
                },
            )
            for field in (
                "provider_calls",
                "external_model_calls",
                "external_network_calls",
                "external_process_launches",
            ):
                state[field] += int(result.get(field) or 0)
            estimate = receipt.get("estimated_cost_cny")
            if isinstance(estimate, (int, float)) and not isinstance(estimate, bool):
                state["estimated_cost_cny"] += float(estimate)
            usage = receipt.get("token_usage")
            if isinstance(usage, Mapping):
                for field in ("prompt_tokens", "completion_tokens"):
                    value = usage.get(field)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        state["token_usage"][field] = state["token_usage"][field] + value if state["token_usage"][field] is not None else None
            reason = result.get("reason")
            raise ValueError(
                reason if isinstance(reason, str) and reason else "WORKFLOW_MODEL_EXAM_CALL_FAILED"
            )
        receipt = self._validate_call_result(
            result,
            requested_model=model_name,
            profile_kind=profile_kind,
        )
        response = deepcopy(dict(result["response"]))
        output_receipt = _write_json_create_only(
            run_root / "provider_outputs" / f"{call_id}.json", response
        )
        _write_json_create_only(
            run_root / "receipts" / f"{call_id}.json",
            {
                **deepcopy(dict(receipt)),
                "run_id": run_id,
                "call_id": call_id,
                "call_kind": call_kind,
                "profile_ref": target["config_id"],
                "pre_send_claim_sha256": claim_receipt["sha256"],
                "provider_output_sha256": output_receipt["sha256"],
            },
        )
        for field in (
            "provider_calls",
            "external_model_calls",
            "external_network_calls",
            "external_process_launches",
        ):
            state[field] += int(result[field])
        estimate = receipt.get("estimated_cost_cny")
        if isinstance(estimate, (int, float)) and not isinstance(estimate, bool):
            state["estimated_cost_cny"] += float(estimate)
        from memorive.models.cli.templates import accumulate_usage
        accumulate_usage(state,receipt.get("token_usage"))
        state["returned_model"]=receipt.get("returned_model")
        return response

    def _stable_provider_call(
        self,
        *,
        run_id: str,
        run_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        request: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        call_kind: str,
    ) -> dict[str, Any]:
        retryable = {
            "API_NETWORK_OR_RESPONSE_FAILURE",
            "API_RATE_LIMITED",
            "API_STRUCTURED_CHAT_REQUEST_FAILED",
            "STRUCTURED_CHAT_RESPONSE_EMPTY",
            "STRUCTURED_CHAT_RESPONSE_INVALID",
            "CLI_STRUCTURED_CHAT_TIMEOUT",
        }
        last_error: ValueError | None = None
        for retry_index in range(self.TRANSIENT_RETRY_LIMIT + 1):
            effective_kind = (
                call_kind
                if retry_index == 0
                else f"{call_kind}_TRANSPORT_RETRY_{retry_index}"
            )
            try:
                return self._provider_call(
                    run_id=run_id,
                    run_root=run_root,
                    state=state,
                    target=target,
                    request=request,
                    response_schema=response_schema,
                    structured_chat=structured_chat,
                    call_kind=effective_kind,
                )
            except ValueError as error:
                last_error = error
                if str(error) not in retryable:
                    raise
                if state["request_attempts"] >= self._request_cap(str(target["kind"])):
                    raise
        if last_error is not None:
            raise last_error
        raise ValueError("WORKFLOW_MODEL_EXAM_CALL_FAILED")

    def _chunked_provider_call(
        self,
        *,
        run_id: str,
        run_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        request: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        call_kind: str,
    ) -> dict[str, Any]:
        form = request.get("form")
        cases = form.get("cases") if isinstance(form, Mapping) else None
        if not isinstance(cases, list) or len(cases) <= self.CASES_PER_CHUNK:
            return self._stable_provider_call(
                run_id=run_id,
                run_root=run_root,
                state=state,
                target=target,
                request=request,
                response_schema=response_schema,
                structured_chat=structured_chat,
                call_kind=call_kind,
            )
        chunks = [
            cases[index : index + self.CASES_PER_CHUNK]
            for index in range(0, len(cases), self.CASES_PER_CHUNK)
        ]
        merged_reviews: list[Any] = []
        parent_hash = str(request.get("request_payload_hash") or _sha256(request))
        for chunk_index, chunk in enumerate(chunks, start=1):
            chunk_request = deepcopy(dict(request))
            chunk_form = deepcopy(dict(form))
            chunk_form["cases"] = deepcopy(chunk)
            case_ids = [
                row.get("case_id")
                for row in chunk
                if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)
            ]
            if len(case_ids) != len(chunk) or len(set(case_ids)) != len(case_ids):
                raise ValueError("WORKFLOW_MODEL_EXAM_CHUNK_CASE_SET_INVALID")
            chunk_request["form"] = chunk_form
            chunk_request["chunk_contract"] = {
                "schema_version": "WorkflowModelExamChunkContract-v1",
                "parent_request_payload_hash": parent_hash,
                "chunk_index": chunk_index,
                "chunk_count": len(chunks),
                "case_ids": case_ids,
                "case_ids_sha256": _sha256(case_ids),
            }
            chunk_request.pop("request_payload_hash", None)
            chunk_request["request_payload_hash"] = _sha256(chunk_request)
            response = self._stable_provider_call(
                run_id=run_id,
                run_root=run_root,
                state=state,
                target=target,
                request=chunk_request,
                response_schema=response_schema,
                structured_chat=structured_chat,
                call_kind=f"{call_kind}_CHUNK_{chunk_index}_OF_{len(chunks)}",
            )
            reviews = response.get("reviews")
            if isinstance(reviews, list):
                merged_reviews.extend(deepcopy(reviews))
        return {"reviews": merged_reviews}

    def execute(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
        *,
        authorization: Mapping[str, Any] | None,
        structured_chat: Callable[..., Mapping[str, Any]],
    ) -> dict[str, Any]:
        started = monotonic()
        plan = self.plan(node, target, context)
        requested_model = (
            str(target.get("model_name"))
            if isinstance(target.get("model_name"), str)
            else None
        )
        if plan.get("status") != "READY":
            return _not_run(
                str(plan.get("reason") or "WORKFLOW_MODEL_EXAM_NOT_AVAILABLE"),
                requested_model=requested_model,
            )
        authorization_reason = self._authorization_reason(authorization, plan)
        if authorization_reason is not None:
            return _not_run(authorization_reason, requested_model=requested_model)

        run_id = "workflow-exam-" + uuid.uuid4().hex
        run_root = self.scratch_root / "workflow_exams" / run_id
        run_root.mkdir(parents=True, exist_ok=False)
        _write_json_create_only(run_root / "plan.json", plan)
        _write_json_create_only(
            run_root / "authorization_receipt.json",
            {
                **deepcopy(dict(authorization or {})),
                "run_id": run_id,
                "accepted": True,
                "accepted_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        state = {
            "request_attempts": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_network_calls": 0,
            "external_process_launches": 0,
            "estimated_cost_cny": 0.0,
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
        try:
            modules = self._quality()
            protocol = modules["protocol"]
            exam = modules["exam"]
            pack = modules["reference"].load_card_reviewer_reference_exam_pack(
                self._reference_pack_root
            )
            schema = _load_json(
                pack.member("schemas/card_reviewer_output.schema.json")
            )
            prompt_contract = _load_json(
                pack.member("prompts/card_reviewer_prompt.json")
            )
            scoring_protocol = _load_json(
                pack.member("scoring/scoring_protocol.json")
            )
            planned_comparison = plan.get("comparison")
            comparison_metadata = (
                deepcopy(dict(planned_comparison))
                if isinstance(planned_comparison, Mapping)
                else card_reviewer_comparison_metadata(
                    reference_pack_id=str(plan["reference_pack_id"]),
                    reference_pack_revision=str(
                        plan["reference_pack_revision"]
                    ),
                    reference_pack_sha256=str(plan["reference_pack_sha256"]),
                    scoring_protocol_revision=str(
                        scoring_protocol["schema_version"]
                    ),
                    scoring_protocol_sha256=_file_sha256(
                        pack.member("scoring/scoring_protocol.json")
                    ),
                    executor_ref=self.EXECUTOR_REF,
                )
            )
            exam_inputs = self._exam_inputs(
                pack=pack,
                schema=schema,
                context=context,
            )
            forms = exam_inputs["forms"]
            golds = exam_inputs["golds"]
            form_schemas = exam_inputs["schemas"]
            scores: dict[str, dict[str, Any]] = {}
            responses: dict[str, dict[str, Any]] = {}
            for form_id in ("A", "B"):
                call_index = {"value": 0}

                def subject_call(
                    request: Mapping[str, Any],
                    *,
                    current_form: str = form_id,
                    index: dict[str, int] = call_index,
                ) -> dict[str, Any]:
                    index["value"] += 1
                    response_schema = request.get("output_schema")
                    if not isinstance(response_schema, Mapping):
                        raise ValueError("WORKFLOW_MODEL_EXAM_RESPONSE_SCHEMA_MISSING")
                    response = self._chunked_provider_call(
                        run_id=run_id,
                        run_root=run_root,
                        state=state,
                        target=target,
                        request=request,
                        response_schema=response_schema,
                        structured_chat=structured_chat,
                        call_kind=(
                            f"FORM_{current_form}_INITIAL"
                            if index["value"] == 1
                            else f"FORM_{current_form}_STRUCTURAL_REPAIR"
                        ),
                    )
                    return {"status": "COMPLETED", "response": response}

                result = self._execute_form_protocol(
                    protocol=protocol,
                    exam=exam,
                    form=forms[form_id],
                    schema=form_schemas[form_id],
                    prompt_contract=prompt_contract,
                    scoring_protocol=scoring_protocol,
                    subject_call=subject_call,
                    gold=golds[form_id],
                )
                scores[form_id] = deepcopy(dict(result))
                final_response = result.get("final_response")
                if isinstance(final_response, Mapping):
                    responses[form_id] = deepcopy(dict(final_response))

            initial_form_scores = {
                form_id: (
                    round(float(scores[form_id]["score"]), 6)
                    if isinstance(scores[form_id].get("score"), (int, float))
                    and not isinstance(scores[form_id].get("score"), bool)
                    else None
                )
                for form_id in ("A", "B")
            }
            pointer_normalization_actions = [
                {
                    "form_id": form_id,
                    **deepcopy(dict(action)),
                }
                for form_id in ("A", "B")
                for action in scores[form_id].get(
                    "pointer_normalizations", []
                )
                if isinstance(action, Mapping)
            ]
            repair_projection: dict[str, Any] | None = None
            repair_prompt_projection: dict[str, Any] | None = None
            qualification_verdict = "DISQUALIFIED"
            operational_eligibility = "NOT_RECOMMENDED"
            operational_safety_gate = "FAIL"
            safety_blocking_residual_count: int | None = None
            calibration_residual_count: int | None = None
            scorecard: dict[str, Any] | None = None
            comparison_projection = deepcopy(comparison_metadata)

            if not all(scores[form_id].get("status") == "SCORED" for form_id in ("A", "B")):
                raise ValueError(
                    "CARD_REVIEWER_STRUCTURAL_SCORING_NOT_SCOREABLE"
                )
                score = 0.0
                score_semantics = "STRUCTURAL_CONTRACT_FAILURE_ZERO"
                verdict = "FAIL"
                failed_exam_item_count = sum(
                    len(exam.form_cases(forms[form_id])[1])
                    for form_id in ("A", "B")
                )
                role_ability_form_scores = {"A": 0.0, "B": 0.0}
                repair_outcome_form_scores = {"A": 0.0, "B": 0.0}
                repair_outcome_score = 0.0
                repair_completion_rate = 0.0
                hard_gates = {"A": "FAIL", "B": "FAIL"}
                repair_rounds = 0
                comparison_projection["horizontal_comparison_eligible"] = False
                comparison_projection["noncomparison_reason"] = (
                    "STRUCTURAL_SCORING_FAILURE"
                )
                repair_projection = {
                    "schema_version": "DesktopCardReviewerRoleProjection-v5",
                    "status": "STRUCTURAL_SCORING_FAILURE",
                    "qualification_verdict": qualification_verdict,
                    "quality_exact_repair_qualification_verdict": (
                        qualification_verdict
                    ),
                    "operational_eligibility_verdict": (
                        operational_eligibility
                    ),
                    "operational_safety_gate": operational_safety_gate,
                    "operational_policy_revision": (
                        CARD_REVIEWER_OPERATIONAL_POLICY_REVISION
                    ),
                    "core_lifecycle_revision": (
                        CORE_CARD_REVIEWER_LIFECYCLE_REVISION
                    ),
                    "core_closure_verdict": "HUMAN_REQUIRED",
                    "core_closure_score": 0.0,
                    "semantic_repair_rounds": 0,
                    "second_semantic_repair_allowed": (
                        self.SEMANTIC_REPAIR_ROUND_LIMIT >= 2
                    ),
                    "role_ability_score": 0.0,
                    "score_semantics": score_semantics,
                    "role_ability_form_scores": deepcopy(
                        role_ability_form_scores
                    ),
                    "reviewer_repair_outcome_score": 0.0,
                    "repair_completion_rate": 0.0,
                    "repair_completion_rate_semantics": (
                        "ORIGINAL_WRONG_CASE_REACHES_EXACT_SCORE_100_"
                        "WITH_NEW_WRONGS_COUNTED"
                    ),
                    "exact_case_repair_rate": 0.0,
                    "case_score_deficit_recovery_rate": 0.0,
                    "repair_metadata_collision_suspect_count": 0,
                    "collision_adjusted_repair_diagnostic_rate": 0.0,
                    "collision_adjusted_repair_diagnostic_only": True,
                    "repair_outcome_form_scores": deepcopy(
                        repair_outcome_form_scores
                    ),
                    "structural_contract_failure_final_score": 0.0,
                    "numeric_score_is_not_qualification_verdict": True,
                    "cross_category_coefficients_reused": False,
                }
            else:
                wrong_count = sum(
                    1
                    for form_id in ("A", "B")
                    for detail in scores[form_id].get("details", [])
                    if isinstance(detail, Mapping)
                    and isinstance(detail.get("score"), (int, float))
                    and not isinstance(detail.get("score"), bool)
                    and float(detail["score"]) < 100.0
                )
                repair_rounds = 0
                if wrong_count:
                    repair_cycle = self._execute_semantic_repair_rounds(
                        run_id=run_id,
                        run_root=run_root,
                        state=state,
                        target=target,
                        structured_chat=structured_chat,
                        exam=exam,
                        forms=forms,
                        golds=golds,
                        form_schemas=form_schemas,
                        schema=schema,
                        scoring_protocol=scoring_protocol,
                        prompt_contract=prompt_contract,
                        original_scores=scores,
                        original_responses=responses,
                    )
                    repaired = repair_cycle["projection"]
                    repair_rounds = int(repair_cycle["repair_rounds"])
                    repair_projection = deepcopy(dict(repaired))
                    repair_prompt_projection = deepcopy(
                        dict(repair_cycle["repair_prompt_projection"])
                    )
                    responses = {
                        key: deepcopy(dict(value))
                        for key, value in repair_cycle["responses"].items()
                    }
                    score = float(repaired["role_ability_score"])
                    score_semantics = str(repaired["score_semantics"])
                    repair_outcome_score = float(
                        repaired["reviewer_repair_outcome_score"]
                    )
                    repair_completion_rate = float(
                        repaired["repair_completion_rate"]
                    )
                    failed_exam_item_count = int(
                        repaired["failed_exam_item_count"]
                    )
                    role_ability_form_scores = dict(
                        repaired["role_ability_form_scores"]
                    )
                    repair_outcome_form_scores = dict(
                        repaired["repair_outcome_form_scores"]
                    )
                    hard_gates = {
                        form_id: repaired["forms"][form_id][
                            "hard_gate_verdict"
                        ]
                        for form_id in ("A", "B")
                    }
                    qualification_verdict = str(
                        repaired["qualification_verdict"]
                    )
                    operational_eligibility = str(
                        repaired["operational_eligibility_verdict"]
                    )
                    operational_safety_gate = str(
                        repaired["operational_safety_gate"]
                    )
                    safety_blocking_residual_count = int(
                        repaired["safety_blocking_residual_count"]
                    )
                    calibration_residual_count = int(
                        repaired["calibration_residual_count"]
                    )
                else:
                    score = (
                        float(scores["A"]["score"])
                        + float(scores["B"]["score"])
                    ) / 2.0
                    score_semantics = CARD_REVIEWER_SCORE_SEMANTICS
                    repair_outcome_score = score
                    repair_completion_rate = 1.0
                    failed_exam_item_count = 0
                    role_ability_form_scores = {
                        form_id: float(scores[form_id]["score"])
                        for form_id in ("A", "B")
                    }
                    repair_outcome_form_scores = deepcopy(
                        role_ability_form_scores
                    )
                    hard_gates = {
                        form_id: str(scores[form_id]["hard_gate_verdict"])
                        for form_id in ("A", "B")
                    }
                    qualification_verdict = (
                        "PASS"
                        if all(value == "PASS" for value in hard_gates.values())
                        else "DISQUALIFIED"
                    )
                    operational_safety_gate = (
                        "PASS"
                        if qualification_verdict == "PASS"
                        else "FAIL"
                    )
                    operational_eligibility = (
                        "SUITABLE"
                        if operational_safety_gate == "PASS"
                        and score >= CARD_REVIEWER_OPERATIONAL_PASSING_SCORE
                        else (
                            "REJECT"
                            if score < 60.0
                            else "NOT_RECOMMENDED"
                        )
                    )
                    safety_blocking_residual_count = 0
                    calibration_residual_count = 0
                    repair_projection = {
                        "schema_version": "DesktopCardReviewerRoleProjection-v5",
                        "status": "DIRECTED_REPAIR_NOT_REQUIRED",
                        "qualification_verdict": qualification_verdict,
                        "quality_exact_repair_qualification_verdict": (
                            qualification_verdict
                        ),
                        "operational_eligibility_verdict": (
                            operational_eligibility
                        ),
                        "operational_safety_gate": operational_safety_gate,
                        "operational_policy_revision": (
                            CARD_REVIEWER_OPERATIONAL_POLICY_REVISION
                        ),
                        "core_lifecycle_revision": (
                            CORE_CARD_REVIEWER_LIFECYCLE_REVISION
                        ),
                        "core_closure_verdict": "DELTA_PASS",
                        "core_closure_score": round(score, 6),
                        "semantic_repair_rounds": 0,
                        "second_semantic_repair_allowed": (
                            self.SEMANTIC_REPAIR_ROUND_LIMIT >= 2
                        ),
                        "governed_disposition_case_count": 0,
                        "operational_passing_score": (
                            CARD_REVIEWER_OPERATIONAL_PASSING_SCORE
                        ),
                        "safety_blocking_residual_count": 0,
                        "safety_blocking_case_ids": [],
                        "calibration_residual_count": 0,
                        "calibration_case_ids": [],
                        "operational_residual_diagnostics": {"A": [], "B": []},
                        "role_ability_score": round(score, 6),
                        "score_semantics": score_semantics,
                        "initial_nonlinear_score": round(score, 6),
                        "post_repair_nonlinear_score": round(score, 6),
                        "reviewer_repair_outcome_score": round(score, 6),
                        "repair_completion_rate": 1.0,
                        "repair_completion_rate_semantics": (
                            "ORIGINAL_WRONG_CASE_REACHES_EXACT_SCORE_100_"
                            "WITH_NEW_WRONGS_COUNTED"
                        ),
                        "exact_case_repair_rate": 1.0,
                        "case_score_deficit_recovery_rate": 1.0,
                        "repair_metadata_collision_suspect_count": 0,
                        "repair_metadata_collision_suspect_case_ids": [],
                        "collision_adjusted_repair_diagnostic_rate": 1.0,
                        "collision_adjusted_repair_diagnostic_only": True,
                        "collision_adjusted_repair_qualification_effect": (
                            "NONE"
                        ),
                        "reviewer_repair_burden": {
                            "initial_burden_total_mean": 0.0,
                        },
                        "role_ability_form_scores": deepcopy(
                            role_ability_form_scores
                        ),
                        "repair_outcome_form_scores": deepcopy(
                            repair_outcome_form_scores
                        ),
                        "failed_exam_item_count": 0,
                        "scoring_axes": {
                            "ability": (
                                "Quality_CARD_REVIEWER_NONLINEAR_PIECEWISE"
                            ),
                            "repair_outcome": (
                                "Quality_CARD_REVIEWER_R1_1"
                            ),
                            "qualification": (
                                "Quality_CARD_REVIEWER_R1_1_"
                                "DISQUALIFY_ON_RESIDUAL"
                            ),
                            "operational_eligibility": (
                                CARD_REVIEWER_OPERATIONAL_POLICY_REVISION
                            ),
                            "comparison": (
                                "EXACT_SAME_CATEGORY_ROLE_PACK_SCORER_"
                                "PROJECTION_ONLY"
                            ),
                        },
                        "numeric_score_is_not_qualification_verdict": True,
                        "cross_category_coefficients_reused": False,
                        "global_ranking_forbidden": True,
                    }
                scorecard = exam.publish_dual_form_result(
                    provider=str(target.get("provider") or target.get("adapter_id")),
                    model=str(target["model_name"]),
                    parameters={
                        "profile_kind": target["kind"],
                        "thinking": target.get("thinking"),
                        "thinking_mode": target.get("thinking_mode"),
                        "maximum_model_calls": self._request_cap(
                            str(target["kind"])
                        ),
                        "quality_semantic_call_limit": self.Quality_SEMANTIC_CALL_LIMIT,
                        "case_chunk_limit": self.CASES_PER_CHUNK,
                    },
                    forms={
                        form_id: {
                            "status": "SCORED",
                            "score": role_ability_form_scores[form_id],
                            "hard_gate_verdict": hard_gates[form_id],
                        }
                        for form_id in ("A", "B")
                    },
                    evidence_refs=[
                        f"responses/form_{form_id}_final.json"
                        for form_id in ("A", "B")
                    ],
                )
                verdict = (
                    "PASS"
                    if operational_eligibility
                    in {
                        "SUITABLE",
                        "SUITABLE_WITH_CALIBRATION",
                        "SUITABLE_WITH_GOVERNED_DISPOSITION",
                    }
                    and scorecard["recommendation_code"]
                    == "SUITABLE_AS_CARD_REVIEWER"
                    else "FAIL"
                )

            raw_quality_verdict = verdict
            blocking_failures: list[dict[str, Any]] = []
            operational_rows = repair_projection.get(
                "operational_residual_diagnostics", {}
            )
            if isinstance(operational_rows, Mapping):
                for form_id in ("A", "B"):
                    rows = operational_rows.get(form_id, [])
                    if not isinstance(rows, list):
                        continue
                    for row in rows:
                        if not isinstance(row, Mapping):
                            continue
                        failed_fields = sorted(
                            {
                                str(value)
                                for key in (
                                    "safety_reasons",
                                    "calibration_reasons",
                                    "failed_output_fields",
                                )
                                for value in (row.get(key) or [])
                                if isinstance(value, str) and value
                            }
                        )
                        if not failed_fields:
                            failed_fields = [
                                str(row.get("risk_class") or "ROLE_RESIDUAL")
                            ]
                        blocking_failures.append(
                            {
                                "case_id": str(
                                    row.get("case_id")
                                    or f"{form_id}-UNKNOWN"
                                ),
                                "failed_fields": failed_fields,
                                "evidence_anchor": (
                                    f"scoring_projection/forms/{form_id}/"
                                    f"{row.get('case_id')}"
                                ),
                                "blocking": True,
                            }
                        )
            if raw_quality_verdict == "FAIL" and not blocking_failures:
                blocking_failures.append(
                    {
                        "case_id": "CARD_REVIEWER_AGGREGATE",
                        "failed_fields": [
                            "operational_eligibility_or_category_score"
                        ],
                        "evidence_anchor": "scoring_projection/aggregate",
                        "blocking": True,
                    }
                )
            quality_fail_admission = None
            if raw_quality_verdict == "FAIL":
                quality_fail_admission = admit_quality_fail(
                    execution_outcome=ExecutionOutcome.COMPLETED,
                    scorability=Scorability.SCOREABLE,
                    repair_rounds_used=repair_rounds,
                    repair_round_limit=self.SEMANTIC_REPAIR_ROUND_LIMIT,
                    repair_cycle_closed=(
                        repair_rounds == self.SEMANTIC_REPAIR_ROUND_LIMIT
                        or failed_exam_item_count == 0
                    ),
                    blocking_failures=blocking_failures,
                    exact_hashes={
                        "reference_pack_sha256": str(
                            plan["reference_pack_sha256"]
                        ),
                        "scoring_protocol_sha256": str(
                            comparison_projection["scoring_protocol_sha256"]
                        ),
                        "executor_sha256": _file_sha256(Path(__file__)),
                        "input_exact_set_sha256": _sha256(
                            {"forms": forms, "schemas": form_schemas}
                        ),
                    },
                )
                verdict = str(quality_fail_admission["quality_verdict"])

            exact_case_repair_rate = float(
                repair_projection.get(
                    "exact_case_repair_rate", repair_completion_rate
                )
            )
            case_score_deficit_recovery_rate = float(
                repair_projection.get(
                    "case_score_deficit_recovery_rate",
                    exact_case_repair_rate,
                )
            )
            repair_metadata_collision_suspect_count = int(
                repair_projection.get(
                    "repair_metadata_collision_suspect_count", 0
                )
            )
            collision_adjusted_repair_diagnostic_rate = float(
                repair_projection.get(
                    "collision_adjusted_repair_diagnostic_rate",
                    exact_case_repair_rate,
                )
            )

            if state["request_attempts"] > self._request_cap(str(target["kind"])):
                raise ValueError("WORKFLOW_MODEL_EXAM_CALL_LIMIT_EXCEEDED")
            cap = plan.get("budget_cap_cny")
            if cap is not None and state["estimated_cost_cny"] > float(cap):
                raise ValueError("WORKFLOW_MODEL_EXAM_COST_CAP_EXCEEDED")
            for form_id, response in responses.items():
                _write_json_create_only(
                    run_root / "responses" / f"form_{form_id}_final.json",
                    response,
                )
            public_result = {
                "schema_version": "WorkflowModelExamRunResult-v1",
                "status": verdict,
                "reason": "Quality_REFERENCE_REGRESSION_COMPLETED",
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "executor_ref": self.EXECUTOR_REF,
                "executor_binding_sha256": _file_sha256(Path(__file__)),
                "requested_model": requested_model,
                "returned_model": state.get("returned_model") if target.get("adapter_id")=="command_template" else requested_model,
                "score": int(round(score)),
                "score_exact": round(score, 6),
                "execution_outcome": ExecutionOutcome.COMPLETED.value,
                "scorability": Scorability.SCOREABLE.value,
                "raw_quality_candidate_verdict": raw_quality_verdict,
                "quality_fail_admission": quality_fail_admission,
                "score_semantics": score_semantics,
                "form_scores": {
                    key: round(float(value), 6)
                    for key, value in role_ability_form_scores.items()
                },
                "role_ability_form_scores": {
                    key: round(float(value), 6)
                    for key, value in role_ability_form_scores.items()
                },
                "repair_outcome_form_scores": {
                    key: round(float(value), 6)
                    for key, value in repair_outcome_form_scores.items()
                },
                "initial_form_scores": initial_form_scores,
                "post_repair_nonlinear_form_scores": {
                    form_id: round(
                        float(
                            repair_projection.get("forms", {})
                            .get(form_id, {})
                            .get(
                                "post_repair_nonlinear_score",
                                role_ability_form_scores[form_id],
                            )
                        ),
                        6,
                    )
                    for form_id in ("A", "B")
                },
                "qualification_verdict": qualification_verdict,
                "quality_exact_repair_qualification_verdict": (
                    qualification_verdict
                ),
                "operational_eligibility_verdict": operational_eligibility,
                "operational_safety_gate": operational_safety_gate,
                "operational_policy_revision": (
                    CARD_REVIEWER_OPERATIONAL_POLICY_REVISION
                ),
                "core_lifecycle_revision": CORE_CARD_REVIEWER_LIFECYCLE_REVISION,
                "core_closure_verdict": repair_projection.get(
                    "core_closure_verdict",
                    "DELTA_PASS" if failed_exam_item_count == 0 else "HUMAN_REQUIRED",
                ),
                "core_closure_score": repair_projection.get(
                    "core_closure_score",
                    repair_outcome_score,
                ),
                "governed_disposition_case_count": repair_projection.get(
                    "governed_disposition_case_count",
                    0,
                ),
                "safety_blocking_residual_count": (
                    safety_blocking_residual_count
                ),
                "calibration_residual_count": calibration_residual_count,
                "repair_prompt_projection": (
                    {
                        key: value
                        for key, value in repair_prompt_projection.items()
                        if key != "prompt"
                    }
                    if isinstance(repair_prompt_projection, Mapping)
                    else None
                ),
                "repair_prompt_projection_revision": (
                    CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION
                ),
                "scorecard_recommendation_code": (
                    scorecard.get("recommendation_code")
                    if isinstance(scorecard, Mapping)
                    else None
                ),
                "scoring_system_revision": (
                    CARD_REVIEWER_SCORING_PROJECTION_REVISION
                ),
                "score_axes": {
                    "role_ability_score": round(
                        float(
                            repair_projection.get(
                                "role_ability_score", score
                            )
                        ),
                        6,
                    ),
                    "post_repair_diagnostic_score": round(
                        float(
                            repair_projection.get(
                                "post_repair_nonlinear_score", score
                            )
                        ),
                        6,
                    ),
                    "reviewer_repair_outcome_score": round(
                        repair_outcome_score, 6
                    ),
                    "reviewer_repair_burden": round(
                        float(
                            repair_projection.get(
                                "reviewer_repair_burden", {}
                            ).get(
                                "initial_burden_total_mean", 0.0
                            )
                        ),
                        6,
                    ),
                    "repair_completion_rate": round(
                        repair_completion_rate, 9
                    ),
                    "exact_case_repair_rate": round(
                        exact_case_repair_rate, 9
                    ),
                    "case_score_deficit_recovery_rate": round(
                        case_score_deficit_recovery_rate, 9
                    ),
                    "repair_metadata_collision_suspect_count": (
                        repair_metadata_collision_suspect_count
                    ),
                    "collision_adjusted_repair_diagnostic_rate": round(
                        collision_adjusted_repair_diagnostic_rate, 9
                    ),
                    "collision_adjusted_repair_diagnostic_only": True,
                    "qualification_verdict": qualification_verdict,
                    "operational_eligibility_verdict": (
                        operational_eligibility
                    ),
                    "operational_safety_gate": operational_safety_gate,
                    "core_closure_score": round(
                        float(
                            repair_projection.get(
                                "core_closure_score",
                                repair_outcome_score,
                            )
                        ),
                        6,
                    ),
                },
                "scoring_projection": repair_projection,
                "comparison": comparison_projection,
                **comparison_projection,
                "failed_exam_item_count": failed_exam_item_count,
                "repair_rounds": repair_rounds,
                "semantic_repair_rounds": repair_rounds,
                "semantic_repair_round_limit": (
                    self.SEMANTIC_REPAIR_ROUND_LIMIT
                ),
                "semantic_repair_lifecycle": repair_projection.get(
                    "semantic_repair_lifecycle", []
                ),
                "cumulative_category_repair_penalty": repair_projection.get(
                    "cumulative_category_repair_penalty"
                ),
                "structural_repair_turns": sum(
                    int(scores[form_id].get("repair_turns") or 0)
                    for form_id in ("A", "B")
                ),
                "pointer_normalization_revision": (
                    CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION
                ),
                "pointer_normalization_count": len(
                    pointer_normalization_actions
                ),
                "pointer_normalizations": pointer_normalization_actions,
                "execution_profile": exam_inputs.get("execution_profile"),
                "sample_metadata": exam_inputs.get("sample_metadata"),
                "workload_profile": plan.get("workload_profile"),
                "initial_total_effective_prompt_chars": plan.get(
                    "initial_total_effective_prompt_chars"
                ),
                "transport_topologies": deepcopy(
                    state.get("transport_topologies", [])
                ),
                "output_limit_policy": plan.get("output_limit_policy"),
                "provider_calls": state["provider_calls"],
                "request_attempts": state["request_attempts"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "external_process_launches": state[
                    "external_process_launches"
                ],
                "token_usage": state["token_usage"],
                "estimated_cost_cny": (
                    round(state["estimated_cost_cny"], 9)
                    if plan["profile_kind"] == "API"
                    else (0.0 if plan["profile_kind"] == "LOCAL" else None)
                ),
                "actual_cost": None,
                "cost_semantics": plan["cost_semantics"],
                "reference_regression_only": True,
                "blind_holdout_eligible": False,
                "qualification_eligible": False,
                "provider_received_answer_key": False,
            }
            evidence = _write_json_create_only(
                run_root / "exam_result.json", public_result
            )
            duration_ms = max(0, int((monotonic() - started) * 1000))
            return {
                "schema_version": RESULT_SCHEMA,
                "kind": "WORKFLOW_NODE",
                "status": verdict,
                "score": int(round(score)),
                "score_exact": round(score, 6),
                "score_semantics": score_semantics,
                "execution_outcome": public_result["execution_outcome"],
                "scorability": public_result["scorability"],
                "raw_quality_candidate_verdict": public_result[
                    "raw_quality_candidate_verdict"
                ],
                "quality_fail_admission": public_result[
                    "quality_fail_admission"
                ],
                "reason": "Quality_REFERENCE_REGRESSION_COMPLETED",
                "requested_model": requested_model,
                "returned_model": state.get("returned_model") if target.get("adapter_id")=="command_template" else requested_model,
                "duration_ms": duration_ms,
                "external_network_calls": state["external_network_calls"],
                "provider_calls": state["provider_calls"],
                "request_attempts": state["request_attempts"],
                "external_model_calls": state["external_model_calls"],
                "external_process_launches": state[
                    "external_process_launches"
                ],
                "exam_mode": plan["mode"],
                "exam_run_id": run_id,
                "exam_run_root": str(run_root.resolve()),
                "exam_evidence_sha256": evidence["sha256"],
                "executor_ref": self.EXECUTOR_REF,
                "executor_binding_sha256": public_result[
                    "executor_binding_sha256"
                ],
                "failed_exam_item_count": failed_exam_item_count,
                "repair_rounds": repair_rounds,
                "semantic_repair_rounds": repair_rounds,
                "semantic_repair_round_limit": public_result[
                    "semantic_repair_round_limit"
                ],
                "semantic_repair_lifecycle": public_result[
                    "semantic_repair_lifecycle"
                ],
                "cumulative_category_repair_penalty": public_result[
                    "cumulative_category_repair_penalty"
                ],
                "structural_repair_turns": public_result[
                    "structural_repair_turns"
                ],
                "pointer_normalization_revision": public_result[
                    "pointer_normalization_revision"
                ],
                "pointer_normalization_count": public_result[
                    "pointer_normalization_count"
                ],
                "pointer_normalizations": public_result[
                    "pointer_normalizations"
                ],
                "core_closure_verdict": public_result[
                    "core_closure_verdict"
                ],
                "core_closure_score": public_result["core_closure_score"],
                "governed_disposition_case_count": public_result[
                    "governed_disposition_case_count"
                ],
                "execution_profile": public_result["execution_profile"],
                "sample_metadata": public_result["sample_metadata"],
                "workload_profile": public_result["workload_profile"],
                "initial_total_effective_prompt_chars": public_result[
                    "initial_total_effective_prompt_chars"
                ],
                "transport_topologies": public_result[
                    "transport_topologies"
                ],
                "output_limit_policy": public_result[
                    "output_limit_policy"
                ],
                "qualification_verdict": qualification_verdict,
                "quality_exact_repair_qualification_verdict": (
                    qualification_verdict
                ),
                "operational_eligibility_verdict": operational_eligibility,
                "operational_safety_gate": operational_safety_gate,
                "operational_policy_revision": (
                    CARD_REVIEWER_OPERATIONAL_POLICY_REVISION
                ),
                "safety_blocking_residual_count": (
                    safety_blocking_residual_count
                ),
                "calibration_residual_count": calibration_residual_count,
                "scoring_system_revision": (
                    CARD_REVIEWER_SCORING_PROJECTION_REVISION
                ),
                "repair_outcome_score": round(repair_outcome_score, 6),
                "repair_completion_rate": round(
                    repair_completion_rate, 9
                ),
                "exact_case_repair_rate": round(
                    exact_case_repair_rate, 9
                ),
                "case_score_deficit_recovery_rate": round(
                    case_score_deficit_recovery_rate, 9
                ),
                "repair_metadata_collision_suspect_count": (
                    repair_metadata_collision_suspect_count
                ),
                "collision_adjusted_repair_diagnostic_rate": round(
                    collision_adjusted_repair_diagnostic_rate, 9
                ),
                "collision_adjusted_repair_diagnostic_only": True,
                "repair_prompt_projection_revision": (
                    CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION
                ),
                **comparison_projection,
                "token_usage": state["token_usage"],
                "estimated_cost_cny": public_result["estimated_cost_cny"],
                "actual_cost": None,
                "cost_semantics": plan["cost_semantics"],
                "reference_regression_only": True,
                "blind_holdout_eligible": False,
                "qualification_eligible": False,
                "provider_received_answer_key": False,
            }
        except Exception as error:
            reason = str(error) or type(error).__name__
            if len(reason) > 200:
                reason = type(error).__name__
            failure = {
                "schema_version": "WorkflowModelExamRunFailure-v1",
                "status": "NOT_ASSESSED",
                "reason": reason,
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "provider_calls": state["provider_calls"],
                "request_attempts": state["request_attempts"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "external_process_launches": state[
                    "external_process_launches"
                ],
                "token_usage": state["token_usage"],
                "estimated_cost_cny": (
                    round(state["estimated_cost_cny"], 9)
                    if plan["profile_kind"] == "API"
                    else (0.0 if plan["profile_kind"] == "LOCAL" else None)
                ),
                "score": None,
                "execution_outcome": classify_execution_outcome(
                    {"status": "ERROR", "reason": reason}
                ).value,
                "scorability": Scorability.NOT_ASSESSED.value,
            }
            evidence = _write_json_create_only(
                run_root / "exam_failure.json", failure
            )
            result = _not_run(reason, requested_model=requested_model)
            result.update(
                duration_ms=max(0, int((monotonic() - started) * 1000)),
                provider_calls=state["provider_calls"],
                request_attempts=state["request_attempts"],
                external_model_calls=state["external_model_calls"],
                external_network_calls=state["external_network_calls"],
                external_process_launches=state[
                    "external_process_launches"
                ],
                exam_run_id=run_id,
                exam_run_root=str(run_root.resolve()),
                exam_evidence_sha256=evidence["sha256"],
                token_usage=state["token_usage"],
                estimated_cost_cny=failure["estimated_cost_cny"],
                cost_semantics=plan["cost_semantics"],
                execution_outcome=failure["execution_outcome"],
                scorability=failure["scorability"],
            )
            return result


class QualityCardReviewerExamSuccessorExecutor(QualityCardReviewerExamExecutor):
    """Balanced, workload-bounded successor used by the Desktop executable."""

    EXECUTOR_REF = (
        "Desktop_MODEL_EXAM_SUCCESSOR_Quality_CARD_REVIEWER_EXECUTOR_V3_TWO_ROUND"
    )
    MODE = "LIVE_Quality_CARD_REVIEWER_SUCCESSOR_REFERENCE_REGRESSION"
    Quality_SEMANTIC_CALL_LIMIT = 4
    SEMANTIC_REPAIR_ROUND_LIMIT = 2
    API_MAXIMUM_REQUEST_ATTEMPTS = 24
    CLI_MAXIMUM_REQUEST_ATTEMPTS = 24
    LOCAL_MAXIMUM_REQUEST_ATTEMPTS = 24
    MAX_TRANSPORT_SHARDS = 12
    CASES_PER_CHUNK = None
    LOCAL_SUBJECT_PROJECTION_REVISION = (
        "Desktop_LOCAL_EVALUATOR_MARKER_SAFE_SUBJECT_PROJECTION_V1"
    )

    def _request_cap(self, profile_kind: str) -> int:
        # The successor selects one balanced panel, not the entire ancestor
        # archive. Reuse its frozen per-run budget declaration; this is an
        # authorization bound shown before execution, not a token/output limit.
        explicit = {"API": self._api_request_cap, "CLI": self._cli_request_cap,
                    "LOCAL": self._local_request_cap}[profile_kind]
        return explicit if explicit is not None else {
            "API": self.API_MAXIMUM_REQUEST_ATTEMPTS,
            "CLI": self.CLI_MAXIMUM_REQUEST_ATTEMPTS,
            "LOCAL": self.LOCAL_MAXIMUM_REQUEST_ATTEMPTS,
        }[profile_kind]

    @staticmethod
    def _sample_slot(context: Mapping[str, Any]) -> int:
        explicit = context.get("workflow_exam_sample_slot")
        if explicit is not None:
            if isinstance(explicit, bool) or not isinstance(explicit, int):
                raise ValueError("CARD_REVIEWER_SUCCESSOR_SAMPLE_SLOT_INVALID")
            if explicit not in CARD_REVIEWER_SUCCESSOR_SAMPLE_SLOTS:
                raise ValueError("CARD_REVIEWER_SUCCESSOR_SAMPLE_SLOT_INVALID")
            return explicit
        ordinal = context.get("workflow_exam_attempt_ordinal", 1)
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            raise ValueError("CARD_REVIEWER_SUCCESSOR_ATTEMPT_ORDINAL_INVALID")
        return ((ordinal - 1) % len(CARD_REVIEWER_SUCCESSOR_SAMPLE_SLOTS)) + 1

    def _exam_inputs(
        self,
        *,
        pack: Any,
        schema: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        source_forms = {
            form_id: _load_json(pack.member(f"forms/form_{form_id}.json"))
            for form_id in ("A", "B")
        }
        source_golds = {
            form_id: _load_json(pack.member(f"gold/form_{form_id}_gold.json"))
            for form_id in ("A", "B")
        }
        sample = build_card_reviewer_successor_sample(
            forms=source_forms,
            golds=source_golds,
            schema=schema,
            sample_slot=self._sample_slot(context),
        )
        return {
            "forms": sample["forms"],
            "golds": sample["golds"],
            "schemas": sample["schemas"],
            "execution_profile": "BALANCED_MEDIUM_HARD_SAMPLE",
            "sample_metadata": sample["manifest"],
        }

    def _execute_form_protocol(
        self,
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
        return execute_card_reviewer_successor_form(
            protocol=protocol,
            exam=exam,
            form=form,
            schema=schema,
            prompt_contract=prompt_contract,
            scoring_protocol=scoring_protocol,
            subject_call=subject_call,
            gold=gold,
        )

    def _score_after_semantic_repair(
        self,
        *,
        exam: Any,
        forms: Mapping[str, Mapping[str, Any]],
        golds: Mapping[str, Mapping[str, Any]],
        original_scores: Mapping[str, Mapping[str, Any]],
        merged_responses: Mapping[str, Mapping[str, Any]],
        schema: Mapping[str, Any],
        scoring_protocol: Mapping[str, Any],
        repair_contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        def successor_scorer(**values: Any) -> Mapping[str, Any]:
            return score_card_reviewer_successor_form(exam=exam, **values)

        return score_card_reviewer_role_projection(
            exam=exam,
            forms=forms,
            golds=golds,
            original_scores=original_scores,
            merged_responses=merged_responses,
            schema=schema,
            scoring_protocol=scoring_protocol,
            repair_contract=repair_contract,
            score_form_fn=successor_scorer,
            call_legacy_whole_exam_scorer=False,
        )

    def _score_current_responses(
        self,
        *,
        exam: Any,
        forms: Mapping[str, Mapping[str, Any]],
        golds: Mapping[str, Mapping[str, Any]],
        responses: Mapping[str, Mapping[str, Any]],
        schema: Mapping[str, Any],
        scoring_protocol: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        return {
            form_id: dict(
                score_card_reviewer_successor_form(
                    exam=exam,
                    form=forms[form_id],
                    final_response=responses[form_id],
                    gold=golds[form_id],
                    schema=schema,
                    scoring_protocol=scoring_protocol,
                    repair_metrics={
                        "form_case_count": len(
                            exam.form_cases(forms[form_id])[1]
                        ),
                        "repair_turns": 0,
                        "directed_repair_case_count": 0,
                        "all_repairs_succeeded": True,
                    },
                )
            )
            for form_id in ("A", "B")
        }

    def _structured_output_policy(
        self,
        target: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int | None, str]:
        return workflow_exam_structured_output_policy(target)

    @classmethod
    def _subject_request_projection(
        cls,
        target: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        if target.get("kind") != "LOCAL":
            return deepcopy(dict(request))

        def safe_text(value: str) -> str:
            replacements = (
                (re.compile(r"(?i)answer[_ -]?key"), "reference_values"),
                (re.compile(r"(?i)expected[ -]+answer"), "reference value"),
                (re.compile(r"(?i)\bgold\b"), "reference labels"),
                (re.compile(r"(?i)\bscorer\b"), "local validator"),
            )
            projected = value
            for pattern, replacement in replacements:
                projected = pattern.sub(replacement, projected)
            return projected

        def project(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {
                    safe_text(str(key)): project(item)
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [project(item) for item in value]
            if isinstance(value, str):
                return safe_text(value)
            return deepcopy(value)

        projected = project(request)
        if not isinstance(projected, dict):
            raise ValueError("LOCAL_SUBJECT_REQUEST_PROJECTION_INVALID")
        projected.pop("request_payload_hash", None)
        projected["subject_transport_projection"] = {
            "schema_version": cls.LOCAL_SUBJECT_PROJECTION_REVISION,
            "semantic_contract_changed": False,
            "evaluator_data_included": False,
            "integration_marker_gate_preserved": True,
        }
        projected["request_payload_hash"] = _sha256(projected)
        serialized = _serialized_request(projected)
        if re.search(
            r"(?i)(?:\bgold\b|\bscorer\b|answer[_ -]?key|expected[ -]+answer)",
            serialized,
        ):
            raise ValueError("LOCAL_SUBJECT_REQUEST_PROJECTION_INCOMPLETE")
        projected_schema = projected.get("output_schema")
        if not isinstance(projected_schema, Mapping):
            raise ValueError("LOCAL_SUBJECT_RESPONSE_SCHEMA_MISSING")
        if (
            _effective_structured_prompt_metrics(
                serialized,
                projected_schema,
            )["effective_prompt_chars"]
            > CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_DESIGN_INPUT_OUT_OF_BOUNDS")
        return projected

    @staticmethod
    def _project_exam_repair_schema(
        schema: Mapping[str, Any],
        exact_sets: Mapping[str, list[str]],
    ) -> dict[str, Any]:
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            raise ValueError("CARD_REVIEWER_REPAIR_SCHEMA_INVALID")
        selected_properties: dict[str, Any] = {}
        required: list[str] = []
        for form_id in ("A", "B"):
            case_ids = exact_sets.get(form_id, [])
            if not case_ids:
                continue
            form_key = f"form_{form_id}"
            source = properties.get(form_key)
            if not isinstance(source, Mapping):
                raise ValueError("CARD_REVIEWER_REPAIR_SCHEMA_INVALID")
            projected = deepcopy(dict(source))
            try:
                reviews = projected["properties"]["reviews"]
                case_schema = reviews["items"]["properties"]["case_id"]
            except (KeyError, TypeError) as error:
                raise ValueError("CARD_REVIEWER_REPAIR_SCHEMA_INVALID") from error
            reviews["minItems"] = len(case_ids)
            reviews["maxItems"] = len(case_ids)
            case_schema.clear()
            case_schema.update({"type": "string", "enum": list(case_ids)})
            selected_properties[form_key] = projected
            required.append(form_key)
        if not required:
            raise ValueError("CARD_REVIEWER_REPAIR_SCHEMA_EMPTY")
        value = deepcopy(dict(schema))
        value["properties"] = selected_properties
        value["required"] = required
        return value

    def _case_shards(
        self,
        *,
        request: Mapping[str, Any],
        response_schema: Mapping[str, Any],
    ) -> list[tuple[dict[str, Any], dict[str, Any], list[str], bool]] | None:
        form = request.get("form")
        cases = form.get("cases") if isinstance(form, Mapping) else None
        if not isinstance(cases, list) or not cases:
            return None
        parent_hash = str(request.get("request_payload_hash") or _sha256(request))

        def build(
            selected: list[Any],
            *,
            index: int,
            count: int,
        ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
            case_ids = [
                str(item.get("case_id"))
                for item in selected
                if isinstance(item, Mapping) and isinstance(item.get("case_id"), str)
            ]
            if len(case_ids) != len(selected) or len(case_ids) != len(set(case_ids)):
                raise ValueError("WORKFLOW_MODEL_EXAM_CHUNK_CASE_SET_INVALID")
            projected_schema = _project_card_reviewer_schema(
                response_schema,
                case_ids,
            )
            chunk_request = deepcopy(dict(request))
            chunk_form = deepcopy(dict(form))
            chunk_form["cases"] = deepcopy(selected)
            chunk_request["form"] = chunk_form
            chunk_request["output_schema"] = deepcopy(projected_schema)
            chunk_request["chunk_contract"] = {
                "schema_version": "DesktopCardReviewerCapabilitiesBoundedShard-v1",
                "parent_request_payload_hash": parent_hash,
                "chunk_index": index,
                "chunk_count": count,
                "case_ids": case_ids,
                "case_ids_sha256": _sha256(case_ids),
                "overlap_unit_count": 0,
                "merge_policy": "EXACT_SET_ORDERED_CONCATENATION",
            }
            chunk_request.pop("request_payload_hash", None)
            chunk_request["request_payload_hash"] = _sha256(chunk_request)
            return chunk_request, projected_schema, case_ids

        groups: list[list[Any]] = []
        current: list[Any] = []
        for case in cases:
            candidate = [*current, case]
            candidate_request, candidate_schema, _ = build(
                candidate,
                index=99,
                count=99,
            )
            metrics = _effective_structured_prompt_metrics(
                _serialized_request(candidate_request),
                candidate_schema,
            )
            if (
                current
                and metrics["effective_prompt_chars"]
                > CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
            ):
                groups.append(current)
                current = [case]
            else:
                current = candidate
        if current:
            groups.append(current)
        if len(groups) > self.MAX_TRANSPORT_SHARDS:
            raise ValueError("WORKFLOW_MODEL_EXAM_MAX_LEAF_SEGMENTS_EXCEEDED")
        result: list[tuple[dict[str, Any], dict[str, Any], list[str], bool]] = []
        for index, group in enumerate(groups, start=1):
            chunk_request, projected_schema, case_ids = build(
                group,
                index=index,
                count=len(groups),
            )
            metrics = _effective_structured_prompt_metrics(
                _serialized_request(chunk_request),
                projected_schema,
            )
            observed = metrics["effective_prompt_chars"]
            atomic_exception = (
                observed > CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
                and len(group) == 1
            )
            if observed > CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT:
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_DESIGN_INPUT_OUT_OF_BOUNDS"
                )
            if (
                observed > CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
                and not atomic_exception
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_SOFT_LIMIT_SHARDING_FAILED")
            result.append(
                (chunk_request, projected_schema, case_ids, atomic_exception)
            )
        return result

    def _repair_shards(
        self,
        *,
        request: Mapping[str, Any],
        response_schema: Mapping[str, Any],
    ) -> list[
        tuple[dict[str, Any], dict[str, Any], dict[str, list[str]], bool]
    ] | None:
        if request.get("phase") != "EXAM_DIRECTED_REPAIR":
            return None
        semantic_repair_round = request.get("semantic_repair_round", 1)
        if semantic_repair_round not in {1, 2}:
            raise ValueError("CARD_REVIEWER_SEMANTIC_REPAIR_ROUND_INVALID")
        user = request.get("user")
        form = request.get("form")
        if not isinstance(user, str) or not isinstance(form, Mapping):
            raise ValueError("CARD_REVIEWER_REPAIR_REQUEST_INVALID")
        try:
            parent_prompt = json.loads(user)
        except json.JSONDecodeError as error:
            raise ValueError("CARD_REVIEWER_REPAIR_PROMPT_INVALID") from error
        targets_by_form = parent_prompt.get("repair_targets")
        exact_sets = form.get("wrong_item_exact_set")
        if not isinstance(targets_by_form, Mapping) or not isinstance(
            exact_sets, Mapping
        ):
            raise ValueError("CARD_REVIEWER_REPAIR_TARGETS_INVALID")
        units: list[tuple[str, dict[str, Any]]] = []
        for form_id in ("A", "B"):
            targets = targets_by_form.get(form_id, [])
            expected = list(exact_sets.get(form_id, []))
            if not isinstance(targets, list) or [
                item.get("case_id") if isinstance(item, Mapping) else None
                for item in targets
            ] != expected:
                raise ValueError("CARD_REVIEWER_REPAIR_TARGET_ORDER_INVALID")
            units.extend((form_id, deepcopy(dict(item))) for item in targets)
        if not units:
            raise ValueError("CARD_REVIEWER_REPAIR_TARGETS_EMPTY")
        parent_hash = str(request.get("request_payload_hash") or _sha256(request))

        def build(
            selected: list[tuple[str, dict[str, Any]]],
            *,
            index: int,
            count: int,
        ) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[str]]]:
            selected_targets = {"A": [], "B": []}
            selected_ids: dict[str, list[str]] = {"A": [], "B": []}
            for form_id, target in selected:
                case_id = target.get("case_id")
                if not isinstance(case_id, str) or not case_id:
                    raise ValueError("CARD_REVIEWER_REPAIR_TARGET_ID_INVALID")
                selected_targets[form_id].append(deepcopy(target))
                selected_ids[form_id].append(case_id)
            selected_targets = {
                key: value for key, value in selected_targets.items() if value
            }
            nonempty_ids = {
                key: value for key, value in selected_ids.items() if value
            }
            projected_schema = self._project_exam_repair_schema(
                response_schema,
                nonempty_ids,
            )
            shard_prompt = deepcopy(dict(parent_prompt))
            shard_prompt["wrong_item_exact_set"] = deepcopy(nonempty_ids)
            shard_prompt["repair_targets"] = selected_targets
            shard_prompt["output_schema"] = deepcopy(projected_schema)
            shard_prompt["transport_shard"] = {
                "schema_version": "DesktopCardReviewerCapabilitiesBoundedRepairShard-v1",
                "parent_request_payload_hash": parent_hash,
                "chunk_index": index,
                "chunk_count": count,
                "case_ids_by_form": deepcopy(nonempty_ids),
                "case_ids_sha256": _sha256(nonempty_ids),
                "semantic_repair_round": semantic_repair_round,
                "transport_shards_do_not_increment_semantic_round": True,
                "merge_policy": "EXACT_SET_BY_FORM_ORDERED_CONCATENATION",
            }
            shard_request = deepcopy(dict(request))
            shard_request["user"] = json.dumps(
                shard_prompt,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            shard_request["form"] = {
                "forms": list(nonempty_ids),
                "wrong_item_exact_set": deepcopy(nonempty_ids),
            }
            shard_request["output_schema"] = deepcopy(projected_schema)
            shard_request["transport_shard"] = deepcopy(
                shard_prompt["transport_shard"]
            )
            shard_request.pop("request_payload_hash", None)
            shard_request["request_payload_hash"] = _sha256(shard_request)
            return shard_request, projected_schema, nonempty_ids

        groups: list[list[tuple[str, dict[str, Any]]]] = []
        current: list[tuple[str, dict[str, Any]]] = []
        for unit in units:
            candidate = [*current, unit]
            candidate_request, candidate_schema, _ = build(
                candidate,
                index=99,
                count=99,
            )
            metrics = _effective_structured_prompt_metrics(
                _serialized_request(candidate_request),
                candidate_schema,
            )
            if (
                current
                and metrics["effective_prompt_chars"]
                > CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
            ):
                groups.append(current)
                current = [unit]
            else:
                current = candidate
        if current:
            groups.append(current)
        if len(groups) > self.MAX_TRANSPORT_SHARDS:
            raise ValueError("WORKFLOW_MODEL_EXAM_MAX_LEAF_SEGMENTS_EXCEEDED")
        result = []
        for index, group in enumerate(groups, start=1):
            shard_request, projected_schema, ids_by_form = build(
                group,
                index=index,
                count=len(groups),
            )
            metrics = _effective_structured_prompt_metrics(
                _serialized_request(shard_request),
                projected_schema,
            )
            observed = metrics["effective_prompt_chars"]
            atomic_exception = (
                observed > CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
                and len(group) == 1
            )
            if observed > CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT:
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_DESIGN_INPUT_OUT_OF_BOUNDS"
                )
            if (
                observed > CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
                and not atomic_exception
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_SOFT_LIMIT_SHARDING_FAILED")
            result.append(
                (shard_request, projected_schema, ids_by_form, atomic_exception)
            )
        return result

    def _chunked_provider_call(
        self,
        *,
        run_id: str,
        run_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        request: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        call_kind: str,
    ) -> dict[str, Any]:
        repair_shards = self._repair_shards(
            request=request,
            response_schema=response_schema,
        )
        semantic_repair_round = request.get("semantic_repair_round", 1)
        case_shards = (
            None
            if repair_shards is not None
            else self._case_shards(request=request, response_schema=response_schema)
        )
        if repair_shards is None and case_shards is None:
            metrics = _effective_structured_prompt_metrics(
                _serialized_request(request),
                response_schema,
            )
            if (
                metrics["effective_prompt_chars"]
                > CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_DESIGN_INPUT_OUT_OF_BOUNDS"
                )
            return self._stable_provider_call(
                run_id=run_id,
                run_root=run_root,
                state=state,
                target=target,
                request=request,
                response_schema=response_schema,
                structured_chat=structured_chat,
                call_kind=call_kind,
            )

        topology_records: list[dict[str, Any]] = []
        if repair_shards is not None:
            merged: dict[str, dict[str, list[Any]]] = {}
            for index, (shard_request, shard_schema, ids_by_form, atomic) in enumerate(
                repair_shards,
                start=1,
            ):
                response = self._stable_provider_call(
                    run_id=run_id,
                    run_root=run_root,
                    state=state,
                    target=target,
                    request=shard_request,
                    response_schema=shard_schema,
                    structured_chat=structured_chat,
                    call_kind=(
                        f"{call_kind}_SHARD_{index}_OF_{len(repair_shards)}"
                    ),
                )
                for form_id, expected_ids in ids_by_form.items():
                    form_key = f"form_{form_id}"
                    value = response.get(form_key)
                    rows = value.get("reviews") if isinstance(value, Mapping) else None
                    observed_ids = [
                        row.get("case_id") if isinstance(row, Mapping) else None
                        for row in rows or []
                    ]
                    if observed_ids != expected_ids:
                        raise ValueError(
                            "CARD_REVIEWER_REPAIR_SHARD_EXACT_SET_MISMATCH"
                        )
                    merged.setdefault(form_key, {"reviews": []})["reviews"].extend(
                        deepcopy(rows)
                    )
                topology_records.append(
                    {
                        "shard_index": index,
                        "case_ids_by_form": deepcopy(ids_by_form),
                        "atomic_soft_limit_exception": atomic,
                        "request_payload_sha256": shard_request[
                            "request_payload_hash"
                        ],
                    }
                )
            state.setdefault("transport_topologies", []).append(
                {
                    "call_kind": call_kind,
                    "topology_revision": "LONG_DOCUMENT_EXACT_SET_NO_OVERLAP_ADAPTER_V1",
                    "semantic_repair_round": semantic_repair_round,
                    "shards": topology_records,
                    "merge_hash": _sha256(merged),
                }
            )
            return merged

        assert case_shards is not None
        merged_reviews: list[Any] = []
        for index, (shard_request, shard_schema, case_ids, atomic) in enumerate(
            case_shards,
            start=1,
        ):
            response = self._stable_provider_call(
                run_id=run_id,
                run_root=run_root,
                state=state,
                target=target,
                request=shard_request,
                response_schema=shard_schema,
                structured_chat=structured_chat,
                call_kind=f"{call_kind}_SHARD_{index}_OF_{len(case_shards)}",
            )
            rows = response.get("reviews")
            observed_ids = [
                row.get("case_id") if isinstance(row, Mapping) else None
                for row in rows or []
            ]
            if not isinstance(rows, list) or observed_ids != case_ids:
                raise ValueError("CARD_REVIEWER_SHARD_EXACT_SET_MISMATCH")
            merged_reviews.extend(deepcopy(rows))
            topology_records.append(
                {
                    "shard_index": index,
                    "case_ids": case_ids,
                    "atomic_soft_limit_exception": atomic,
                    "request_payload_sha256": shard_request[
                        "request_payload_hash"
                    ],
                }
            )
        state.setdefault("transport_topologies", []).append(
            {
                "call_kind": call_kind,
                "topology_revision": "LONG_DOCUMENT_EXACT_SET_NO_OVERLAP_ADAPTER_V1",
                "shards": topology_records,
                "merge_hash": _sha256(merged_reviews),
            }
        )
        return {"reviews": merged_reviews}

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        plan = super().plan(node, target, context)
        if plan.get("status") != "READY":
            return plan
        try:
            modules = self._quality()
            pack = modules["reference"].load_card_reviewer_reference_exam_pack(
                self._reference_pack_root
            )
            schema = _load_json(
                pack.member("schemas/card_reviewer_output.schema.json")
            )
            prompt_contract = _load_json(
                pack.member("prompts/card_reviewer_prompt.json")
            )
            (
                model_binding,
                request_max_output_tokens,
                output_limit_mode,
            ) = self._structured_output_policy(target)
            inputs = self._exam_inputs(pack=pack, schema=schema, context=context)
            initial_metrics: dict[str, dict[str, int]] = {}
            for form_id in ("A", "B"):
                request = modules["protocol"].build_initial_request(
                    form=inputs["forms"][form_id],
                    schema=inputs["schemas"][form_id],
                    prompt_contract=prompt_contract,
                )
                planned_shards = self._case_shards(
                    request=request,
                    response_schema=inputs["schemas"][form_id],
                )
                if not planned_shards:
                    raise ValueError("WORKFLOW_MODEL_EXAM_INITIAL_TOPOLOGY_EMPTY")
                shard_metrics = [
                    _effective_structured_prompt_metrics(
                        _serialized_request(shard_request),
                        shard_schema,
                    )
                    for shard_request, shard_schema, _case_ids, _atomic
                    in planned_shards
                ]
                initial_metrics[form_id] = {
                    "shard_count": len(shard_metrics),
                    "prompt_chars": sum(
                        item["prompt_chars"] for item in shard_metrics
                    ),
                    "response_schema_chars": sum(
                        item["response_schema_chars"] for item in shard_metrics
                    ),
                    "effective_prompt_chars": sum(
                        item["effective_prompt_chars"] for item in shard_metrics
                    ),
                    "effective_prompt_utf8_bytes": sum(
                        item["effective_prompt_utf8_bytes"] for item in shard_metrics
                    ),
                    "maximum_shard_effective_prompt_chars": max(
                        item["effective_prompt_chars"] for item in shard_metrics
                    ),
                }
            total_initial_chars = sum(
                item["effective_prompt_chars"] for item in initial_metrics.values()
            )
        except ValueError as error:
            bounded_design_reasons = {
                "WORKFLOW_MODEL_EXAM_DESIGN_INPUT_OUT_OF_BOUNDS",
                "WORKFLOW_MODEL_EXAM_MAX_LEAF_SEGMENTS_EXCEEDED",
                "WORKFLOW_MODEL_EXAM_SOFT_LIMIT_SHARDING_FAILED",
                "LOCAL_EXAM_OUTPUT_LIMIT_PROFILE_INVALID",
                "LOCAL_EXAM_CONTEXT_LIMIT_PROFILE_INVALID",
            }
            reason = str(error)
            if reason in bounded_design_reasons:
                return _not_available(reason)
            return _not_available("WORKFLOW_MODEL_EXAM_SUCCESSOR_ASSETS_INVALID")
        except (ImportError, OSError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_SUCCESSOR_ASSETS_INVALID")
        sample_manifest = inputs["sample_metadata"]
        comparison_basis = {
            "legacy_comparison_binding_sha256": plan[
                "comparison_binding_sha256"
            ],
            "executor_ref": self.EXECUTOR_REF,
            "sample_revision": sample_manifest["schema_version"],
            "sample_slot": sample_manifest["sample_slot"],
            "sample_manifest_sha256": sample_manifest["manifest_sha256"],
            "scoring_projection_revision": (
                CARD_REVIEWER_SCORING_PROJECTION_REVISION
            ),
            "pointer_normalization_revision": (
                CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION
            ),
            "repair_prompt_projection_revision": (
                CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION
            ),
            "subject_transport_projection_revision": (
                self.LOCAL_SUBJECT_PROJECTION_REVISION
                if plan["profile_kind"] == "LOCAL"
                else "NONE"
            ),
        }
        successor_cohort = _sha256(comparison_basis)
        comparison = deepcopy(dict(plan["comparison"]))
        comparison.update(
            {
                "sample_revision": sample_manifest["schema_version"],
                "sample_slot": sample_manifest["sample_slot"],
                "sample_manifest_sha256": sample_manifest["manifest_sha256"],
                "comparison_cohort_id": (
                    f"CARD_REVIEWER_SUCCESSOR:{successor_cohort}"
                ),
                "comparison_binding_sha256": successor_cohort,
                "three_slot_series_aggregate_required_for_stable_cache": True,
                "subject_transport_projection_revision": comparison_basis[
                    "subject_transport_projection_revision"
                ],
            }
        )
        plan.update(
            {
                "mode": self.MODE,
                "reason": "Quality_CARD_REVIEWER_SUCCESSOR_READY",
                "executor_ref": self.EXECUTOR_REF,
                "comparison": comparison,
                **comparison,
                "sample_revision": sample_manifest["schema_version"],
                "sample_slot": sample_manifest["sample_slot"],
                "sample_manifest_sha256": sample_manifest["manifest_sha256"],
                "sample_case_count": sample_manifest["selected_case_count"],
                "sample_difficulty_profile": "MEDIUM_PLUS_HARD_NO_ADVERSARIAL",
                "workload_profile": core_card_reviewer_workload_profile(),
                "initial_effective_prompt_metrics_by_half": initial_metrics,
                "initial_total_effective_prompt_chars": total_initial_chars,
                "initial_total_within_soft_target": (
                    total_initial_chars
                    <= CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
                ),
                "initial_total_effective_prompt_semantics": (
                    "SUM_OF_INDEPENDENT_TRANSPORT_CALLS_DIAGNOSTIC_ONLY_"
                    "NEVER_COMPARED_TO_ONE_CALL_LIMIT"
                ),
                "total_question_material_semantics": (
                    "UNIQUE_BALANCED_SAMPLE_FULL_REVIEW_PER_SEMANTIC_PASS"
                ),
                "sharding_revision": (
                    "LONG_DOCUMENT_EXACT_SET_NO_OVERLAP_ADAPTER_V1"
                ),
                "case_chunk_limit": None,
                "char_bounded_sharding": True,
                "output_limit_policy": (
                    "CALIBRATED_CEILING_WITH_PROVIDER_DEFAULT_API_CLI_"
                    "AND_MODEL_PROFILE_LOCAL"
                ),
                "calibrated_reply_token_ceiling": (
                    WORKFLOW_EXAM_REPLY_TOKEN_CEILING
                ),
                "output_limit_contract": workflow_exam_output_limit_contract(),
                "subject_transport_projection_revision": comparison_basis[
                    "subject_transport_projection_revision"
                ],
                "structured_output_profile": {
                    "mode": output_limit_mode,
                    "request_max_output_tokens": request_max_output_tokens,
                    "context_window_tokens": (
                        model_binding.get("context_window_tokens")
                        if plan["profile_kind"] == "LOCAL"
                        else None
                    ),
                    "profile_specific_not_global": True,
                },
                "semantic_repair_round_limit": 2,
                "second_semantic_repair_allowed": True,
                "second_round_scope": (
                    "ROUND1_RESIDUAL_EXACT_SET_ONLY_NO_FULL_FORM_RETRY"
                ),
                "second_round_penalty_semantics": (
                    "CUMULATIVE_Quality_CATEGORY_PENALTY_PLUS_EXISTING_"
                    "CATEGORY_FIXED_AND_VOLUME_BURDEN"
                ),
                "pointer_normalization_revision": (
                    CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION
                ),
                "repair_prompt_projection_revision": (
                    CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION
                ),
                "pointer_normalization_semantics": (
                    "GOLD_FREE_DETERMINISTIC_MECHANICAL_RECOVERY_"
                    "DOES_NOT_INCREMENT_SEMANTIC_REPAIR_ROUNDS"
                ),
                "core_terminal_disposition_enabled": True,
            }
        )
        plan["plan_sha256"] = _sha256(
            {key: value for key, value in plan.items() if key != "plan_sha256"}
        )
        return plan
