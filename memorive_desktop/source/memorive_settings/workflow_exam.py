from __future__ import annotations
from memorive_settings.provider_catalog_aliases import matches_result_model

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import itertools
import json
import math
from pathlib import Path
import re
import statistics
from time import monotonic
from typing import Any, Callable, Mapping, Sequence
import unicodedata
import uuid
from model_gateway.resource_limits import resolve_call_limits

from .exam_successor import (
    ANALYSIS_REPAIR_ACCEPTANCE_REVISION,
    ANALYSIS_REPAIR_MATERIALITY_REVISION,
    ExecutionOutcome,
    Scorability,
    WORKFLOW_EXAM_REPLY_TOKEN_CEILING,
    admit_quality_fail,
    analysis_cohort_model_fail_decision,
    analysis_material_repair_targets,
    analysis_repair_acceptance,
    build_analysis_panel_cohort,
    build_repair_round,
    classify_execution_outcome,
    clean_control_residual,
    cumulative_category_repair_penalty,
    determine_scorability,
    terminal_disposition,
    workflow_exam_output_limit_contract,
    workflow_exam_structured_output_policy,
)
from .retrieval_exam_calibration import (
    EMBEDDING_ROLE_ADEQUACY_POLICY,
    assess_embedding_role_adequacy,
)


PLAN_SCHEMA = "WorkflowModelExamPlan-v2"
AUTHORIZATION_SCHEMA = "WorkflowModelExamAuthorization-v1"
RESULT_SCHEMA = "SettingsModelValidationResult-v1"
EXECUTOR_REF = "MODEL_EVALUATION_MODEL_EVALUATION_CARD_DISTILLER_EXECUTOR_R1_3"
PASSING_SCORE = 85.0
MAX_CALLS_PER_STAGE = 17
MAX_STRUCTURAL_REPAIR_ROUNDS = 2
MAX_OUTPUT_TOKENS = 32768  # frozen PRICING snapshot identity only; never sent as a task cap
REQUEST_TIMEOUT_SECONDS = 300
SAFE_PACKAGE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
CARD_REVIEWER_EXAM_CATEGORY_ID = "Quality_CARD_REVIEWER_REFERENCE_REGRESSION"
CARD_DISTILLER_EXAM_CATEGORY_ID = "Quality_CARD_DISTILLER_REFERENCE_REGRESSION"
ANALYSIS_PRIMARY_EXAM_CATEGORY_ID = "Quality_ANALYSIS_PRIMARY_REFERENCE_REGRESSION"
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
Core_PRIMARY_WORKLOAD_PROFILE_REVISION = (
    "Desktop_Core_ACTUAL_CARD_ANALYSIS_MEAN_X2_V1"
)
Core_PRIMARY_WORKLOAD_MEASUREMENT_SHA256 = (
    "7920FEE97BB1D1AD618AFFE93155C1FF9A3F5E3F6F049822C88D909A54C57C1E"
)
Core_PRIMARY_WORKLOAD_CLOSEOUT_SHA256 = (
    "65606B32990005793E563D9C2B0B7AA5CF96CA3C7CD56FA7A2440CB4B22418A8"
)
Core_PRIMARY_WORKLOAD_MULTIPLIER = 2.0
Core_PRIMARY_CARD_COUNT = 19
Core_PRIMARY_CARD_MEAN_CHARS = 22_017.157895
Core_PRIMARY_CARD_OUTPUT_CHAR_LIMIT = 44_035
Core_PRIMARY_CARD_OUTPUT_BYTE_LIMIT = 45_769
Core_PRIMARY_SOURCE_MEAN_CHARS = 68_748.315789
Core_PRIMARY_SOURCE_INPUT_CHAR_LIMIT = 137_497
Core_PRIMARY_ANALYSIS_COUNT = 8
Core_PRIMARY_ANALYSIS_MEAN_CHARS = 6_752.75
Core_PRIMARY_ANALYSIS_OUTPUT_CHAR_LIMIT = 13_506
Core_PRIMARY_ANALYSIS_CONTEXT_MEAN_TOKENS = 2_308.0
Core_PRIMARY_ANALYSIS_CONTEXT_TOKEN_LIMIT = 4_616
Core_PRIMARY_ANALYSIS_PROMPT_MEAN_TOKENS = 3_289.875
Core_PRIMARY_ANALYSIS_PROMPT_TOKEN_LIMIT = 6_580
Core_PRIMARY_ANALYSIS_PROMPT_CHAR_SURROGATE_LIMIT = 26_320
Core_PRIMARY_ANALYSIS_VISIBLE_TOKEN_LIMIT = 4_205
Core_PRIMARY_ANALYSIS_COMPLETION_TOKEN_LIMIT = 12_065
CARD_DISTILLER_SUCCESSOR_SAMPLE_REVISION = (
    "Desktop_CARD_DISTILLER_Core_X2_ROTATING_7_OF_8_PACKS_V1"
)
ANALYSIS_PRIMARY_SUCCESSOR_SAMPLE_REVISION = (
    "Desktop_ANALYSIS_PRIMARY_NO_LENGTH_INVALIDATION_THREE_PANEL_V3"
)
ANALYSIS_PRIMARY_SUBJECT_PROJECTION_REVISION = (
    "Desktop_ANALYSIS_PRIMARY_REDUNDANT_SCOPE_TEXT_PROJECTION_V1"
)
ANALYSIS_PRIMARY_ANCHOR_NORMALIZATION_REVISION = (
    "Desktop_ANALYSIS_PRIMARY_CASE_LOCAL_ANCHOR_ALIAS_V1"
)
ANALYSIS_PRIMARY_STRUCTURAL_REPAIR_PROJECTION_REVISION = (
    "Desktop_ANALYSIS_PRIMARY_BOUNDED_STRUCTURAL_RESIDUAL_V2"
)
ANALYSIS_PRIMARY_SEMANTIC_REPAIR_SHARDING_REVISION = (
    "Desktop_ANALYSIS_PRIMARY_Core_X2_DYNAMIC_RESIDUAL_SHARDS_V1"
)
PRIMARY_MODEL_FAIL_POLICY_REVISION = (
    "Desktop_PRIMARY_MODEL_FAIL_THREE_PANEL_CORROBORATED_MATERIAL_RESIDUAL_V3_"
    "CATEGORY_CALIBRATION"
)
CARD_DISTILLER_REPAIR_ISSUE_GUIDANCE_REVISION = (
    "Desktop_CARD_DISTILLER_GOLD_FREE_ISSUE_GUIDANCE_V1"
)
CARD_DISTILLER_RESIDUAL_MATERIALITY_REVISION = (
    "Desktop_CARD_DISTILLER_D3_IMPLICIT_COMPARATOR_CALIBRATION_V1"
)
_STRUCTURED_PROMPT_SCHEMA_PREAMBLE = (
    "Return exactly one JSON object. Do not wrap it in Markdown. "
    "The object must conform to this JSON Schema:\n"
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


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest().upper()


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


def core_primary_workload_profile() -> dict[str, Any]:
    """Return aggregate-only Core Card/Analysis workload evidence.

    The EXE carries measurements and hashes, not the underlying PDFs, source
    texts, Cards, Analyses, prompts, or provider-visible evidence.  Each hard
    capacity bound is exactly 200% of the matching observed Core mean.
    """

    profile = {
        "schema_version": Core_PRIMARY_WORKLOAD_PROFILE_REVISION,
        "measurement_sha256": Core_PRIMARY_WORKLOAD_MEASUREMENT_SHA256,
        "formal_closeout_sha256": Core_PRIMARY_WORKLOAD_CLOSEOUT_SHA256,
        "multiplier": Core_PRIMARY_WORKLOAD_MULTIPLIER,
        "card_distiller": {
            "actual_card_count": Core_PRIMARY_CARD_COUNT,
            "actual_card_mean_chars": Core_PRIMARY_CARD_MEAN_CHARS,
            "source_mean_chars": Core_PRIMARY_SOURCE_MEAN_CHARS,
            "source_input_chars_ceiling": (
                Core_PRIMARY_SOURCE_INPUT_CHAR_LIMIT
            ),
            "final_card_chars_ceiling": (
                Core_PRIMARY_CARD_OUTPUT_CHAR_LIMIT
            ),
            "final_card_utf8_bytes_ceiling": (
                Core_PRIMARY_CARD_OUTPUT_BYTE_LIMIT
            ),
            "sample_case_count_per_form": 63,
            "sample_pack_count_per_form": 7,
            "sample_rotation_slots": 8,
        },
        "analysis_primary": {
            "actual_analysis_count": Core_PRIMARY_ANALYSIS_COUNT,
            "actual_analysis_mean_chars": Core_PRIMARY_ANALYSIS_MEAN_CHARS,
            "context_pack_mean_tokens": (
                Core_PRIMARY_ANALYSIS_CONTEXT_MEAN_TOKENS
            ),
            "context_pack_tokens_ceiling": (
                Core_PRIMARY_ANALYSIS_CONTEXT_TOKEN_LIMIT
            ),
            "provider_prompt_mean_tokens": (
                Core_PRIMARY_ANALYSIS_PROMPT_MEAN_TOKENS
            ),
            "provider_prompt_tokens_ceiling": (
                Core_PRIMARY_ANALYSIS_PROMPT_TOKEN_LIMIT
            ),
            "prompt_char_surrogate_ceiling": (
                Core_PRIMARY_ANALYSIS_PROMPT_CHAR_SURROGATE_LIMIT
            ),
            "visible_analysis_chars_ceiling": (
                Core_PRIMARY_ANALYSIS_OUTPUT_CHAR_LIMIT
            ),
            "visible_answer_tokens_ceiling": (
                Core_PRIMARY_ANALYSIS_VISIBLE_TOKEN_LIMIT
            ),
            "completion_tokens_including_thinking_ceiling": (
                Core_PRIMARY_ANALYSIS_COMPLETION_TOKEN_LIMIT
            ),
            "sample_case_count_per_form": 6,
            "sample_family_count_per_form": 6,
            "transport_target_count": 6,
            "sample_rotation_slots": 12,
        },
        "capacity_overflow_scorability": Scorability.NOT_ASSESSED.value,
        "cross_category_comparison_forbidden": True,
        "underlying_private_payload_embedded": False,
    }
    profile["profile_sha256"] = _sha256(profile)
    return profile


def _primary_model_fail_projection(
    *,
    category_id: str,
    score: float,
    raw_quality_verdict: str,
    blocking_failures: list[dict[str, Any]],
    guarded_operational_verdict: str,
) -> dict[str, Any]:
    """Separate a bad item from an established whole-model failure.

    A single material residual remains visible and scoreable, but cannot by
    itself label the model FAIL.  The same completed run must contain at least
    two distinct material residual cases.  Aggregate-only or calibration-only
    misses never satisfy that corroboration rule.
    """

    material_case_ids = sorted(
        {
            str(row.get("case_id"))
            for row in blocking_failures
            if isinstance(row, Mapping)
            and isinstance(row.get("case_id"), str)
            and row.get("case_id")
            and row.get("model_fail_materiality") != "CALIBRATION"
            and any(
                isinstance(reason, str)
                and reason
                and reason
                not in {
                    "CASE_SCORE_BELOW_100",
                    "aggregate_score_or_repair_burden",
                    "aggregate_score_hard_gate_or_repair_burden",
                }
                for reason in (row.get("failed_fields") or [])
            )
        }
    )
    calibration_case_ids = sorted(
        {
            str(row.get("case_id"))
            for row in blocking_failures
            if isinstance(row, Mapping)
            and isinstance(row.get("case_id"), str)
            and row.get("case_id")
            and str(row.get("case_id")) not in material_case_ids
        }
    )
    model_fail_established = (
        raw_quality_verdict == "FAIL" and len(material_case_ids) >= 2
    )
    if model_fail_established:
        operational = "REJECT"
        model_fail_verdict = "FAIL"
    elif raw_quality_verdict == "PASS":
        operational = "SUITABLE"
        model_fail_verdict = "PASS"
    elif material_case_ids:
        operational = guarded_operational_verdict
        model_fail_verdict = "NOT_ESTABLISHED"
    elif calibration_case_ids:
        operational = "SUITABLE_WITH_CALIBRATION"
        model_fail_verdict = "NOT_ESTABLISHED"
    else:
        operational = "NOT_RECOMMENDED"
        model_fail_verdict = "NOT_ESTABLISHED"
    return {
        "model_fail_policy_revision": PRIMARY_MODEL_FAIL_POLICY_REVISION,
        "model_fail_verdict": model_fail_verdict,
        "model_fail_established": model_fail_established,
        "model_fail_minimum_distinct_material_cases": 2,
        "material_residual_case_ids": material_case_ids,
        "calibration_residual_case_ids": calibration_case_ids,
        "operational_eligibility_verdict": operational,
        "category_id": category_id,
        "category_local_score": round(float(score), 2),
        "numeric_score_is_not_model_fail_verdict": True,
        "cross_category_comparison_forbidden": True,
        "global_ranking_forbidden": True,
    }


def _card_distiller_residual_materiality_projection(
    *,
    blocking_failures: list[dict[str, Any]],
    golds: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    family_by_case_id: dict[str, str] = {}
    for gold in golds.values():
        cases = gold.get("cases")
        if not isinstance(cases, list):
            continue
        for row in cases:
            if (
                isinstance(row, Mapping)
                and isinstance(row.get("case_id"), str)
                and isinstance(row.get("family"), str)
            ):
                family_by_case_id[str(row["case_id"])] = str(row["family"])
    implicit_comparator_codes = {
        "COMPARATOR_MISSING",
        "COMPARATOR_CONTEXT_INCOMPLETE",
    }
    projected: list[dict[str, Any]] = []
    calibration_case_ids: list[str] = []
    for source in blocking_failures:
        row = deepcopy(source)
        case_id = str(row.get("case_id") or "")
        reasons = {
            str(reason)
            for reason in (row.get("failed_fields") or [])
            if isinstance(reason, str) and reason
        }
        if (
            family_by_case_id.get(case_id) == "D3_MULTI_OCCURRENCE"
            and bool(reasons)
            and reasons.issubset(implicit_comparator_codes)
        ):
            row["model_fail_materiality"] = "CALIBRATION"
            row["model_fail_materiality_reason"] = (
                "D3_TARGET_ONLY_REQUEST_WITH_IMPLICIT_SIBLING_COMPARATOR"
            )
            calibration_case_ids.append(case_id)
        else:
            row["model_fail_materiality"] = "MATERIAL"
        projected.append(row)
    return {
        "schema_version": CARD_DISTILLER_RESIDUAL_MATERIALITY_REVISION,
        "blocking_failures": projected,
        "calibration_override_case_ids": sorted(set(calibration_case_ids)),
        "quality_raw_score_and_verdict_preserved": True,
        "d1_explicit_comparator_errors_remain_material": True,
        "provider_visible": False,
    }


def _effective_structured_prompt_metrics(
    prompt: str,
    response_schema: Mapping[str, Any],
) -> dict[str, int]:
    schema_text = json.dumps(
        response_schema,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    effective = (
        f"{prompt.rstrip()}\n\n"
        f"{_STRUCTURED_PROMPT_SCHEMA_PREAMBLE}{schema_text}"
    )
    return {
        "prompt_chars": len(prompt),
        "response_schema_chars": len(schema_text),
        "effective_prompt_chars": len(effective),
        "effective_prompt_utf8_bytes": len(effective.encode("utf-8")),
    }


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


def _serialized_request(request: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(request),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _card_distiller_candidate_output_metrics(
    response: Mapping[str, Any],
) -> dict[str, Any]:
    """Measure Card Distiller artifacts without conflating pipeline surfaces.

    Quality deliberately asks for both a fact-map intermediary and a Card
    projection. Core's measured final-Card ceiling is therefore applied to
    each logical artifact, not to the byte sum of two independently consumed
    artifacts. The combined size remains evidence-only so a naturally verbose
    but complete answer cannot be converted into an execution failure.
    """

    artifacts: dict[str, dict[str, int]] = {}
    for artifact in ("fact_map", "card_projection"):
        serialized = _serialized_request({artifact: response.get(artifact)})
        artifacts[artifact] = {
            "chars": len(serialized),
            "utf8_bytes": len(serialized.encode("utf-8")),
        }
    combined = _serialized_request(response)
    return {
        "artifacts": artifacts,
        "combined_diagnostic": {
            "chars": len(combined),
            "utf8_bytes": len(combined.encode("utf-8")),
        },
        "capacity_limit_surface": (
            "EACH_LOGICAL_ARTIFACT_PER_FORM_NOT_CUMULATIVE"
        ),
        "combined_metric_quality_semantics": (
            "DIAGNOSTIC_ONLY_NOT_MODEL_FAIL"
        ),
    }


def _card_distiller_candidate_artifact_limit_exceeded(
    metrics: Mapping[str, Any],
) -> bool:
    """Return a Core workload-reference diagnostic, never a fail gate."""

    artifacts = metrics.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError("WORKFLOW_MODEL_EXAM_CARD_OUTPUT_METRICS_INVALID")
    for artifact in ("fact_map", "card_projection"):
        row = artifacts.get(artifact)
        if not isinstance(row, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_CARD_OUTPUT_METRICS_INVALID")
        chars = row.get("chars")
        utf8_bytes = row.get("utf8_bytes")
        if (
            isinstance(chars, bool)
            or not isinstance(chars, int)
            or isinstance(utf8_bytes, bool)
            or not isinstance(utf8_bytes, int)
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CARD_OUTPUT_METRICS_INVALID")
        if (
            chars > Core_PRIMARY_CARD_OUTPUT_CHAR_LIMIT
            or utf8_bytes > Core_PRIMARY_CARD_OUTPUT_BYTE_LIMIT
        ):
            return True
    return False


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


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_json_create_only(path: Path, value: Any) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_bytes(value) + b"\n"
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
    return {
        "path": str(path.resolve()),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest().upper(),
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"WORKFLOW_MODEL_EXAM_JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _distiller_call_bound(forms: Mapping[str, Any], profile_kind: str) -> int:
    fact_size = 12 if profile_kind == 'LOCAL' else 36
    shards = sum(math.ceil(len(form['cases']) / fact_size)
                 + math.ceil(len(form.get('packs', [])) / 4) for form in forms.values())
    final_repair = (sum(math.ceil(len(form['cases']) / 12) for form in forms.values())
                    if profile_kind == 'LOCAL' else 1)
    return shards * (1 + MAX_STRUCTURAL_REPAIR_ROUNDS) + final_repair


def _public_forms(root: Path) -> dict[str, Any]:
    # Only the already verified public question forms; never inspect Gold.
    return {key: _load_json(root / 'forms' / f'form_{key}.json') for key in ('A', 'B')}


def _compact(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _not_available(reason: str) -> dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA,
        "status": "NOT_AVAILABLE",
        "mode": "NONE",
        "reason": reason,
        "external_network_calls": 0,
        "provider_calls": 0,
        "external_model_calls": 0,
    }


def _not_run(reason: str, *, requested_model: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA,
        "kind": "WORKFLOW_NODE",
        "status": "NOT_RUN",
        "score": None,
        "reason": reason,
        "requested_model": requested_model,
        "returned_model": None,
        "duration_ms": 0,
        "external_network_calls": 0,
        "provider_calls": 0,
        "external_model_calls": 0,
        "external_process_launches": 0,
        "reference_regression_only": True,
        "qualification_eligible": False,
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
            for item in exam._reviewer_exam_wrong_details(original_score)
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
                exam._reviewer_exam_issue_metadata(
                    original_details[case_id]
                )
            )
            issue_categories.update(issues)
            severity_categories.add(severity)
            original_failed_fields_by_case[case_id] = set(failed_fields)
            repair_issue_priority_by_case[case_id] = severity
        form_case_count = len(exam.form_cases(form)[1])
        form_case_counts[form_id] = form_case_count
        initial_penalty = exam._reviewer_exam_form_penalty(
            form_case_count=form_case_count,
            wrong_case_count=len(target_ids),
            issue_categories=issue_categories,
            severity_categories=severity_categories,
        )
        initial_penalties[form_id] = initial_penalty

        post_wrong_details = exam._reviewer_exam_wrong_details(post)
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
                exam._reviewer_exam_issue_metadata(detail)
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
        residual_disposition_penalty = exam._reviewer_exam_form_penalty(
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


class QualityCardDistillerExamExecutor:
    """Desktop adapter over the frozen Quality Card Distiller exam contracts.

    The adapter deliberately exposes a reference-regression score only.  It is
    not a blind holdout and cannot grant production qualification.
    """

    SEMANTIC_REPAIR_ROUND_LIMIT = 1
    EXAM_CATEGORY_ID = CARD_DISTILLER_EXAM_CATEGORY_ID

    def __init__(
        self,
        *,
        scratch_root: Path,
        quality_package: str,
        reference_pack_root: Path,
        pricing_profile_path: Path | None = None,
    ) -> None:
        if not isinstance(quality_package, str) or not SAFE_PACKAGE.fullmatch(
            quality_package
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_Quality_PACKAGE_INVALID")
        self._scratch_root = Path(scratch_root).resolve()
        self._quality_package = quality_package
        self._reference_pack_root = Path(reference_pack_root).resolve()
        self._pricing_profile_path = (
            Path(pricing_profile_path).resolve()
            if pricing_profile_path is not None
            else self._reference_pack_root.parent
            / "deepseek_flash_to_pro_profile.public.json"
        )
        self._modules: dict[str, Any] | None = None

    def _quality(self) -> dict[str, Any]:
        if self._modules is None:
            names = {
                "protocol": "card_distiller_exam_protocol",
                "assets": "card_distiller_exam_assets",
                "pack": "card_distiller_exam_pack",
                "repair": "card_distiller_exam_repair",
                "reference": "reference_exam_pack",
            }
            self._modules = {
                key: importlib.import_module(f"{self._quality_package}.{suffix}")
                for key, suffix in names.items()
            }
        return self._modules

    def _pricing_profile(self) -> dict[str, Any]:
        profile = _load_json(self._pricing_profile_path)
        if (
            profile.get("schema_version")
            != "model_evaluation-model_evaluation-card-distiller-flash-to-pro-public-profile-v1"
            or profile.get("suite") != "card_distiller_primary"
            or profile.get("credential_value_included") is not False
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_PRICING_PROFILE_INVALID")
        low = profile.get("low_cost")
        high = profile.get("high_cost")
        exception = profile.get("single_run_cost_ratio_exception")
        if (
            not isinstance(low, Mapping)
            or not isinstance(high, Mapping)
            or low.get("model_id") != "deepseek-v4-flash"
            or high.get("model_id") != "deepseek-v4-pro"
            or low.get("request_max_output_tokens") != MAX_OUTPUT_TOKENS
            or high.get("request_max_output_tokens") != MAX_OUTPUT_TOKENS
            or not isinstance(exception, Mapping)
            or exception.get("reusable") is not False
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_PRICING_PROFILE_INVALID")
        return profile

    @staticmethod
    def _eligible_target(target: Mapping[str, Any], *, low_cost: bool) -> bool:
        expected_model = "deepseek-v4-flash" if low_cost else "deepseek-v4-pro"
        expected_tier = "flash" if low_cost else "pro"
        return (
            target.get("kind") == "API"
            and re.fullmatch(
                r"deepseek(?:apikey[a-z0-9]*)?", _compact(target.get("provider"))
            )
            is not None
            and target.get("model_name") == expected_model
            and target.get("thinking") is True
            and target.get("tier") == expected_tier
            and target.get("connection_status") == "AVAILABLE"
            and isinstance(target.get("config_id"), str)
            and bool(target.get("config_id"))
        )

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        if node.get("node_id") != "card_distill":
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        if not self._eligible_target(target, low_cost=False):
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        low_cost_target = context.get("low_cost_target")
        if not isinstance(low_cost_target, Mapping) or not self._eligible_target(
            low_cost_target, low_cost=True
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_LOW_COST_PROFILE_UNAVAILABLE")
        try:
            modules = self._quality()
            verification = modules["pack"].verify_reference_pack(
                self._reference_pack_root
            )
            pricing = self._pricing_profile()
        except (ImportError, OSError, ValueError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_ASSETS_INVALID")
        if (
            verification.get("status") != "PASS"
            or verification.get("reference_regression_only") is not True
            or verification.get("qualification_eligible") is not False
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_PACK_NOT_ELIGIBLE")
        low = pricing["low_cost"]
        high = pricing["high_cost"]
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA,
            "status": "READY",
            "mode": "LIVE_Quality_CARD_DISTILLER_REFERENCE_REGRESSION",
            "reason": "Quality_REFERENCE_REGRESSION_READY",
            "node_id": "card_distill",
            "executor_ref": EXECUTOR_REF,
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": verification["pack_id"],
            "reference_pack_revision": verification["pack_revision"],
            "reference_pack_sha256": verification["pack_fingerprint"],
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "low_cost": {
                "profile_ref": low_cost_target["config_id"],
                "model_name": low_cost_target["model_name"],
                "tier": low_cost_target["tier"],
                "forms": ["G", "H"],
                "budget_cap_cny": float(low["budget_cap_cny"]),
            },
            "high_cost": {
                "profile_ref": target["config_id"],
                "model_name": target["model_name"],
                "tier": target["tier"],
                "forms": ["E", "F"],
                "budget_cap_cny": float(high["budget_cap_cny"]),
            },
            "maximum_provider_calls": _distiller_call_bound(_public_forms(self._reference_pack_root), 'API') * 2,
            "budget_cap_cny": float(low["budget_cap_cny"])
            + float(high["budget_cap_cny"]),
            "low_to_high_worst_cost_ratio": float(
                pricing["low_to_high_worst_cost_ratio"]
            ),
            "low_to_high_ratio_limit": float(pricing["low_to_high_ratio_limit"]),
            "requires_non_reusable_ratio_exception": True,
            "pricing_snapshot_date": pricing["profile_snapshot_date"],
            "pricing_profile_sha256": _file_sha256(self._pricing_profile_path),
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
            "acknowledged_non_reusable_ratio_exception",
        }:
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_INVALID"
        if authorization.get("schema_version") != AUTHORIZATION_SCHEMA:
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_INVALID"
        if authorization.get("authorized") is not True:
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_REQUIRED"
        if authorization.get("plan_sha256") != plan.get("plan_sha256"):
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_MISMATCH"
        cap = authorization.get("cost_cap_cny")
        if isinstance(cap, bool) or not isinstance(cap, (int, float)):
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_INVALID"
        if float(cap) != float(plan["budget_cap_cny"]):
            return "WORKFLOW_MODEL_EXAM_COST_CAP_MISMATCH"
        if authorization.get("acknowledged_non_reusable_ratio_exception") is not True:
            return "WORKFLOW_MODEL_EXAM_RATIO_EXCEPTION_REQUIRED"
        return None

    @staticmethod
    def _validate_call_result(
        result: Mapping[str, Any], requested_model: str, profile_kind: str
    ) -> Mapping[str, Any]:
        is_api = profile_kind == "API"
        is_cli = profile_kind == "CLI"
        if (
            result.get("schema_version") != "SettingsStructuredChatRunnerResult-v1"
            or result.get("status") != "PASS"
            or result.get("requested_model") != requested_model
            or not matches_result_model(result, requested_model)
            or not isinstance(result.get("response"), Mapping)
            or result.get("provider_calls") != (1 if is_api else 0)
            or result.get("external_model_calls") != 1
            or result.get("external_network_calls") != (1 if is_api else 0)
            or result.get("external_process_launches") != (1 if is_cli else 0)
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
            or not isinstance(receipt.get("route"), str)
            or not receipt.get("route")
            or not isinstance(receipt.get("region"), str)
            or not receipt.get("region")
            or not isinstance(receipt.get("egress"), str)
            or not receipt.get("egress")
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CALL_RECEIPT_INVALID")
        usage = receipt.get("token_usage")
        if not isinstance(usage, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_TOKEN_EVIDENCE_MISSING")
        for field in ("prompt_tokens", "completion_tokens"):
            value = usage.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("WORKFLOW_MODEL_EXAM_TOKEN_EVIDENCE_MISSING")
        actual = receipt.get("actual_cost")
        estimate = receipt.get("estimated_cost_cny")
        if is_api:
            if actual is None and (
                isinstance(estimate, bool)
                or not isinstance(estimate, (int, float))
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_COST_EVIDENCE_MISSING")
        elif is_cli:
            if (
                actual is not None
                or estimate is not None
                or receipt.get("cost_evidence")
                != "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE"
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_CLI_COST_EVIDENCE_INVALID")
        elif (
            actual is not None
            or estimate != 0.0
            or receipt.get("cost_evidence")
            != "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_LOCAL_COST_EVIDENCE_INVALID")
        return receipt

    def _structured_output_policy(
        self,
        target: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int | None, str]:
        """Legacy Card Distiller contract kept for the frozen executor."""

        return deepcopy(dict(target)), MAX_OUTPUT_TOKENS, "EXPLICIT"

    def _request_timeout_seconds(self, profile_kind: str) -> int:
        del profile_kind
        return REQUEST_TIMEOUT_SECONDS

    def _provider_call(
        self,
        *,
        run_id: str,
        stage_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        prompt: str,
        response_schema: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        call_kind: str,
    ) -> dict[str, Any]:
        stage_call_cap = int(
            state.get("stage_call_cap", MAX_CALLS_PER_STAGE)
        )
        if state["stage_calls"] >= stage_call_cap:
            raise ValueError("WORKFLOW_MODEL_EXAM_STAGE_CALL_LIMIT_REACHED")
        call_number = state["stage_calls"] + 1
        call_id = f"call-{call_number:02d}"
        model_name = str(target["model_name"])
        profile_kind = str(target["kind"])
        is_api = profile_kind == "API"
        is_local = profile_kind == "LOCAL"
        request_timeout_seconds = self._request_timeout_seconds(profile_kind)
        if not 30 <= request_timeout_seconds <= 3_600:
            raise ValueError("WORKFLOW_MODEL_EXAM_TIMEOUT_PROFILE_INVALID")
        (
            model_binding,
            request_max_output_tokens,
            output_limit_mode,
        ) = self._structured_output_policy(target)
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest().upper()
        limits = resolve_call_limits(profile_kind=profile_kind, service=target, model=target)
        if output_limit_mode == "PROVIDER_DEFAULT":
            limits["max_output_tokens"] = None
            limits["wire_output_policy"] = "PROVIDER_DEFAULT"
        claim = {
            "schema_version": "WorkflowModelExamPreSendClaim-v1",
            "run_id": run_id,
            "call_id": call_id,
            "call_kind": call_kind,
            "profile_ref": target["config_id"],
            "requested_model": model_name,
            "profile_kind": profile_kind,
            "route": (
                "EXISTING_SETTINGS_API_TRANSPORT"
                if is_api
                else ("OLLAMA_LOOPBACK" if is_local else "CODEX_CLI_SUBSCRIPTION")
            ),
            "region": (
                "PROVIDER_MANAGED_UNDISCLOSED"
                if is_api
                else ("LOCAL_MACHINE" if is_local else "OPENAI_MANAGED_UNDISCLOSED")
            ),
            "egress": (
                "PROVIDER_API"
                if is_api
                else ("LOOPBACK_ONLY" if is_local else "CODEX_CLI")
            ),
            "behavior_sha256": prompt_sha256,
            "response_schema_sha256": _sha256(response_schema),
            "max_output_tokens": limits['max_output_tokens'],
            "timeout_seconds": limits['timeout_seconds'],
            "resource_limits": limits,
            "max_output_tokens_mode": output_limit_mode,
            "pricing_profile_sha256": (
                _file_sha256(self._pricing_profile_path) if is_api else None
            ),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_create_only(stage_root / "claims" / f"{call_id}.json", claim)
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
                    "purpose": "workflow_model_exam",
                    "requested_model": model_name,
                    "returned_model": result.get("returned_model"),
                    "token_usage": None,
                    "actual_cost": None,
                    "estimated_cost_cny": None,
                    "cost_evidence": "ATTEMPT_EVIDENCE_UNAVAILABLE",
                }
            _write_json_create_only(
                stage_root / "receipts" / f"{call_id}.json",
                {
                    **deepcopy(dict(receipt)),
                    "run_id": run_id,
                    "call_id": call_id,
                    "call_kind": call_kind,
                    "profile_ref": target["config_id"],
                    "pre_send_claim_sha256": _sha256(claim),
                    "provider_output_sha256": None,
                },
            )
            state["stage_calls"] += 1
            state["provider_calls"] += int(result.get("provider_calls") or 0)
            state["external_model_calls"] += int(
                result.get("external_model_calls") or 0
            )
            state["external_network_calls"] += int(
                result.get("external_network_calls") or 0
            )
            state["external_process_launches"] += int(
                result.get("external_process_launches") or 0
            )
            estimate = receipt.get("estimated_cost_cny")
            if isinstance(estimate, (int, float)) and not isinstance(estimate, bool):
                state["estimated_cost_cny"] += float(estimate)
            reason = result.get("reason")
            raise ValueError(
                reason if isinstance(reason, str) and reason else "WORKFLOW_MODEL_EXAM_CALL_FAILED"
            )
        receipt = self._validate_call_result(result, model_name, profile_kind)
        response = deepcopy(dict(result["response"]))
        output_receipt = _write_json_create_only(
            stage_root / "provider_outputs" / f"{call_id}.json", response
        )
        receipt_payload = {
            **deepcopy(dict(receipt)),
            "run_id": run_id,
            "call_id": call_id,
            "call_kind": call_kind,
            "profile_ref": target["config_id"],
            "pre_send_claim_sha256": _sha256(claim),
            "provider_output_sha256": output_receipt["sha256"],
        }
        _write_json_create_only(
            stage_root / "receipts" / f"{call_id}.json", receipt_payload
        )
        state["stage_calls"] += 1
        state["provider_calls"] += int(result["provider_calls"])
        state["external_model_calls"] += int(result["external_model_calls"])
        state["external_network_calls"] += int(result["external_network_calls"])
        state["external_process_launches"] += int(
            result["external_process_launches"]
        )
        state["estimated_cost_cny"] += float(
            receipt.get("estimated_cost_cny") or 0.0
        )
        return response

    def _run_shard(
        self,
        *,
        modules: Mapping[str, Any],
        run_id: str,
        stage_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        form: Mapping[str, Any],
        artifact: str,
        target_ids: list[str],
        schema: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
    ) -> dict[str, Any]:
        protocol = modules["protocol"]
        prompt = protocol.build_artifact_prompt(
            dict(form), artifact, target_ids=target_ids, schema=dict(schema)
        )
        response = self._provider_call(
            run_id=run_id,
            stage_root=stage_root,
            state=state,
            target=target,
            prompt=prompt,
            response_schema=schema,
            structured_chat=structured_chat,
            call_kind=f"INITIAL_{artifact.upper()}",
        )
        audit = protocol.audit_artifact(
            form=dict(form),
            response=response,
            schema=dict(schema),
            artifact=artifact,
            target_ids=target_ids,
        )
        audit_name = (
            f"{artifact}-{target_ids[0]}-{target_ids[-1]}-initial.json"
        )
        _write_json_create_only(stage_root / "audits" / audit_name, audit)
        if audit["status"] == "PASS":
            return response
        source_collection = "cases" if artifact == "fact_map" else "packs"
        source_id_field = "case_id" if artifact == "fact_map" else "pack_id"
        target_source = [
            deepcopy(row)
            for row in form.get(source_collection, [])
            if isinstance(row, Mapping) and row.get(source_id_field) in target_ids
        ]
        partition_fields = [
            "research_question",
            "research_object",
            "method",
            "key_results",
            "author_conclusion",
            "boundary_conditions",
        ]
        artifact_contract = (
            {
                "row_identity": "case_id",
                "exact_target_order": target_ids,
                "rule": "Return exactly one fact_map row per target case_id.",
            }
            if artifact == "fact_map"
            else {
                "row_identity": "pack_id",
                "exact_target_order": target_ids,
                "partition_fields": partition_fields,
                "rules": [
                    "For each pack, every source case_id must occur exactly once across partition_fields[].case_ids.",
                    "No case_id may occur in two partition fields; move it to the single best matching field.",
                    "The union of partition_fields[].case_ids must equal that pack's source case_ids exactly.",
                    "Partition fields are buckets, not one-item slots: multiple case_ids in one field are required when the source pack has more cases than partition fields.",
                    "Before returning, verify that both total assignment count and unique assignment count equal the exact source case count for every pack.",
                    "key_data_case_ids is a separate summary list and does not relax the exact partition rule.",
                    "Use only chunk_id values present in the same source pack as anchor_ids.",
                ],
            }
        )

        def partition_diagnostics(current: Mapping[str, Any]) -> list[dict[str, Any]]:
            if artifact != "card_projection":
                return []
            source_by_id = {
                str(row.get("pack_id")): row
                for row in target_source
                if isinstance(row, Mapping)
            }
            current_rows = current.get("card_projection")
            current_by_id = (
                {
                    str(row.get("pack_id")): row
                    for row in current_rows
                    if isinstance(row, Mapping)
                }
                if isinstance(current_rows, list)
                else {}
            )
            diagnostics: list[dict[str, Any]] = []
            for pack_id in target_ids:
                source = source_by_id.get(pack_id, {})
                expected = [
                    str(case_id) for case_id in source.get("case_ids", [])
                    if isinstance(case_id, str)
                ]
                row = current_by_id.get(pack_id, {})
                observed_by_field: dict[str, list[str]] = {}
                assigned: list[str] = []
                for field in partition_fields:
                    field_value = row.get(field)
                    case_ids = (
                        field_value.get("case_ids", [])
                        if isinstance(field_value, Mapping)
                        else []
                    )
                    normalized = [
                        str(case_id) for case_id in case_ids
                        if isinstance(case_id, str)
                    ]
                    observed_by_field[field] = normalized
                    assigned.extend(normalized)
                counts = {
                    case_id: assigned.count(case_id) for case_id in set(assigned)
                }
                diagnostics.append(
                    {
                        "pack_id": pack_id,
                        "exact_source_case_ids": expected,
                        "expected_assignment_count": len(expected),
                        "observed_assignment_count": len(assigned),
                        "observed_unique_count": len(set(assigned)),
                        "missing_case_ids": [
                            case_id for case_id in expected if case_id not in assigned
                        ],
                        "duplicate_case_ids": sorted(
                            case_id for case_id, count in counts.items() if count > 1
                        ),
                        "extra_case_ids": sorted(set(assigned) - set(expected)),
                        "current_partition": observed_by_field,
                    }
                )
            return diagnostics

        def partition_decision_targets(
            diagnostics: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            targets: list[dict[str, Any]] = []
            for diagnostic in diagnostics:
                pack_id = str(diagnostic["pack_id"])
                current_partition = diagnostic["current_partition"]
                missing = set(diagnostic["missing_case_ids"])
                duplicates = set(diagnostic["duplicate_case_ids"])
                for case_id in diagnostic["exact_source_case_ids"]:
                    if case_id not in missing and case_id not in duplicates:
                        continue
                    current_fields = [
                        field for field in partition_fields
                        if case_id in current_partition[field]
                    ]
                    targets.append(
                        {
                            "pack_id": pack_id,
                            "case_id": case_id,
                            "operation": (
                                "ASSIGN_MISSING"
                                if case_id in missing
                                else "KEEP_ONE_REMOVE_DUPLICATES"
                            ),
                            "allowed_fields": (
                                partition_fields
                                if case_id in missing
                                else current_fields
                            ),
                        }
                    )
            return targets

        def partition_patch_schema(
            decision_targets: list[dict[str, Any]],
        ) -> dict[str, Any]:
            case_ids = [str(row["case_id"]) for row in decision_targets]
            return {
                "type": "object",
                "additionalProperties": False,
                "required": ["decisions"],
                "properties": {
                    "decisions": {
                        "type": "array",
                        "minItems": len(decision_targets),
                        "maxItems": len(decision_targets),
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["pack_id", "case_id", "field"],
                            "properties": {
                                "pack_id": {
                                    "type": "string",
                                    "enum": target_ids,
                                },
                                "case_id": {
                                    "type": "string",
                                    "enum": case_ids,
                                },
                                "field": {
                                    "type": "string",
                                    "enum": partition_fields,
                                },
                            },
                        },
                    }
                },
            }

        def merge_partition_patch(
            current: Mapping[str, Any],
            patch: Mapping[str, Any],
            diagnostics: list[dict[str, Any]],
            decision_targets: list[dict[str, Any]],
        ) -> dict[str, Any]:
            decisions = patch.get("decisions")
            if not isinstance(decisions, list):
                raise ValueError("WORKFLOW_MODEL_EXAM_PARTITION_DECISIONS_REQUIRED")
            expected_pairs = [
                (str(row["pack_id"]), str(row["case_id"]))
                for row in decision_targets
            ]
            observed_pairs = [
                (
                    str(row.get("pack_id")),
                    str(row.get("case_id")),
                )
                for row in decisions
                if isinstance(row, Mapping)
            ]
            if observed_pairs != expected_pairs or len(decisions) != len(
                decision_targets
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_PARTITION_DECISION_EXACT_SET_MISMATCH"
                )
            allowed_by_pair = {
                (str(row["pack_id"]), str(row["case_id"])): set(
                    row["allowed_fields"]
                )
                for row in decision_targets
            }
            selected: dict[tuple[str, str], str] = {}
            for decision in decisions:
                if not isinstance(decision, Mapping):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_PARTITION_DECISION_MAPPING_REQUIRED"
                    )
                pair = (
                    str(decision.get("pack_id")),
                    str(decision.get("case_id")),
                )
                field = decision.get("field")
                if (
                    not isinstance(field, str)
                    or field not in allowed_by_pair[pair]
                ):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_PARTITION_DECISION_FIELD_INVALID"
                    )
                selected[pair] = field
            merged = deepcopy(dict(current))
            rows = merged.get("card_projection")
            if not isinstance(rows, list):
                raise ValueError("WORKFLOW_MODEL_EXAM_PARTITION_ROWS_REQUIRED")
            diagnostic_by_id = {
                str(row["pack_id"]): row for row in diagnostics
            }
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_PARTITION_ROW_MAPPING_REQUIRED"
                    )
                pack_id = str(row.get("pack_id"))
                diagnostic = diagnostic_by_id.get(pack_id)
                if diagnostic is None:
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_PARTITION_DIAGNOSTIC_MISSING"
                    )
                exact_source = set(diagnostic["exact_source_case_ids"])
                for field in partition_fields:
                    field_value = row.get(field)
                    if not isinstance(field_value, dict) or not isinstance(
                        field_value.get("case_ids"), list
                    ):
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_PARTITION_FIELD_INVALID"
                        )
                    field_value["case_ids"] = [
                        case_id for case_id in field_value["case_ids"]
                        if case_id in exact_source
                    ]
                for decision_target in decision_targets:
                    if decision_target["pack_id"] != pack_id:
                        continue
                    case_id = str(decision_target["case_id"])
                    for field in partition_fields:
                        row[field]["case_ids"] = [
                            value for value in row[field]["case_ids"]
                            if value != case_id
                        ]
                    row[selected[(pack_id, case_id)]]["case_ids"].append(case_id)
            return merged

        current_audit = audit
        for repair_round in range(1, MAX_STRUCTURAL_REPAIR_ROUNDS + 1):
            diagnostics = partition_diagnostics(response)
            decision_targets = partition_decision_targets(diagnostics)
            use_directed_partition_patch = (
                artifact == "card_projection"
                and repair_round == MAX_STRUCTURAL_REPAIR_ROUNDS
                and bool(decision_targets)
            )
            if use_directed_partition_patch:
                patch_schema = partition_patch_schema(decision_targets)
                repair_prompt = json.dumps(
                    {
                        "instruction": (
                            "Return only the requested partition decisions in exact order. "
                            "For each duplicate case choose the one current semantic field to "
                            "keep; for each missing case choose the best semantic field to add. "
                            "Do not rewrite the projection and do not omit a decision."
                        ),
                        "repair_round": repair_round,
                        "artifact": artifact,
                        "decision_targets": decision_targets,
                        "partition_diagnostics": diagnostics,
                        "target_source": target_source,
                        "current_response": response,
                        "output_schema": patch_schema,
                        "source_task_prompt_sha256": hashlib.sha256(
                            prompt.encode("utf-8")
                        ).hexdigest().upper(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                patch_response = self._provider_call(
                    run_id=run_id,
                    stage_root=stage_root,
                    state=state,
                    target=target,
                    prompt=repair_prompt,
                    response_schema=patch_schema,
                    structured_chat=structured_chat,
                    call_kind=(
                        "DIRECTED_PARTITION_PATCH_CARD_PROJECTION_ROUND_2"
                    ),
                )
                response = merge_partition_patch(
                    response,
                    patch_response,
                    diagnostics,
                    decision_targets,
                )
                repaired_audit = protocol.audit_artifact(
                    form=dict(form),
                    response=response,
                    schema=dict(schema),
                    artifact=artifact,
                    target_ids=target_ids,
                )
                _write_json_create_only(
                    stage_root
                    / "partition_patches"
                    / (
                        f"{artifact}-{target_ids[0]}-{target_ids[-1]}-"
                        "round-02.json"
                    ),
                    {
                        "schema_version": "WorkflowModelExamPartitionPatch-v1",
                        "decision_targets": decision_targets,
                        "patch_response": patch_response,
                        "merged_response_sha256": _sha256(response),
                        "audit": repaired_audit,
                    },
                )
            else:
                repair_prompt = json.dumps(
                    {
                        "instruction": (
                            "Repair all and only this failed Card Distiller shard. Return "
                            "the complete exact target set in the original order and no other rows. "
                            "Do not repeat the current response unchanged; correct every listed defect. "
                            "For card_projection, do not stop after deduplicating: reassign every "
                            "missing source case_id so each exact source case appears once."
                        ),
                        "repair_round": repair_round,
                        "maximum_repair_rounds": MAX_STRUCTURAL_REPAIR_ROUNDS,
                        "artifact": artifact,
                        "target_ids": target_ids,
                        "defects": current_audit["defects"],
                        "artifact_contract": artifact_contract,
                        "partition_diagnostics": diagnostics,
                        "target_source": target_source,
                        "current_response": response,
                        "output_schema": schema,
                        "source_task_prompt_sha256": hashlib.sha256(
                            prompt.encode("utf-8")
                        ).hexdigest().upper(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                response = self._provider_call(
                    run_id=run_id,
                    stage_root=stage_root,
                    state=state,
                    target=target,
                    prompt=repair_prompt,
                    response_schema=schema,
                    structured_chat=structured_chat,
                    call_kind=(
                        f"STRUCTURAL_REPAIR_{artifact.upper()}_ROUND_{repair_round}"
                    ),
                )
                repaired_audit = protocol.audit_artifact(
                    form=dict(form),
                    response=response,
                    schema=dict(schema),
                    artifact=artifact,
                    target_ids=target_ids,
                )
            repaired_audit_name = (
                f"{artifact}-{target_ids[0]}-{target_ids[-1]}-repaired.json"
                if repair_round == 1
                else (
                    f"{artifact}-{target_ids[0]}-{target_ids[-1]}-"
                    f"repair-{repair_round:02d}.json"
                )
            )
            _write_json_create_only(
                stage_root / "audits" / repaired_audit_name, repaired_audit
            )
            if repaired_audit["status"] == "PASS":
                return response
            current_audit = repaired_audit
        raise ValueError("WORKFLOW_MODEL_EXAM_SHARD_CONTRACT_UNCLOSED")

    @staticmethod
    def _project_directed_repair_schema(
        repair_schema: Mapping[str, Any],
        *,
        form_id: str,
        case_ids: list[str],
    ) -> dict[str, Any]:
        form_key = f"form_{form_id}"
        properties = repair_schema.get("properties")
        if (
            form_id not in {"A", "B"}
            or not case_ids
            or len(case_ids) != len(set(case_ids))
            or not isinstance(properties, Mapping)
            or not isinstance(properties.get(form_key), Mapping)
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_SHARD_SCHEMA_INVALID")
        projected = deepcopy(dict(repair_schema))
        projected["required"] = [form_key]
        projected["properties"] = {
            form_key: deepcopy(dict(properties[form_key]))
        }
        try:
            fact_map = projected["properties"][form_key]["properties"]["fact_map"]
            case_id_schema = fact_map["items"]["properties"]["case_id"]
        except (KeyError, TypeError) as error:
            raise ValueError(
                "WORKFLOW_MODEL_EXAM_REPAIR_SHARD_SCHEMA_INVALID"
            ) from error
        fact_map["minItems"] = len(case_ids)
        fact_map["maxItems"] = len(case_ids)
        case_id_schema["enum"] = list(case_ids)
        return projected

    def _run_exam_directed_repair(
        self,
        *,
        run_id: str,
        stage_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        package: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        call_kind: str = "WHOLE_EXAM_DIRECTED_REPAIR",
    ) -> dict[str, Any]:
        repair_schema = package.get("repair_schema")
        contract = package.get("contract")
        repair_targets = package.get("repair_targets")
        prompt = package.get("prompt")
        if (
            not isinstance(repair_schema, Mapping)
            or not isinstance(contract, Mapping)
            or not isinstance(repair_targets, Mapping)
            or not isinstance(prompt, str)
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_PACKAGE_INVALID")
        exact_sets = contract.get("wrong_item_exact_set")
        if not isinstance(exact_sets, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_PACKAGE_INVALID")
        total_wrong = sum(
            len(case_ids)
            for case_ids in exact_sets.values()
            if isinstance(case_ids, list)
        )
        active_form_ids = [
            form_id
            for form_id in ("A", "B")
            if isinstance(exact_sets.get(form_id), list)
            and bool(exact_sets[form_id])
        ]
        is_local = target.get("kind") == "LOCAL"
        shard_size = 12 if is_local else 36
        try:
            parent_prompt = json.loads(prompt)
        except json.JSONDecodeError as error:
            raise ValueError(
                "WORKFLOW_MODEL_EXAM_REPAIR_PROMPT_INVALID"
            ) from error
        if not isinstance(parent_prompt, dict):
            raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_PROMPT_INVALID")
        if len(active_form_ids) == 1 and total_wrong <= shard_size:
            guided_prompt = self._card_distiller_repair_issue_guidance(
                parent_prompt, repair_targets
            )
            return self._provider_call(
                run_id=run_id,
                stage_root=stage_root,
                state=state,
                target=target,
                prompt=json.dumps(
                    guided_prompt,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                response_schema=repair_schema,
                structured_chat=structured_chat,
                call_kind=call_kind,
            )

        merged: dict[str, Any] = {}
        shard_receipts: list[dict[str, Any]] = []
        for form_id in ("A", "B"):
            case_ids = exact_sets.get(form_id)
            targets = repair_targets.get(form_id)
            if not isinstance(case_ids, list) or not isinstance(targets, list):
                raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_PACKAGE_INVALID")
            if not case_ids:
                continue
            if (
                any(not isinstance(case_id, str) or not case_id for case_id in case_ids)
                or len(case_ids) != len(set(case_ids))
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_EXACT_SET_INVALID")
            target_by_case: dict[str, Mapping[str, Any]] = {}
            for row in targets:
                if not isinstance(row, Mapping):
                    raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_TARGET_INVALID")
                case_id = row.get("case_id")
                if not isinstance(case_id, str) or case_id in target_by_case:
                    raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_TARGET_INVALID")
                target_by_case[case_id] = row
            if list(target_by_case) != case_ids:
                raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_TARGET_ORDER_INVALID")

            chunks = [
                case_ids[start : start + shard_size]
                for start in range(0, len(case_ids), shard_size)
            ]
            merged_rows: list[Any] = []
            for chunk_index, chunk_case_ids in enumerate(chunks, start=1):
                chunk_schema = self._project_directed_repair_schema(
                    repair_schema,
                    form_id=form_id,
                    case_ids=chunk_case_ids,
                )
                chunk_prompt = deepcopy(parent_prompt)
                chunk_prompt["wrong_item_exact_set"] = {
                    form_id: list(chunk_case_ids)
                }
                chunk_prompt["repair_targets"] = {
                    form_id: [
                        deepcopy(dict(target_by_case[case_id]))
                        for case_id in chunk_case_ids
                    ]
                }
                chunk_prompt["output_schema"] = chunk_schema
                chunk_prompt = self._card_distiller_repair_issue_guidance(
                    chunk_prompt,
                    chunk_prompt["repair_targets"],
                )
                chunk_prompt["transport_shard"] = {
                    "schema_version": "WorkflowModelExamRepairTransportShard-v1",
                    "parent_contract_sha256": contract.get("contract_sha256"),
                    "parent_package_sha256": package.get("package_sha256"),
                    "form_id": form_id,
                    "chunk_index": chunk_index,
                    "chunk_count": len(chunks),
                    "case_ids": list(chunk_case_ids),
                    "case_ids_sha256": _sha256(chunk_case_ids),
                }
                chunk_prompt_text = json.dumps(
                    chunk_prompt,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                shard_call_kind = (
                    f"{call_kind}_FORM_{form_id}_"
                    f"CHUNK_{chunk_index}_OF_{len(chunks)}"
                )
                response = self._provider_call(
                    run_id=run_id,
                    stage_root=stage_root,
                    state=state,
                    target=target,
                    prompt=chunk_prompt_text,
                    response_schema=chunk_schema,
                    structured_chat=structured_chat,
                    call_kind=shard_call_kind,
                )
                form_key = f"form_{form_id}"
                response_form = response.get(form_key)
                if not isinstance(response_form, Mapping):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_REPAIR_SHARD_RESPONSE_INVALID"
                    )
                rows = response_form.get("fact_map")
                if not isinstance(rows, list):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_REPAIR_SHARD_RESPONSE_INVALID"
                    )
                observed_ids = [
                    row.get("case_id") if isinstance(row, Mapping) else None
                    for row in rows
                ]
                if observed_ids != chunk_case_ids:
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_REPAIR_SHARD_EXACT_SET_MISMATCH"
                    )
                merged_rows.extend(deepcopy(rows))
                shard_receipts.append(
                    {
                        "call_kind": shard_call_kind,
                        "form_id": form_id,
                        "chunk_index": chunk_index,
                        "chunk_count": len(chunks),
                        "case_ids": list(chunk_case_ids),
                        "case_ids_sha256": _sha256(chunk_case_ids),
                        "prompt_sha256": hashlib.sha256(
                            chunk_prompt_text.encode("utf-8")
                        ).hexdigest().upper(),
                        "response_schema_sha256": _sha256(chunk_schema),
                        "response_sha256": _sha256(response),
                    }
                )
            merged[f"form_{form_id}"] = {"fact_map": merged_rows}

        profile_kind = str(target.get("kind"))
        transport_receipt = {
                "schema_version": "WorkflowModelExamRepairTransportReceipt-v1",
                "profile_kind": profile_kind,
                "route": (
                    "EXISTING_SETTINGS_API_TRANSPORT"
                    if profile_kind == "API"
                    else (
                        "CODEX_CLI_SUBSCRIPTION"
                        if profile_kind == "CLI"
                        else "OLLAMA_LOOPBACK"
                    )
                ),
                "egress": (
                    "PROVIDER_API"
                    if profile_kind == "API"
                    else ("CODEX_CLI" if profile_kind == "CLI" else "LOOPBACK_ONLY")
                ),
                "parent_contract_sha256": contract.get("contract_sha256"),
                "parent_package_sha256": package.get("package_sha256"),
                "wrong_item_count": total_wrong,
                "shard_size": shard_size,
                "shard_count": len(shard_receipts),
                "shard_activation": (
                    "MULTI_FORM_OR_PROFILE_CASE_LIMIT"
                ),
                "cross_form_response_required_from_provider": False,
                "shards": shard_receipts,
                "merged_response_sha256": _sha256(merged),
                "provider_received_answer_key": False,
            }
        _write_json_create_only(
            stage_root
            / (
                re.sub(r"[^A-Za-z0-9_-]", "_", call_kind).lower()
                + "_transport_shards.json"
            ),
            transport_receipt,
        )
        legacy_receipt = stage_root / "whole_exam_repair_transport_shards.json"
        if not legacy_receipt.exists():
            _write_json_create_only(legacy_receipt, transport_receipt)
        return merged

    @staticmethod
    def _card_distiller_repair_issue_guidance(
        prompt: Mapping[str, Any],
        repair_targets: Mapping[str, Any],
    ) -> dict[str, Any]:
        issue_codes = sorted(
            {
                str(code)
                for targets in repair_targets.values()
                if isinstance(targets, list)
                for target in targets
                if isinstance(target, Mapping)
                for code in (target.get("issue_codes") or [])
                if isinstance(code, str) and code
            }
        )
        guidance_by_code = {
            "COMPARATOR_MISSING": (
                "When the source unit provides a same-context sibling group "
                "or reference occurrence for the target, populate comparator "
                "with its label, value, and unit. Use null only when the "
                "source contains no comparator context."
            ),
            "COMPARATOR_CONTEXT_INCOMPLETE": (
                "Do not clear an existing comparator. Preserve it and add "
                "the missing source-expressed label, numeric value, and unit; "
                "a group label alone is incomplete."
            ),
        }
        projected = deepcopy(dict(prompt))
        projected["successor_issue_guidance"] = {
            "schema_version": CARD_DISTILLER_REPAIR_ISSUE_GUIDANCE_REVISION,
            "gold_consulted": False,
            "expected_values_disclosed": False,
            "preserve_unfailed_current_row_fields": True,
            "issue_codes": issue_codes,
            "guidance": {
                code: guidance_by_code[code]
                for code in issue_codes
                if code in guidance_by_code
            },
            "instruction": (
                "Apply only guidance for issue codes present in this shard. "
                "Derive every value from source_case.source_unit.text."
            ),
        }
        return projected

    def _stage_score_projection(
        self,
        *,
        protocol: Any,
        forms: Mapping[str, Mapping[str, Any]],
        initial_scores: Mapping[str, Mapping[str, Any]],
        final_scores: Mapping[str, Mapping[str, Any]],
        repair_rounds: int,
    ) -> dict[str, Any]:
        del protocol, forms, initial_scores, final_scores, repair_rounds
        return {}

    def _run_stage(
        self,
        *,
        modules: Mapping[str, Any],
        run_id: str,
        run_root: Path,
        stage_name: str,
        target: Mapping[str, Any],
        forms: Mapping[str, Mapping[str, Any]],
        golds: Mapping[str, Mapping[str, Any]],
        budget_cap_cny: float,
        state: dict[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        fact_shard_size: int = 36,
        projection_shard_size: int = 4,
        stage_call_cap: int = MAX_CALLS_PER_STAGE,
    ) -> dict[str, Any]:
        protocol = modules["protocol"]
        repair = modules["repair"]
        stage_root = run_root / stage_name
        stage_root.mkdir(parents=True, exist_ok=False)
        state["stage_calls"] = 0
        state["stage_call_cap"] = _distiller_call_bound(forms, str(target['kind']))
        stage_cost_before = state["estimated_cost_cny"]
        # Coverage views can retain different atomic packs in A and B. Each
        # response must satisfy its own exact form, not the other form's size.
        schemas_by_form = {fid: protocol.output_schema(
            fact_count=len(forms[fid]["cases"]), pack_count=len(forms[fid]["packs"]))
            for fid in ("A", "B")}
        full_schema = schemas_by_form["A"]
        repair_score_kwargs = ({} if schemas_by_form["A"] == schemas_by_form["B"]
                               else {"schemas_by_form": schemas_by_form})
        responses: dict[str, dict[str, Any]] = {}
        candidate_output_metrics_by_form: dict[str, dict[str, Any]] = {}
        for slot in ("A", "B"):
            form = forms[slot]
            form_schema = schemas_by_form[slot]
            fact_ids = [str(row["case_id"]) for row in form["cases"]]
            pack_ids = [str(row["pack_id"]) for row in form["packs"]]
            facts: list[dict[str, Any]] = []
            projections: list[dict[str, Any]] = []
            for start in range(0, len(fact_ids), fact_shard_size):
                target_ids = fact_ids[start : start + fact_shard_size]
                schema = protocol.artifact_schema(
                    form_schema, "fact_map", target_count=len(target_ids)
                )
                shard = self._run_shard(
                    modules=modules,
                    run_id=run_id,
                    stage_root=stage_root,
                    state=state,
                    target=target,
                    form=form,
                    artifact="fact_map",
                    target_ids=target_ids,
                    schema=schema,
                    structured_chat=structured_chat,
                )
                facts.extend(deepcopy(shard["fact_map"]))
            for start in range(0, len(pack_ids), projection_shard_size):
                target_ids = pack_ids[start : start + projection_shard_size]
                schema = protocol.artifact_schema(
                    form_schema, "card_projection", target_count=len(target_ids)
                )
                shard = self._run_shard(
                    modules=modules,
                    run_id=run_id,
                    stage_root=stage_root,
                    state=state,
                    target=target,
                    form=form,
                    artifact="card_projection",
                    target_ids=target_ids,
                    schema=schema,
                    structured_chat=structured_chat,
                )
                projections.extend(deepcopy(shard["card_projection"]))
            response = {"fact_map": facts, "card_projection": projections}
            protocol.validate_response_shape(
                response, dict(form), deepcopy(form_schema)
            )
            output_metrics = _card_distiller_candidate_output_metrics(response)
            output_metrics["core_x2_reference_exceeded"] = (
                _card_distiller_candidate_artifact_limit_exceeded(output_metrics)
            )
            output_metrics["core_x2_reference_semantics"] = (
                "DIAGNOSTIC_ONLY_NOT_INVALIDATION_MODEL_FAIL_OR_REPAIR"
            )
            candidate_output_metrics_by_form[slot] = output_metrics
            responses[slot] = response
        _write_json_create_only(stage_root / "responses_frozen.json", responses)

        initial_scores = {
            slot: protocol.score_response(
                deepcopy(responses[slot]),
                deepcopy(dict(forms[slot])),
                deepcopy(dict(golds[slot])),
                deepcopy(schemas_by_form[slot]),
            )
            for slot in ("A", "B")
        }
        initial_dual = protocol.aggregate_dual_form(
            initial_scores["A"], initial_scores["B"]
        )
        first_pass_role_ability_score = float(initial_dual["mean"])
        final_scores = deepcopy(initial_scores)
        wrong_count = sum(
            int(score["critical_count"]) + int(score["major_count"])
            for score in initial_scores.values()
        )
        failed_exam_item_count = 0
        repair_rounds = 0
        repair_lifecycle: list[dict[str, Any]] = []
        cumulative_penalty: dict[str, Any] | None = None
        if wrong_count:
            current_responses = deepcopy(responses)
            current_scores = deepcopy(initial_scores)
            initial_contract: Mapping[str, Any] | None = None
            first_round_exact_sets: dict[str, list[str]] | None = None
            round_two_burden: dict[str, Any] | None = None
            unchanged_surface_sha256 = _sha256(
                {
                    "forms": forms,
                    "golds_excluded": True,
                    "schema": full_schema,
                    "category_id": self.EXAM_CATEGORY_ID,
                }
            )
            for round_number in range(
                1, self.SEMANTIC_REPAIR_ROUND_LIMIT + 1
            ):
                source_scores = deepcopy(current_scores)
                if round_number == 2:
                    assert first_round_exact_sets is not None
                    for form_id in ("A", "B"):
                        allowed = set(first_round_exact_sets[form_id])
                        source_scores[form_id]["details"] = [
                            deepcopy(detail)
                            for detail in source_scores[form_id].get(
                                "details", []
                            )
                            if isinstance(detail, Mapping)
                            and detail.get("case_id") in allowed
                            and (
                                detail.get("critical_reasons")
                                or detail.get("major_reasons")
                            )
                        ]
                    if not any(
                        source_scores[form_id]["details"]
                        for form_id in ("A", "B")
                    ):
                        break
                package = repair.build_exam_directed_repair_package(
                    forms=forms,
                    original_responses=current_responses,
                    original_scores=source_scores,
                    schema=full_schema,
                )
                exact_sets = {
                    form_id: list(case_ids)
                    for form_id, case_ids in package["contract"][
                        "wrong_item_exact_set"
                    ].items()
                }
                if first_round_exact_sets is None:
                    first_round_exact_sets = deepcopy(exact_sets)
                    initial_contract = deepcopy(package["contract"])
                lifecycle = build_repair_round(
                    round_number=round_number,
                    category_id=self.EXAM_CATEGORY_ID,
                    targets_by_form=exact_sets,
                    prior_targets_by_form=(
                        first_round_exact_sets
                        if round_number == 2
                        else None
                    ),
                    prompt_projection=json.loads(package["prompt"]),
                    unchanged_surface_sha256=unchanged_surface_sha256,
                )
                repair_response = self._run_exam_directed_repair(
                    run_id=run_id,
                    stage_root=stage_root,
                    state=state,
                    target=target,
                    package=package,
                    structured_chat=structured_chat,
                    call_kind=(
                        f"WHOLE_EXAM_DIRECTED_REPAIR_ROUND_{round_number}"
                    ),
                )
                merge = repair.merge_exam_directed_repair(
                    forms=forms,
                    original_responses=current_responses,
                    repair_response=repair_response,
                    schema=full_schema,
                    repair_contract=package["contract"],
                )
                current_responses = deepcopy(merge["responses"])
                current_scores = {
                    form_id: protocol.score_response(
                        deepcopy(current_responses[form_id]),
                        deepcopy(dict(forms[form_id])),
                        deepcopy(dict(golds[form_id])),
                        deepcopy(schemas_by_form[form_id]),
                    )
                    for form_id in ("A", "B")
                }
                if round_number == 2:
                    second = repair.score_exam_after_directed_repair(
                        forms=forms,
                        golds=golds,
                        original_scores=source_scores,
                        merged_responses=current_responses,
                        schema=full_schema,
                        repair_contract=package["contract"],
                        **repair_score_kwargs,
                    )
                    round_two_burden = {
                        "by_form": {
                            form_id: {
                                "nonlinear_category_penalty": float(
                                    second["repair_penalty"][
                                        "initial_burden_by_form"
                                    ][form_id]["total"]
                                ),
                                "fixed_penalty": 0.0,
                                "volume_penalty": float(
                                    second["repair_penalty"][
                                        "initial_burden_by_form"
                                    ][form_id]["wrong_item_volume"]
                                ),
                            }
                            for form_id in ("A", "B")
                        }
                    }
                repair_lifecycle.append(lifecycle)
                if not sum(
                    int(score["critical_count"])
                    + int(score["major_count"])
                    for score in current_scores.values()
                ):
                    break
            assert initial_contract is not None
            final = repair.score_exam_after_directed_repair(
                forms=forms,
                golds=golds,
                original_scores=initial_scores,
                merged_responses=current_responses,
                schema=full_schema,
                repair_contract=initial_contract,
                **repair_score_kwargs,
            )
            penalty_rounds = [
                {
                    "round_number": 1,
                    "category_id": self.EXAM_CATEGORY_ID,
                    "nonlinear_category_penalty": float(
                        final["repair_penalty"][
                            "initial_burden_total_mean"
                        ]
                    ),
                    "fixed_penalty": 0.0,
                    "volume_penalty": 0.0,
                }
            ]
            if round_two_burden is not None:
                second_by_form = round_two_burden["by_form"]
                for form_id in ("A", "B"):
                    extra = sum(
                        float(second_by_form[form_id][field])
                        for field in (
                            "nonlinear_category_penalty",
                            "fixed_penalty",
                            "volume_penalty",
                        )
                    )
                    adjusted = max(
                        0.0,
                        float(final["final_form_scores"][form_id]) - extra,
                    )
                    final["final_form_scores"][form_id] = round(
                        adjusted, 2
                    )
                    final["forms"][form_id][
                        "round_two_additional_burden"
                    ] = deepcopy(second_by_form[form_id])
                    final["forms"][form_id]["final_form_score"] = round(
                        adjusted, 2
                    )
                final["final_score"] = round(
                    sum(final["final_form_scores"].values()) / 2.0, 2
                )
                penalty_rounds.append(
                    {
                        "round_number": 2,
                        "category_id": self.EXAM_CATEGORY_ID,
                        "nonlinear_category_penalty": sum(
                            float(row["nonlinear_category_penalty"])
                            for row in second_by_form.values()
                        )
                        / 2.0,
                        "fixed_penalty": sum(
                            float(row["fixed_penalty"])
                            for row in second_by_form.values()
                        )
                        / 2.0,
                        "volume_penalty": sum(
                            float(row["volume_penalty"])
                            for row in second_by_form.values()
                        )
                        / 2.0,
                    }
                )
            cumulative_penalty = cumulative_category_repair_penalty(
                penalty_rounds
            )
            score = float(final["final_score"])
            verdict = (
                "PASS"
                if final["quality_hard_gate"] == "PASS"
                and score >= PASSING_SCORE
                else "FAIL"
            )
            failed_exam_item_count = int(final["failed_exam_item_count"])
            repair_rounds = len(repair_lifecycle)
            final_scores = deepcopy(current_scores)
        else:
            dual = protocol.aggregate_dual_form(
                initial_scores["A"], initial_scores["B"]
            )
            score = float(dual["mean"])
            verdict = (
                "PASS"
                if dual["quality_hard_gate"] == "PASS" and score >= PASSING_SCORE
                else "FAIL"
            )
        score_projection = self._stage_score_projection(
            protocol=protocol,
            forms=forms,
            initial_scores=initial_scores,
            final_scores=final_scores,
            repair_rounds=repair_rounds,
        )
        primary_case_ids: set[str] | None = None
        if score_projection:
            projected_score = score_projection.get("score_override")
            projected_first_pass = score_projection.get(
                "first_pass_score_override"
            )
            projected_verdict = score_projection.get("quality_verdict_override")
            projected_ids = score_projection.get("primary_case_ids")
            if (
                isinstance(projected_score, (int, float))
                and not isinstance(projected_score, bool)
                and isinstance(projected_first_pass, (int, float))
                and not isinstance(projected_first_pass, bool)
                and projected_verdict in {"PASS", "FAIL"}
                and isinstance(projected_ids, list)
                and projected_ids
                and all(isinstance(value, str) and value for value in projected_ids)
            ):
                score = float(projected_score)
                first_pass_role_ability_score = float(projected_first_pass)
                verdict = str(projected_verdict)
                primary_case_ids = set(projected_ids)
            else:
                raise ValueError("CARD_DISTILLER_STAGE_SCORE_PROJECTION_INVALID")
        blocking_failures: list[dict[str, Any]] = []
        for form_id in ("A", "B"):
            for detail in final_scores[form_id].get("details", []):
                if not isinstance(detail, Mapping):
                    continue
                if (
                    primary_case_ids is not None
                    and str(detail.get("case_id")) not in primary_case_ids
                ):
                    continue
                failed_fields = sorted(
                    {
                        str(reason)
                        for key in ("critical_reasons", "major_reasons")
                        for reason in (detail.get(key) or [])
                        if isinstance(reason, str) and reason
                    }
                )
                if failed_fields:
                    blocking_failures.append(
                        {
                            "case_id": str(detail.get("case_id") or f"{form_id}-UNKNOWN"),
                            "failed_fields": failed_fields,
                            "evidence_anchor": (
                                f"{stage_name}/scores/form_{form_id}/"
                                f"case_details/{detail.get('case_id')}"
                            ),
                            "blocking": True,
                        }
                    )
        if (
            verdict == "FAIL"
            and not blocking_failures
            and primary_case_ids is None
        ):
            blocking_failures.append(
                {
                    "case_id": "CARD_DISTILLER_AGGREGATE",
                    "failed_fields": ["aggregate_score_or_repair_burden"],
                    "evidence_anchor": f"{stage_name}/stage_result/aggregate",
                    "blocking": True,
                }
            )
        residual_materiality = (
            _card_distiller_residual_materiality_projection(
                blocking_failures=blocking_failures,
                golds=golds,
            )
        )
        blocking_failures = residual_materiality["blocking_failures"]
        model_fail_projection = _primary_model_fail_projection(
            category_id=self.EXAM_CATEGORY_ID,
            score=score,
            raw_quality_verdict=verdict,
            blocking_failures=blocking_failures,
            guarded_operational_verdict=(
                "SUITABLE_WITH_CARD_REVIEWER_AND_GOVERNED_DISPOSITION"
            ),
        )
        repair_cycle_closed = (
            repair_rounds == self.SEMANTIC_REPAIR_ROUND_LIMIT
            or failed_exam_item_count == 0
        )
        stage_cost = round(state["estimated_cost_cny"] - stage_cost_before, 9)
        if stage_cost > budget_cap_cny:
            raise ValueError("WORKFLOW_MODEL_EXAM_STAGE_COST_CAP_EXCEEDED")
        stage_result = {
            "schema_version": "WorkflowModelExamStageResult-v1",
            "stage": stage_name,
            "chain_status": "PASS",
            "quality_verdict": verdict,
            "score": round(score, 2),
            "first_pass_role_ability_score": round(
                first_pass_role_ability_score, 2
            ),
            "post_repair_score": round(score, 2),
            "failed_exam_item_count": failed_exam_item_count,
            "repair_rounds": repair_rounds,
            "semantic_repair_round_limit": self.SEMANTIC_REPAIR_ROUND_LIMIT,
            "semantic_repair_lifecycle": repair_lifecycle,
            "cumulative_category_repair_penalty": cumulative_penalty,
            "blocking_failures": blocking_failures,
            **model_fail_projection,
            "residual_materiality_policy_revision": residual_materiality[
                "schema_version"
            ],
            "calibration_override_case_ids": residual_materiality[
                "calibration_override_case_ids"
            ],
            "quality_raw_score_and_verdict_preserved": True,
            "repair_cycle_closed": repair_cycle_closed,
            "input_exact_set_sha256": _sha256(
                {"forms": forms, "schema": full_schema}
            ),
            "transport_retry_is_semantic_repair": False,
            "provider_calls": state["stage_calls"],
            "estimated_cost_cny": stage_cost,
            "budget_cap_cny": budget_cap_cny,
            "provider_received_answer_key": False,
            "reference_regression_only": stage_name != "low_cost",
            "candidate_output_metrics_by_form": (
                candidate_output_metrics_by_form
            ),
            "candidate_output_capacity_limit_surface": (
                "EACH_LOGICAL_ARTIFACT_PER_FORM_NOT_CUMULATIVE"
            ),
            "combined_candidate_output_metric_quality_semantics": (
                "DIAGNOSTIC_ONLY_NOT_MODEL_FAIL"
            ),
            **{
                key: deepcopy(value)
                for key, value in score_projection.items()
                if key
                not in {
                    "score_override",
                    "first_pass_score_override",
                    "quality_verdict_override",
                    "primary_case_ids",
                }
            },
        }
        _write_json_create_only(stage_root / "stage_result.json", stage_result)
        return stage_result

    def execute(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
        *,
        authorization: Mapping[str, Any] | None,
        structured_chat: Callable[..., Mapping[str, Any]],
    ) -> dict[str, Any]:
        plan = self.plan(node, target, context)
        requested_model = (
            str(target.get("model_name"))
            if isinstance(target.get("model_name"), str)
            else None
        )
        if plan.get("status") != "READY":
            return _not_run(str(plan.get("reason") or "WORKFLOW_MODEL_EXAM_NOT_AVAILABLE"), requested_model=requested_model)
        authorization_reason = self._authorization_reason(authorization, plan)
        if authorization_reason is not None:
            return _not_run(authorization_reason, requested_model=requested_model)

        run_id = "workflow-exam-" + uuid.uuid4().hex
        run_root = self._scratch_root / "workflow_exams" / run_id
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
            "stage_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_network_calls": 0,
            "external_process_launches": 0,
            "estimated_cost_cny": 0.0,
        }
        try:
            modules = self._quality()
            low_form_a, low_gold_a = modules["assets"].generate_form("G")
            low_form_b, low_gold_b = modules["assets"].generate_form("H")
            pack = modules["reference"].load_card_distiller_reference_exam_pack(
                self._reference_pack_root
            )
            high_form_a = _load_json(pack.member("forms/form_A.json"))
            high_form_b = _load_json(pack.member("forms/form_B.json"))
            high_gold_a = _load_json(pack.member("gold/form_A_gold.json"))
            high_gold_b = _load_json(pack.member("gold/form_B_gold.json"))
            low = self._run_stage(
                modules=modules,
                run_id=run_id,
                run_root=run_root,
                stage_name="low_cost",
                target=context["low_cost_target"],
                forms={"A": low_form_a, "B": low_form_b},
                golds={"A": low_gold_a, "B": low_gold_b},
                budget_cap_cny=float(plan["low_cost"]["budget_cap_cny"]),
                state=state,
                structured_chat=structured_chat,
            )
            if low["chain_status"] != "PASS":
                raise ValueError("LOW_COST_REHEARSAL_NOT_PASSED")
            high = self._run_stage(
                modules=modules,
                run_id=run_id,
                run_root=run_root,
                stage_name="high_cost",
                target=target,
                forms={"A": high_form_a, "B": high_form_b},
                golds={"A": high_gold_a, "B": high_gold_b},
                budget_cap_cny=float(plan["high_cost"]["budget_cap_cny"]),
                state=state,
                structured_chat=structured_chat,
            )
            if state["provider_calls"] > int(plan["maximum_provider_calls"]):
                raise ValueError("WORKFLOW_MODEL_EXAM_TOTAL_CALL_LIMIT_EXCEEDED")
            if state["estimated_cost_cny"] > float(plan["budget_cap_cny"]):
                raise ValueError("WORKFLOW_MODEL_EXAM_TOTAL_COST_CAP_EXCEEDED")
            public_result = {
                "schema_version": "WorkflowModelExamRunResult-v1",
                "status": high["quality_verdict"],
                "reason": "Quality_REFERENCE_REGRESSION_COMPLETED",
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "executor_ref": EXECUTOR_REF,
                "executor_sha256": _file_sha256(Path(__file__)),
                "requested_model": requested_model,
                "returned_model": requested_model,
                "score": int(round(float(high["score"]))),
                "failed_exam_item_count": high["failed_exam_item_count"],
                "low_cost_chain_status": low["chain_status"],
                "low_cost_quality_verdict": low["quality_verdict"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "estimated_cost_cny": round(state["estimated_cost_cny"], 9),
                "actual_cost": None,
                "reference_regression_only": True,
                "blind_holdout_eligible": False,
                "qualification_eligible": False,
                "provider_received_answer_key": False,
            }
            evidence = _write_json_create_only(
                run_root / "exam_result.json", public_result
            )
            return {
                "schema_version": RESULT_SCHEMA,
                "kind": "WORKFLOW_NODE",
                "status": high["quality_verdict"],
                "score": int(round(float(high["score"]))),
                "reason": "Quality_REFERENCE_REGRESSION_COMPLETED",
                "requested_model": requested_model,
                "returned_model": requested_model,
                "duration_ms": 0,
                "external_network_calls": state["external_network_calls"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_process_launches": state[
                    "external_process_launches"
                ],
                "exam_mode": plan["mode"],
                "exam_run_id": run_id,
                "exam_run_root": str(run_root.resolve()),
                "exam_evidence_sha256": evidence["sha256"],
                "failed_exam_item_count": high["failed_exam_item_count"],
                "estimated_cost_cny": round(state["estimated_cost_cny"], 9),
                "actual_cost": None,
                "reference_regression_only": True,
                "blind_holdout_eligible": False,
                "qualification_eligible": False,
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
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "external_process_launches": state[
                    "external_process_launches"
                ],
                "estimated_cost_cny": round(state["estimated_cost_cny"], 9),
                "score": None,
            }
            evidence = _write_json_create_only(run_root / "exam_failure.json", failure)
            result = _not_run(reason, requested_model=requested_model)
            result.update(
                provider_calls=state["provider_calls"],
                external_model_calls=state["external_model_calls"],
                external_network_calls=state["external_network_calls"],
                external_process_launches=state[
                    "external_process_launches"
                ],
                exam_run_id=run_id,
                exam_run_root=str(run_root.resolve()),
                exam_evidence_sha256=evidence["sha256"],
                estimated_cost_cny=round(state["estimated_cost_cny"], 9),
            )
            return result


class QualityCardDistillerDirectExamExecutor(QualityCardDistillerExamExecutor):
    """Run the frozen Card Distiller reference pack over API, CLI, or local."""

    EXECUTOR_REF = (
        "Desktop_MODEL_EXAM_SUCCESSOR_Quality_CARD_DISTILLER_EXECUTOR_V6_"
        "TWO_ROUND_GUIDED_REPAIR_CALIBRATED_FAIL"
    )
    MODE = "LIVE_Quality_CARD_DISTILLER_SUCCESSOR_REFERENCE_REGRESSION"
    SEMANTIC_REPAIR_ROUND_LIMIT = 2
    API_REQUEST_CAP = 25
    CLI_REQUEST_CAP = 25
    LOCAL_REQUEST_CAP = 64
    API_BUDGET_CAP_CNY = 6.0
    SAMPLE_ROTATION_SLOTS = 8
    REQUEST_TIMEOUT_SECONDS_BY_PROFILE = {
        "API": 600,
        "CLI": 1_200,
        "LOCAL": 1_200,
    }

    @classmethod
    def _request_timeout_seconds(cls, profile_kind: str) -> int:
        value = cls.REQUEST_TIMEOUT_SECONDS_BY_PROFILE.get(profile_kind)
        if value is None:
            raise ValueError("WORKFLOW_MODEL_EXAM_TIMEOUT_PROFILE_INVALID")
        return value

    @staticmethod
    def _profile_kind(target: Mapping[str, Any]) -> str | None:
        kind = target.get("kind")
        if kind == "API":
            if QualityCardDistillerExamExecutor._eligible_target(
                target, low_cost=True
            ):
                return "API"
            return None
        if kind == "CLI":
            if (
                _compact(target.get("adapter_id")) == "codexcli"
                and target.get("model_name") == "gpt-5.6-luna"
                and target.get("thinking_mode") in {"high", "xhigh"}
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "CLI"
            return None
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

    @classmethod
    def _sample_slot(cls, context: Mapping[str, Any]) -> int:
        explicit = context.get("workflow_exam_sample_slot")
        if explicit is not None:
            if (
                isinstance(explicit, bool)
                or not isinstance(explicit, int)
                or explicit < 1
                or explicit > cls.SAMPLE_ROTATION_SLOTS
            ):
                raise ValueError("CARD_DISTILLER_SUCCESSOR_SAMPLE_SLOT_INVALID")
            return explicit
        ordinal = context.get("workflow_exam_attempt_ordinal")
        if ordinal is None:
            revision = context.get("settings_revision", 0)
            if isinstance(revision, bool) or not isinstance(revision, int):
                raise ValueError("CARD_DISTILLER_SUCCESSOR_SAMPLE_SLOT_INVALID")
            ordinal = max(1, revision + 1)
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            raise ValueError("CARD_DISTILLER_SUCCESSOR_ATTEMPT_ORDINAL_INVALID")
        return ((ordinal - 1) % cls.SAMPLE_ROTATION_SLOTS) + 1

    @classmethod
    def _exam_inputs(
        cls,
        *,
        pack: Any,
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
        sample_slot = cls._sample_slot(context)
        forms: dict[str, dict[str, Any]] = {}
        golds: dict[str, dict[str, Any]] = {}
        selected: dict[str, dict[str, Any]] = {}
        for form_id in ("A", "B"):
            source_form = source_forms[form_id]
            source_gold = source_golds[form_id]
            packs = source_form.get("packs")
            gold_packs = source_gold.get("packs")
            if (
                not isinstance(packs, list)
                or len(packs) != cls.SAMPLE_ROTATION_SLOTS
                or not isinstance(gold_packs, list)
                or len(gold_packs) != cls.SAMPLE_ROTATION_SLOTS
            ):
                raise ValueError("CARD_DISTILLER_SUCCESSOR_SOURCE_PACK_INVALID")
            omitted_pack_id = str(packs[sample_slot - 1].get("pack_id"))
            selected_packs = [
                deepcopy(row)
                for row in packs
                if isinstance(row, Mapping)
                and row.get("pack_id") != omitted_pack_id
            ]
            selected_pack_ids = [str(row["pack_id"]) for row in selected_packs]
            selected_pack_set = set(selected_pack_ids)
            selected_cases = [
                deepcopy(row)
                for row in source_form.get("cases", [])
                if isinstance(row, Mapping)
                and row.get("pack_id") in selected_pack_set
            ]
            selected_case_ids = [str(row["case_id"]) for row in selected_cases]
            selected_case_set = set(selected_case_ids)
            selected_gold_packs = [
                deepcopy(row)
                for row in gold_packs
                if isinstance(row, Mapping)
                and row.get("pack_id") in selected_pack_set
            ]
            selected_gold_cases = [
                deepcopy(row)
                for row in source_gold.get("cases", [])
                if isinstance(row, Mapping)
                and row.get("case_id") in selected_case_set
            ]
            if (
                len(selected_packs) != 7
                or len(selected_cases) != 63
                or len(selected_gold_packs) != 7
                or len(selected_gold_cases) != 63
                or [row["case_id"] for row in selected_gold_cases]
                != selected_case_ids
            ):
                raise ValueError("CARD_DISTILLER_SUCCESSOR_SAMPLE_INVALID")
            form = deepcopy(dict(source_form))
            form["packs"] = selected_packs
            form["cases"] = selected_cases
            form["successor_projection"] = {
                "revision": CARD_DISTILLER_SUCCESSOR_SAMPLE_REVISION,
                "source_content_sha256": source_form.get("content_sha256"),
                "omitted_pack_id": omitted_pack_id,
            }
            gold = deepcopy(dict(source_gold))
            gold["packs"] = selected_gold_packs
            gold["cases"] = selected_gold_cases
            gold["successor_projection"] = {
                "revision": CARD_DISTILLER_SUCCESSOR_SAMPLE_REVISION,
                "source_content_sha256": source_gold.get("content_sha256"),
                "omitted_pack_id": omitted_pack_id,
            }
            forms[form_id] = form
            golds[form_id] = gold
            selected[form_id] = {
                "omitted_pack_id": omitted_pack_id,
                "selected_pack_ids": selected_pack_ids,
                "selected_case_ids": selected_case_ids,
            }
        manifest = {
            "schema_version": CARD_DISTILLER_SUCCESSOR_SAMPLE_REVISION,
            "sample_slot": sample_slot,
            "rotation_slots": cls.SAMPLE_ROTATION_SLOTS,
            "selected_case_count_per_form": 63,
            "selected_pack_count_per_form": 7,
            "selection": selected,
            "coverage_semantics": "EACH_SOURCE_PACK_OMITTED_ONCE_ACROSS_EIGHT_SLOTS",
            "gold_in_subject_request": False,
        }
        manifest["manifest_sha256"] = _sha256(manifest)
        return {"forms": forms, "golds": golds, "sample_metadata": manifest}

    @classmethod
    def _request_cap(cls, profile_kind: str) -> int:
        return {
            "API": cls.API_REQUEST_CAP,
            "CLI": cls.CLI_REQUEST_CAP,
            "LOCAL": cls.LOCAL_REQUEST_CAP,
        }[profile_kind]

    def _structured_output_policy(
        self,
        target: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int | None, str]:
        return workflow_exam_structured_output_policy(target)

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        node_id = node.get("node_id")
        if node_id not in {"ingest", "card_distill"}:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        profile_kind = self._profile_kind(target)
        if profile_kind is None:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        try:
            modules = self._quality()
            verification = modules["pack"].verify_reference_pack(
                self._reference_pack_root
            )
            pack = modules["reference"].load_card_distiller_reference_exam_pack(
                self._reference_pack_root
            )
            inputs = self._exam_inputs(pack=pack, context=context)
            workload = core_primary_workload_profile()
            protocol = modules["protocol"]
            (
                model_binding,
                request_max_output_tokens,
                output_limit_mode,
            ) = self._structured_output_policy(target)
            perfect_metrics: dict[str, dict[str, int]] = {}
            perfect_artifact_metrics: dict[
                str, dict[str, dict[str, int]]
            ] = {}
            source_metrics: dict[str, int] = {}
            for form_id in ("A", "B"):
                source_metrics[form_id] = len(
                    _serialized_request(inputs["forms"][form_id])
                )
                perfect = protocol.perfect_response(inputs["golds"][form_id])
                measured = _card_distiller_candidate_output_metrics(perfect)
                perfect_metrics[form_id] = deepcopy(
                    measured["combined_diagnostic"]
                )
                perfect_artifact_metrics[form_id] = deepcopy(
                    measured["artifacts"]
                )
            if any(
                value > Core_PRIMARY_SOURCE_INPUT_CHAR_LIMIT
                for value in source_metrics.values()
            ) or any(
                row["chars"] > Core_PRIMARY_CARD_OUTPUT_CHAR_LIMIT
                or row["utf8_bytes"] > Core_PRIMARY_CARD_OUTPUT_BYTE_LIMIT
                for row in perfect_metrics.values()
            ):
                return _not_available(
                    "WORKFLOW_MODEL_EXAM_DESIGN_INPUT_OUT_OF_BOUNDS"
                )
            if profile_kind == "API":
                self._pricing_profile()
        except (ImportError, OSError, ValueError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_ASSETS_INVALID")
        if (
            verification.get("status") != "PASS"
            or verification.get("reference_regression_only") is not True
            or verification.get("qualification_eligible") is not False
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_PACK_NOT_ELIGIBLE")
        budget_cap = self.API_BUDGET_CAP_CNY if profile_kind == "API" else None
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
            "model_digest": target.get("model_digest"),
            "thinking_mode": target.get("thinking_mode"),
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": verification["pack_id"],
            "reference_pack_revision": verification["pack_revision"],
            "reference_pack_sha256": verification["pack_fingerprint"],
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "forms": ["A", "B"],
            "base_model_calls": 16 if profile_kind == "LOCAL" else 8,
            "fact_shard_size": 12 if profile_kind == "LOCAL" else 36,
            "projection_shard_size": 4,
            "maximum_model_calls": self._request_cap(profile_kind),
            "request_timeout_seconds": self._request_timeout_seconds(
                profile_kind
            ),
            "request_timeout_policy": (
                "Core_X2_NODE_AND_PROFILE_SPECIFIC_NOT_GLOBAL"
            ),
            "output_limit_policy": (
                "CALIBRATED_CEILING_WITH_PROVIDER_DEFAULT_API_CLI_"
                "AND_MODEL_PROFILE_LOCAL"
            ),
            "calibrated_reply_token_ceiling": (
                WORKFLOW_EXAM_REPLY_TOKEN_CEILING
            ),
            "output_limit_contract": workflow_exam_output_limit_contract(),
            "structured_output_profile": {
                "mode": output_limit_mode,
                "request_max_output_tokens": request_max_output_tokens,
                "context_window_tokens": (
                    model_binding.get("context_window_tokens")
                    if profile_kind == "LOCAL"
                    else None
                ),
                "profile_specific_not_global": True,
            },
            "sample_revision": inputs["sample_metadata"]["schema_version"],
            "sample_slot": inputs["sample_metadata"]["sample_slot"],
            "sample_manifest_sha256": inputs["sample_metadata"][
                "manifest_sha256"
            ],
            "sample_case_count_per_form": 63,
            "sample_pack_count_per_form": 7,
            "sample_rotation_slots": self.SAMPLE_ROTATION_SLOTS,
            "workload_profile": workload,
            "source_input_chars_by_form": source_metrics,
            "perfect_output_metrics_by_form": perfect_metrics,
            "perfect_artifact_output_metrics_by_form": (
                perfect_artifact_metrics
            ),
            "exam_design_length_limit_surface": (
                "COMBINED_FROZEN_PERFECT_RESPONSE_PER_FORM"
            ),
            "candidate_output_capacity_limit_surface": (
                "NONE_Core_X2_ARTIFACT_SIZE_IS_DIAGNOSTIC_ONLY"
            ),
            "combined_candidate_output_metric_quality_semantics": (
                "DIAGNOSTIC_ONLY_NOT_MODEL_FAIL"
            ),
            "candidate_output_reference_quality_semantics": (
                "DIAGNOSTIC_ONLY_NOT_INVALIDATION_MODEL_FAIL_OR_REPAIR"
            ),
            "capacity_overflow_quality_semantics": "NOT_ASSESSED",
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "exam_category_id": self.EXAM_CATEGORY_ID,
            "horizontal_comparison_eligible": True,
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "semantic_repair_round_limit": self.SEMANTIC_REPAIR_ROUND_LIMIT,
            "second_semantic_repair_allowed": True,
            "second_round_scope": (
                "ROUND1_RESIDUAL_EXACT_SET_ONLY_NO_FULL_FORM_RETRY"
            ),
            "second_round_penalty_semantics": (
                "CUMULATIVE_Quality_CATEGORY_NONLINEAR_PLUS_"
                "CATEGORY_VOLUME_BURDEN"
            ),
            "directed_repair_transport_policy": {
                "multi_form_response_required_from_provider": False,
                "api_cli_per_form_case_limit": 36,
                "local_per_form_case_limit": 12,
                "merge_authority": "LOCAL_Quality_REPAIR_MERGER",
                "semantic_round_increment_per_transport_shard": False,
                "issue_guidance_revision": (
                    CARD_DISTILLER_REPAIR_ISSUE_GUIDANCE_REVISION
                ),
                "gold_consulted_for_issue_guidance": False,
            },
            "residual_materiality_policy_revision": (
                CARD_DISTILLER_RESIDUAL_MATERIALITY_REVISION
            ),
            "budget_cap_cny": budget_cap,
            "cost_semantics": (
                "FROZEN_PROFILE_ESTIMATE_CAP"
                if profile_kind == "API"
                else (
                    "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE"
                    if profile_kind == "CLI"
                    else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
                )
            ),
            "authorization_schema_version": AUTHORIZATION_SCHEMA,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }
        comparison_basis = {
            "category_id": self.EXAM_CATEGORY_ID,
            "reference_pack_sha256": plan["reference_pack_sha256"],
            "sample_revision": plan["sample_revision"],
            "sample_slot": plan["sample_slot"],
            "sample_manifest_sha256": plan["sample_manifest_sha256"],
            "executor_ref": self.EXECUTOR_REF,
        }
        comparison_sha256 = _sha256(comparison_basis)
        plan["comparison_binding_sha256"] = comparison_sha256
        plan["comparison_cohort_id"] = (
            f"CARD_DISTILLER_SUCCESSOR:{comparison_sha256}"
        )
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
        if (
            authorization.get("acknowledged_subscription_no_per_call_price")
            is not (plan.get("profile_kind") == "CLI")
        ):
            return "WORKFLOW_MODEL_EXAM_COST_SEMANTICS_ACKNOWLEDGEMENT_REQUIRED"
        return None

    def _provider_call(self, **kwargs: Any) -> dict[str, Any]:
        state = kwargs["state"]
        target = kwargs["target"]
        if state["stage_calls"] >= self._request_cap(str(target["kind"])):
            raise ValueError("WORKFLOW_MODEL_EXAM_STAGE_CALL_LIMIT_REACHED")
        return super()._provider_call(**kwargs)

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
        run_root = self._scratch_root / "workflow_exams" / run_id
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
            "stage_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_network_calls": 0,
            "external_process_launches": 0,
            "estimated_cost_cny": 0.0,
        }
        try:
            modules = self._quality()
            pack = modules["reference"].load_card_distiller_reference_exam_pack(
                self._reference_pack_root
            )
            inputs = self._exam_inputs(pack=pack, context=context)
            forms = inputs["forms"]
            golds = inputs["golds"]
            if inputs["sample_metadata"]["manifest_sha256"] != plan.get(
                "sample_manifest_sha256"
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_CARD_SAMPLE_MANIFEST_MISMATCH"
                )
            _write_json_create_only(
                run_root / "sample_manifest.json",
                inputs["sample_metadata"],
            )
            for form in forms.values():
                if (
                    len(_serialized_request(form))
                    > Core_PRIMARY_SOURCE_INPUT_CHAR_LIMIT
                ):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_CARD_INPUT_200_PERCENT_LIMIT_EXCEEDED"
                    )
            stage = self._run_stage(
                modules=modules,
                run_id=run_id,
                run_root=run_root,
                stage_name="direct_reference",
                target=target,
                forms=forms,
                golds=golds,
                budget_cap_cny=float(plan["budget_cap_cny"] or 0.0),
                state=state,
                structured_chat=structured_chat,
                fact_shard_size=int(plan["fact_shard_size"]),
                projection_shard_size=int(plan["projection_shard_size"]),
                stage_call_cap=int(plan["maximum_model_calls"]),
            )
            if state["stage_calls"] > int(plan["maximum_model_calls"]):
                raise ValueError("WORKFLOW_MODEL_EXAM_TOTAL_CALL_LIMIT_EXCEEDED")
            score = int(round(float(stage["score"])))
            raw_quality_verdict = str(stage["quality_verdict"])
            raw_fail_admission = None
            if raw_quality_verdict == "FAIL":
                raw_fail_admission = admit_quality_fail(
                    execution_outcome=ExecutionOutcome.COMPLETED,
                    scorability=Scorability.SCOREABLE,
                    repair_rounds_used=int(stage["repair_rounds"]),
                    repair_round_limit=int(
                        stage["semantic_repair_round_limit"]
                    ),
                    repair_cycle_closed=bool(stage["repair_cycle_closed"]),
                    blocking_failures=stage["blocking_failures"],
                    exact_hashes={
                        "reference_pack_sha256": str(
                            plan["reference_pack_sha256"]
                        ),
                        "scoring_protocol_sha256": _file_sha256(
                            pack.member("scoring/scoring_protocol.json")
                        ),
                        "executor_sha256": _file_sha256(Path(__file__)),
                        "input_exact_set_sha256": str(
                            stage["input_exact_set_sha256"]
                        ),
                    },
                )
            model_fail_established = bool(stage["model_fail_established"])
            fail_admission = (
                raw_fail_admission if model_fail_established else None
            )
            verdict = "FAIL" if model_fail_established else "PASS"
            public_result = {
                "schema_version": "WorkflowModelExamRunResult-v1",
                "status": verdict,
                "reason": "Quality_REFERENCE_REGRESSION_COMPLETED",
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "executor_ref": self.EXECUTOR_REF,
                "executor_sha256": _file_sha256(Path(__file__)),
                "requested_model": requested_model,
                "returned_model": requested_model,
                "score": score,
                "execution_outcome": ExecutionOutcome.COMPLETED.value,
                "scorability": Scorability.SCOREABLE.value,
                "raw_quality_candidate_verdict": raw_quality_verdict,
                "raw_quality_fail_admission": raw_fail_admission,
                "quality_fail_admission": fail_admission,
                "first_pass_role_ability_score": stage[
                    "first_pass_role_ability_score"
                ],
                "post_repair_score": stage["post_repair_score"],
                "model_fail_policy_revision": stage[
                    "model_fail_policy_revision"
                ],
                "model_fail_verdict": stage["model_fail_verdict"],
                "model_fail_established": model_fail_established,
                "model_fail_minimum_distinct_material_cases": stage[
                    "model_fail_minimum_distinct_material_cases"
                ],
                "material_residual_case_ids": stage[
                    "material_residual_case_ids"
                ],
                "calibration_residual_case_ids": stage[
                    "calibration_residual_case_ids"
                ],
                "operational_eligibility_verdict": stage[
                    "operational_eligibility_verdict"
                ],
                "exam_category_id": stage["category_id"],
                "category_local_score": stage["category_local_score"],
                "numeric_score_is_not_model_fail_verdict": True,
                "cross_category_comparison_forbidden": True,
                "global_ranking_forbidden": True,
                "comparison_binding_sha256": plan[
                    "comparison_binding_sha256"
                ],
                "comparison_cohort_id": plan["comparison_cohort_id"],
                "horizontal_comparison_eligible": True,
                "horizontal_comparison_allowed_only_with_same_cohort": True,
                "failed_exam_item_count": stage["failed_exam_item_count"],
                "semantic_repair_rounds": stage["repair_rounds"],
                "semantic_repair_round_limit": stage[
                    "semantic_repair_round_limit"
                ],
                "semantic_repair_lifecycle": stage[
                    "semantic_repair_lifecycle"
                ],
                "cumulative_category_repair_penalty": stage[
                    "cumulative_category_repair_penalty"
                ],
                "transport_retry_is_semantic_repair": False,
                "request_attempts": state["stage_calls"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "external_process_launches": state["external_process_launches"],
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
                "sample_revision": plan["sample_revision"],
                "sample_slot": plan["sample_slot"],
                "sample_manifest_sha256": plan["sample_manifest_sha256"],
                "workload_profile": plan["workload_profile"],
                "residual_materiality_policy_revision": stage[
                    "residual_materiality_policy_revision"
                ],
                "calibration_override_case_ids": stage[
                    "calibration_override_case_ids"
                ],
                "quality_raw_score_and_verdict_preserved": stage[
                    "quality_raw_score_and_verdict_preserved"
                ],
                "candidate_output_metrics_by_form": stage[
                    "candidate_output_metrics_by_form"
                ],
                "candidate_output_capacity_limit_surface": stage[
                    "candidate_output_capacity_limit_surface"
                ],
                "combined_candidate_output_metric_quality_semantics": stage[
                    "combined_candidate_output_metric_quality_semantics"
                ],
                **(
                    {
                        "primary_language_track": stage[
                            "primary_language_track"
                        ],
                        "language_tracks": deepcopy(stage["language_tracks"]),
                        "cross_language_aggregation_forbidden": stage[
                            "cross_language_aggregation_forbidden"
                        ],
                        "repair_burden_affects_capability_score": stage[
                            "repair_burden_affects_capability_score"
                        ],
                    }
                    if "language_tracks" in stage
                    else {}
                ),
            }
            evidence = _write_json_create_only(
                run_root / "exam_result.json", public_result
            )
            return {
                **public_result,
                "schema_version": RESULT_SCHEMA,
                "kind": "WORKFLOW_NODE",
                "duration_ms": max(0, int((monotonic() - started) * 1000)),
                "exam_mode": plan["mode"],
                "exam_run_id": run_id,
                "exam_run_root": str(run_root.resolve()),
                "exam_evidence_sha256": evidence["sha256"],
                "executor_ref": self.EXECUTOR_REF,
                "executor_sha256": public_result["executor_sha256"],
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
                "request_attempts": state["stage_calls"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "external_process_launches": state["external_process_launches"],
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
                request_attempts=state["stage_calls"],
                provider_calls=state["provider_calls"],
                external_model_calls=state["external_model_calls"],
                external_network_calls=state["external_network_calls"],
                external_process_launches=state["external_process_launches"],
                exam_run_id=run_id,
                exam_run_root=str(run_root.resolve()),
                exam_evidence_sha256=evidence["sha256"],
                estimated_cost_cny=failure["estimated_cost_cny"],
                cost_semantics=plan["cost_semantics"],
                execution_outcome=failure["execution_outcome"],
                scorability=failure["scorability"],
            )
            return result


class QualityNewCardDistillerLanguageBankExamExecutor(
    QualityCardDistillerDirectExamExecutor
):
    """Quality_new Card exam over a 100/36/36 language bank."""

    EXECUTOR_REF = (
        "Desktop_MODEL_EXAM_SUCCESSOR_Quality_CARD_DISTILLER_LANGUAGE_BANK_"
        "EXECUTOR_V7"
    )
    MODE = "LIVE_Quality_CARD_DISTILLER_LANGUAGE_BANK_SUCCESSOR"
    API_REQUEST_CAP = 25
    CLI_REQUEST_CAP = 25
    LOCAL_REQUEST_CAP = 25
    SAMPLE_ROTATION_SLOTS = 5
    SAMPLE_REVISION = "CARD_DISTILLER_EN36_ZH6_JA6_DUAL_FORM_V2"

    @staticmethod
    def _prune_pack(
        *,
        pack: Mapping[str, Any],
        gold_pack: Mapping[str, Any],
        selected_case_ids: set[str],
        case_chunk_by_id: Mapping[str, str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        projected_pack = deepcopy(dict(pack))
        projected_gold = deepcopy(dict(gold_pack))
        all_case_ids = [str(value) for value in pack.get("case_ids", [])]
        kept_case_ids = [
            case_id for case_id in all_case_ids if case_id in selected_case_ids
        ]
        all_case_chunks = {
            case_chunk_by_id[case_id]
            for case_id in all_case_ids
            if case_id in case_chunk_by_id
        }
        selected_chunks = {
            case_chunk_by_id[case_id]
            for case_id in kept_case_ids
            if case_id in case_chunk_by_id
        }
        projected_pack["case_ids"] = kept_case_ids
        projected_pack["chunks"] = [
            deepcopy(row)
            for row in pack.get("chunks", [])
            if str(row.get("chunk_id")) not in all_case_chunks
            or str(row.get("chunk_id")) in selected_chunks
        ]
        projected_gold["case_ids"] = kept_case_ids
        projection = projected_gold["expected_projection"]
        projection["key_data_case_ids"] = [
            value
            for value in projection["key_data_case_ids"]
            if value in selected_case_ids
        ]
        for field in (
            "research_question",
            "research_object",
            "method",
            "key_results",
            "author_conclusion",
            "boundary_conditions",
        ):
            projection[field]["case_ids"] = [
                value
                for value in projection[field]["case_ids"]
                if value in selected_case_ids
            ]
            projection[field]["anchor_ids"] = [
                value
                for value in projection[field]["anchor_ids"]
                if value not in all_case_chunks or value in selected_chunks
            ]
        return projected_pack, projected_gold

    @classmethod
    def _best_case_subset(
        cls,
        *,
        rows: Sequence[Mapping[str, Any]],
        gold_by_id: Mapping[str, Mapping[str, Any]],
        count: int,
        language: str,
        form_id: str,
        slot: int,
    ) -> list[str]:
        case_ids = [str(row["case_id"]) for row in rows]
        candidates: list[tuple[tuple[Any, ...], list[str]]] = []
        for combination in itertools.combinations(case_ids, count):
            gold_rows = [gold_by_id[case_id] for case_id in combination]
            difficulties = {
                value: sum(row.get("difficulty") == value for row in gold_rows)
                for value in ("easy", "medium", "hard")
            }
            families = {str(row.get("family")) for row in gold_rows}
            if language in {"zh", "ja"}:
                if difficulties != {"easy": 1, "medium": 1, "hard": 1}:
                    continue
                if len(families) != count:
                    continue
            elif families != {
                "D1_NUMERIC_UNIT_COMPARATOR",
                "D2_TIME_SAMPLE_SCOPE",
                "D3_MULTI_OCCURRENCE",
                "D4_MISSING_STATUS",
                "D5_SOURCE_AS_PRINTED",
                "D6_CLEAN_DIRECT_EXTRACTION",
            }:
                continue
            imbalance = max(difficulties.values()) - min(difficulties.values())
            minimum = min(difficulties.values())
            digest = _sha256(
                {
                    "revision": cls.SAMPLE_REVISION,
                    "form_id": form_id,
                    "slot": slot,
                    "language": language,
                    "case_ids": combination,
                }
            )
            candidates.append(((imbalance, -minimum, digest), list(combination)))
        if not candidates:
            raise ValueError("CARD_DISTILLER_LANGUAGE_SAMPLE_PARTITION_INVALID")
        candidates.sort(key=lambda row: row[0])
        return candidates[0][1]

    @classmethod
    def _exam_inputs(
        cls,
        *,
        pack: Any,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        sample_slot = cls._sample_slot(context)
        forms: dict[str, dict[str, Any]] = {}
        golds: dict[str, dict[str, Any]] = {}
        selection: dict[str, Any] = {}
        for form_id in ("A", "B"):
            source_form = _load_json(pack.member(f"forms/form_{form_id}.json"))
            source_gold = _load_json(
                pack.member(f"gold/form_{form_id}_gold.json")
            )
            gold_by_id = {
                str(row["case_id"]): row for row in source_gold["cases"]
            }
            form_case_by_id = {
                str(row["case_id"]): row for row in source_form["cases"]
            }
            case_chunk_by_id = {
                case_id: str(row["source_unit"]["chunk_id"])
                for case_id, row in form_case_by_id.items()
            }
            packs_by_language = {
                language: [
                    row
                    for row in source_form["packs"]
                    if row.get("source_language") == language
                ]
                for language in ("en", "zh", "ja")
            }
            gold_pack_by_id = {
                str(row["pack_id"]): row for row in source_gold["packs"]
            }
            english_packs = packs_by_language["en"]
            english_pair = [
                english_packs[(sample_slot - 1) % len(english_packs)],
                english_packs[sample_slot % len(english_packs)],
            ]
            english_rows = [
                form_case_by_id[str(case_id)]
                for pack_row in english_pair
                for case_id in pack_row["case_ids"]
            ]
            english_ids = cls._best_case_subset(
                rows=english_rows,
                gold_by_id=gold_by_id,
                count=18,
                language="en",
                form_id=form_id,
                slot=sample_slot,
            )
            zh_pack = packs_by_language["zh"][(sample_slot - 1) % 2]
            ja_pack = packs_by_language["ja"][
                (sample_slot - 1 + (0 if form_id == "A" else 1)) % 2
            ]
            zh_rows = [
                form_case_by_id[str(case_id)] for case_id in zh_pack["case_ids"]
            ]
            ja_rows = [
                form_case_by_id[str(case_id)] for case_id in ja_pack["case_ids"]
            ]
            zh_ids = cls._best_case_subset(
                rows=zh_rows,
                gold_by_id=gold_by_id,
                count=3,
                language="zh",
                form_id=form_id,
                slot=sample_slot,
            )
            ja_ids = cls._best_case_subset(
                rows=ja_rows,
                gold_by_id=gold_by_id,
                count=3,
                language="ja",
                form_id=form_id,
                slot=sample_slot,
            )
            selected_ids = set([*english_ids, *zh_ids, *ja_ids])
            selected_pack_ids = [
                str(row["pack_id"])
                for row in [*english_pair, zh_pack, ja_pack]
            ]
            projected_packs: list[dict[str, Any]] = []
            projected_gold_packs: list[dict[str, Any]] = []
            for source_pack in source_form["packs"]:
                pack_id = str(source_pack["pack_id"])
                if pack_id not in selected_pack_ids:
                    continue
                projected_pack, projected_gold = cls._prune_pack(
                    pack=source_pack,
                    gold_pack=gold_pack_by_id[pack_id],
                    selected_case_ids=selected_ids,
                    case_chunk_by_id=case_chunk_by_id,
                )
                projected_packs.append(projected_pack)
                projected_gold_packs.append(projected_gold)
            selected_cases = [
                deepcopy(row)
                for row in source_form["cases"]
                if str(row["case_id"]) in selected_ids
            ]
            selected_case_ids = [str(row["case_id"]) for row in selected_cases]
            selected_gold_cases = [
                deepcopy(row)
                for row in source_gold["cases"]
                if str(row["case_id"]) in selected_ids
            ]
            if (
                len(selected_cases) != 24
                or len(projected_packs) != 4
                or [str(row["case_id"]) for row in selected_gold_cases]
                != selected_case_ids
            ):
                raise ValueError("CARD_DISTILLER_LANGUAGE_SAMPLE_INVALID")
            form = deepcopy(source_form)
            form["packs"] = projected_packs
            form["cases"] = selected_cases
            form["successor_projection"] = {
                "revision": cls.SAMPLE_REVISION,
                "source_content_sha256": source_form.get("content_sha256"),
            }
            gold = deepcopy(source_gold)
            gold["packs"] = projected_gold_packs
            gold["cases"] = selected_gold_cases
            gold["successor_projection"] = {
                "revision": cls.SAMPLE_REVISION,
                "source_content_sha256": source_gold.get("content_sha256"),
            }
            forms[form_id] = form
            golds[form_id] = gold
            selection[form_id] = {
                "selected_pack_ids": selected_pack_ids,
                "selected_case_ids": selected_case_ids,
                "language_counts": {"en": 18, "zh": 3, "ja": 3},
            }
        manifest = {
            "schema_version": cls.SAMPLE_REVISION,
            "sample_slot": sample_slot,
            "rotation_slots": cls.SAMPLE_ROTATION_SLOTS,
            "selected_case_count_per_form": 24,
            "selected_pack_count_per_form": 4,
            "language_counts_per_form": {"en": 18, "zh": 3, "ja": 3},
            "primary_language": "en",
            "diagnostic_languages": ["zh", "ja"],
            "cross_language_aggregation_forbidden": True,
            "selection": selection,
            "gold_in_subject_request": False,
        }
        manifest["manifest_sha256"] = _sha256(manifest)
        return {"forms": forms, "golds": golds, "sample_metadata": manifest}

    @staticmethod
    def _language_score_from_details(
        *,
        protocol: Any,
        details: Sequence[Mapping[str, Any]],
        primary: bool,
    ) -> dict[str, Any]:
        if not details:
            raise ValueError("CARD_DISTILLER_LANGUAGE_SCORE_EMPTY")
        values = [float(row["score"]) for row in details]
        critical_count = sum(bool(row.get("critical_reasons")) for row in details)
        major_count = sum(bool(row.get("major_reasons")) for row in details)
        family_means: dict[str, float] = {}
        for family in protocol.FAMILIES:
            family_values = [
                float(row["score"])
                for row in details
                if row.get("family") == family
            ]
            if family_values:
                family_means[family] = statistics.fmean(family_values)
        if primary:
            if set(family_means) != set(protocol.FAMILIES):
                raise ValueError("CARD_DISTILLER_ENGLISH_FAMILY_COVERAGE_INVALID")
            hard = [
                float(row["score"])
                for row in details
                if row.get("difficulty") == "hard"
            ]
            if not hard:
                raise ValueError("CARD_DISTILLER_ENGLISH_HARD_SET_EMPTY")
            tail_count = max(1, math.ceil(len(values) * 0.10))
            layers = {
                "overall_mean": statistics.fmean(values),
                "hard_mean": statistics.fmean(hard),
                "weakest_family": min(family_means.values()),
                "bottom_ten_percent": statistics.fmean(
                    sorted(values)[:tail_count]
                ),
                "structure": 100.0,
            }
            contributions = {
                key: protocol.piecewise_contribution(key, layers[key])
                for key in (
                    "overall_mean",
                    "hard_mean",
                    "weakest_family",
                    "bottom_ten_percent",
                )
            }
            contributions["structure"] = 15.0
            score = sum(contributions.values())
            caps: list[str] = []
            if critical_count and score > 59.0:
                score = 59.0
                caps.append("CATASTROPHIC_SCIENTIFIC_FAILURE_CAP_59")
            if major_count and score >= 90.0:
                score = min(score, 89.99)
                caps.append("MAJOR_FINDING_CEILING_BELOW_90")
            return {
                "score": round(score, 2),
                "case_count": len(details),
                "critical_count": critical_count,
                "major_count": major_count,
                "family_means": {
                    key: round(value, 2) for key, value in family_means.items()
                },
                "layers": {key: round(value, 2) for key, value in layers.items()},
                "score_caps_applied": caps,
                "score_semantics": "RAW_CAPABILITY_WITHOUT_REPAIR_BURDEN",
            }
        return {
            "score": round(statistics.fmean(values), 2),
            "case_count": len(details),
            "critical_count": critical_count,
            "major_count": major_count,
            "family_means_present": {
                key: round(value, 2) for key, value in family_means.items()
            },
            "score_semantics": "DESCRIPTIVE_CASE_MEAN_ONLY",
        }

    def _stage_score_projection(
        self,
        *,
        protocol: Any,
        forms: Mapping[str, Mapping[str, Any]],
        initial_scores: Mapping[str, Mapping[str, Any]],
        final_scores: Mapping[str, Mapping[str, Any]],
        repair_rounds: int,
    ) -> dict[str, Any]:
        language_by_case = {
            str(row["case_id"]): str(row["source_language"])
            for form in forms.values()
            for row in form["cases"]
        }

        def project(
            scores: Mapping[str, Mapping[str, Any]], language: str, primary: bool
        ) -> dict[str, Any]:
            by_form = {
                form_id: self._language_score_from_details(
                    protocol=protocol,
                    details=[
                        row
                        for row in scores[form_id]["details"]
                        if language_by_case.get(str(row.get("case_id")))
                        == language
                    ],
                    primary=primary,
                )
                for form_id in ("A", "B")
            }
            return {
                "score": round(
                    statistics.fmean(row["score"] for row in by_form.values()),
                    2,
                ),
                "forms": by_form,
                "case_count": sum(row["case_count"] for row in by_form.values()),
                "critical_count": sum(
                    row["critical_count"] for row in by_form.values()
                ),
                "major_count": sum(row["major_count"] for row in by_form.values()),
            }

        initial_en = project(initial_scores, "en", True)
        final_en = project(final_scores, "en", True)
        final_zh = project(final_scores, "zh", False)
        final_ja = project(final_scores, "ja", False)
        primary_case_ids = sorted(
            case_id
            for case_id, language in language_by_case.items()
            if language == "en"
        )
        quality = (
            "PASS"
            if final_en["critical_count"] == 0
            and final_en["score"] >= PASSING_SCORE
            else "FAIL"
        )
        return {
            "score_override": final_en["score"],
            "first_pass_score_override": initial_en["score"],
            "quality_verdict_override": quality,
            "primary_case_ids": primary_case_ids,
            "primary_language_track": "en",
            "language_tracks": {
                "en": {
                    "role": "PRODUCTION_PRIMARY",
                    "first_pass": initial_en,
                    "post_repair": final_en,
                    "affects_primary_score": True,
                    "affects_model_fail": True,
                },
                "zh": {
                    "role": "DIAGNOSTIC_ONLY",
                    "post_repair": final_zh,
                    "affects_primary_score": False,
                    "affects_model_fail": False,
                },
                "ja": {
                    "role": "DIAGNOSTIC_ONLY",
                    "post_repair": final_ja,
                    "affects_primary_score": False,
                    "affects_model_fail": False,
                },
            },
            "cross_language_aggregation_forbidden": True,
            "repair_burden_affects_capability_score": False,
            "repair_burden_reported_separately": True,
            "repair_rounds_used": repair_rounds,
        }

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        plan = super().plan(node, target, context)
        if plan.get("status") != "READY":
            return plan
        plan.pop("plan_sha256", None)
        plan.update(
            reason="Quality_LANGUAGE_BANK_REFERENCE_REGRESSION_READY",
            base_model_calls=6 if plan["profile_kind"] == "LOCAL" else 4,
            sample_case_count_per_form=24,
            sample_pack_count_per_form=4,
            sample_rotation_slots=self.SAMPLE_ROTATION_SLOTS,
            sample_language_counts_per_form={"en": 18, "zh": 3, "ja": 3},
            primary_language_track="en",
            diagnostic_language_tracks=["zh", "ja"],
            cross_language_aggregation_forbidden=True,
            repair_burden_affects_capability_score=False,
            second_round_penalty_semantics=(
                "REPORTED_SEPARATELY_NOT_DEDUCTED_FROM_CAPABILITY_SCORE"
            ),
        )
        plan["plan_sha256"] = _sha256(plan)
        return plan


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
        self._scratch_root = Path(scratch_root).resolve()
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
                issues, _fields, severity = exam._reviewer_exam_issue_metadata(
                    details[case_id]
                )
                issue_categories.update(issues)
                severity_categories.add(severity)
            form_case_count = len(exam.form_cases(forms[form_id])[1])
            nonlinear = exam._reviewer_exam_form_penalty(
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
                    "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE"
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
                else ("OLLAMA_LOOPBACK" if is_local else "CODEX_CLI_SUBSCRIPTION")
            ),
            "region": (
                "PROVIDER_MANAGED_UNDISCLOSED"
                if is_api
                else ("LOCAL_MACHINE" if is_local else "OPENAI_MANAGED_UNDISCLOSED")
            ),
            "egress": (
                "PROVIDER_API"
                if is_api
                else ("LOOPBACK_ONLY" if is_local else "CODEX_CLI")
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
                        state["token_usage"][field] += value
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
        state["token_usage"]["prompt_tokens"] += int(
            receipt["token_usage"]["prompt_tokens"]
        )
        state["token_usage"]["completion_tokens"] += int(
            receipt["token_usage"]["completion_tokens"]
        )
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
        run_root = self._scratch_root / "workflow_exams" / run_id
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
                "returned_model": requested_model,
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
                "returned_model": requested_model,
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


class QualityAnalysisExamExecutor(QualityCardReviewerExamSuccessorExecutor):
    """Transport the frozen Quality analysis A/B exam through API/CLI/local."""

    EXECUTOR_REF = (
        "Desktop_MODEL_EXAM_SUCCESSOR_Quality_ANALYSIS_PRIMARY_EXECUTOR_V5_"
        "NO_LENGTH_INVALIDATION"
    )
    MODE = "LIVE_Quality_ANALYSIS_SUCCESSOR_REFERENCE_REGRESSION"
    API_MAXIMUM_REQUEST_ATTEMPTS = 25
    CLI_MAXIMUM_REQUEST_ATTEMPTS = 25
    LOCAL_MAXIMUM_REQUEST_ATTEMPTS = 40
    API_BUDGET_CAP_CNY = 20.0
    TARGETS_PER_SHARD = 6
    SAMPLE_ROTATION_SLOTS = 12
    SEMANTIC_REPAIR_ROUND_LIMIT = 2
    PANELS_REQUIRED_FOR_CACHE = 3
    PANEL_COHORT_COUNT = 4
    EXAM_CATEGORY_ID = ANALYSIS_PRIMARY_EXAM_CATEGORY_ID

    @staticmethod
    def _profile_kind(target: Mapping[str, Any]) -> str | None:
        kind = target.get("kind")
        if kind == "API":
            if (
                re.fullmatch(
                    r"deepseek(?:apikey[a-z0-9]*)?",
                    _compact(target.get("provider")),
                )
                and target.get("model_name") == "deepseek-v4-pro"
                and target.get("thinking") is True
                and target.get("tier") == "pro"
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "API"
            if (
                target.get("analysis_primary_exam_eligible") is True
                and target.get("catalog_model_identity_status") == "AVAILABLE"
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
                and isinstance(target.get("provider"), str)
                and bool(target.get("provider"))
                and isinstance(target.get("model_name"), str)
                and bool(target.get("model_name"))
                and isinstance(target.get("thinking"), bool)
                and isinstance(target.get("tier"), str)
                and bool(target.get("tier"))
            ):
                return "API"
            return None
        if kind == "CLI":
            if (
                _compact(target.get("adapter_id")) == "codexcli"
                and target.get("model_name") == "gpt-5.6-sol"
                and target.get("thinking_mode") in {"high", "xhigh", "max"}
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "CLI"
            return None
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

    def _analysis_pricing_binding(
        self,
        target: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if target is not None and target.get("analysis_primary_exam_eligible") is True:
            snapshot = target.get("pricing_snapshot_date")
            profile_sha256 = target.get("pricing_profile_sha256")
            budget_cap = target.get("exam_budget_cap_cny")
            pricing_role = target.get("pricing_role")
            if (
                not isinstance(snapshot, str)
                or not snapshot
                or not isinstance(profile_sha256, str)
                or not re.fullmatch(r"[A-F0-9]{64}", profile_sha256)
                or isinstance(budget_cap, bool)
                or not isinstance(budget_cap, (int, float))
                or not 0 < float(budget_cap) <= 100_000
                or not isinstance(pricing_role, str)
                or not pricing_role
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_PRICING_PROFILE_INVALID")
            return {
                "snapshot_date": snapshot,
                "sha256": profile_sha256,
                "budget_cap_cny": float(budget_cap),
                "pricing_role": pricing_role,
            }
        profile = _load_json(self._pricing_profile_path)
        high = profile.get("high_cost")
        if (
            profile.get("schema_version")
            != "model_evaluation-model_evaluation-card-distiller-flash-to-pro-public-profile-v1"
            or not isinstance(high, Mapping)
            or high.get("model_id") != "deepseek-v4-pro"
            or float(high.get("budget_cap_cny", -1)) != self.API_BUDGET_CAP_CNY
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_PRICING_PROFILE_INVALID")
        return {
            "snapshot_date": profile.get("profile_snapshot_date"),
            "sha256": _file_sha256(self._pricing_profile_path),
            "budget_cap_cny": float(high["budget_cap_cny"]),
            "pricing_role": "HIGH_COST_DEEPSEEK_PRO",
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
        # Reuse the Execution/7 marker-safe local projection.  Its reviewer
        # contract expects an inline schema, so supply a validator-only dummy
        # and remove it after the marker check; the real response schema stays
        # on the structured transport rather than being duplicated in prompt.
        augmented = deepcopy(dict(request))
        augmented["output_schema"] = {
            "type": "object",
            "additionalProperties": True,
        }
        projected = super()._subject_request_projection(target, augmented)
        projected.pop("output_schema", None)
        projected.pop("request_payload_hash", None)
        projected["request_payload_hash"] = _sha256(projected)
        return projected

    @classmethod
    def _sample_slot(cls, context: Mapping[str, Any]) -> int:
        explicit = context.get("workflow_exam_sample_slot")
        if explicit is not None:
            if (
                isinstance(explicit, bool)
                or not isinstance(explicit, int)
                or explicit < 1
                or explicit > cls.SAMPLE_ROTATION_SLOTS
            ):
                raise ValueError("ANALYSIS_PRIMARY_SAMPLE_SLOT_INVALID")
            return explicit
        ordinal = context.get("workflow_exam_attempt_ordinal", 1)
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            raise ValueError("ANALYSIS_PRIMARY_ATTEMPT_ORDINAL_INVALID")
        return ((ordinal - 1) % cls.SAMPLE_ROTATION_SLOTS) + 1

    @classmethod
    def _panel_cohort_slots(cls, sample_slot: int) -> list[int]:
        if (
            isinstance(sample_slot, bool)
            or not isinstance(sample_slot, int)
            or sample_slot < 1
            or sample_slot > cls.SAMPLE_ROTATION_SLOTS
        ):
            raise ValueError("ANALYSIS_PRIMARY_SAMPLE_SLOT_INVALID")
        base = (
            ((sample_slot - 1) // cls.PANELS_REQUIRED_FOR_CACHE)
            * cls.PANELS_REQUIRED_FOR_CACHE
            + 1
        )
        return list(range(base, base + cls.PANELS_REQUIRED_FOR_CACHE))

    @staticmethod
    def _panel_model_identity(target: Mapping[str, Any]) -> dict[str, Any]:
        model_name = target.get("model_name")
        return {
            "profile_kind": target.get("kind"),
            "requested_model": model_name,
            "returned_model": model_name,
            "provider": target.get("provider"),
            "adapter_id": target.get("adapter_id"),
            "endpoint_kind": target.get("endpoint_kind"),
            "thinking_mode": target.get("thinking_mode"),
            "thinking": target.get("thinking"),
            "tier": target.get("tier"),
            "model_digest": target.get("model_digest"),
        }

    @classmethod
    def _panel_cohort_basis(cls, reference_pack_sha256: str) -> dict[str, Any]:
        return {
            "category_id": cls.EXAM_CATEGORY_ID,
            "reference_pack_sha256": reference_pack_sha256,
            "sample_revision": ANALYSIS_PRIMARY_SUCCESSOR_SAMPLE_REVISION,
            "subject_projection_revision": (
                ANALYSIS_PRIMARY_SUBJECT_PROJECTION_REVISION
            ),
            "executor_ref": cls.EXECUTOR_REF,
        }

    def _completed_panel_records(
        self,
        *,
        target: Mapping[str, Any],
        cohort_basis: Mapping[str, Any],
        cohort_slots: list[int],
    ) -> list[dict[str, Any]]:
        workflow_root = self._scratch_root / "workflow_exams"
        if not workflow_root.is_dir():
            return []
        expected_identity = self._panel_model_identity(target)
        candidates: list[tuple[int, int, str, dict[str, Any]]] = []
        for path in workflow_root.glob(
            "workflow-exam-*/panel_scores/panel_record.json"
        ):
            try:
                record = _load_json(path)
                stored_hash = record.get("panel_record_sha256")
                unsigned = {
                    key: value
                    for key, value in record.items()
                    if key != "panel_record_sha256"
                }
                if (
                    record.get("schema_version")
                    != "DesktopAnalysisPrimaryPanelRecord-v1"
                    or not isinstance(stored_hash, str)
                    or stored_hash != _sha256(unsigned)
                    or record.get("cohort_basis") != dict(cohort_basis)
                    or record.get("model_identity") != expected_identity
                    or record.get("sample_slot") not in cohort_slots
                    or record.get("single_panel_cache_eligible") is not False
                ):
                    continue
                slot = int(record["sample_slot"])
                candidates.append(
                    (
                        slot,
                        path.stat().st_mtime_ns,
                        str(path.resolve()),
                        record,
                    )
                )
            except (OSError, ValueError, TypeError):
                continue
        selected: dict[int, tuple[int, str, dict[str, Any]]] = {}
        for slot, modified, path_text, record in sorted(
            candidates, key=lambda row: (row[0], row[1], row[2])
        ):
            selected.setdefault(slot, (modified, path_text, record))
        return [
            deepcopy(selected[slot][2])
            for slot in sorted(selected)
        ]

    def _runtime_panel_selection(
        self,
        *,
        context: Mapping[str, Any],
        target: Mapping[str, Any],
        cohort_basis: Mapping[str, Any],
    ) -> dict[str, Any]:
        explicit = (
            "workflow_exam_sample_slot" in context
            or "workflow_exam_attempt_ordinal" in context
        )
        if explicit:
            slot = self._sample_slot(context)
            cohort_slots = self._panel_cohort_slots(slot)
            records = self._completed_panel_records(
                target=target,
                cohort_basis=cohort_basis,
                cohort_slots=cohort_slots,
            )
            return {
                "sample_slot": slot,
                "cohort_slots": cohort_slots,
                "existing_panel_count": len(records),
                "state_driven_rotation": False,
            }
        cohort_slots = [1, 2, 3]
        records = self._completed_panel_records(
            target=target,
            cohort_basis=cohort_basis,
            cohort_slots=cohort_slots,
        )
        completed = {int(record["sample_slot"]) for record in records}
        missing = [slot for slot in cohort_slots if slot not in completed]
        slot = missing[0] if missing else cohort_slots[-1]
        return {
            "sample_slot": slot,
            "cohort_slots": cohort_slots,
            "existing_panel_count": len(records),
            "state_driven_rotation": True,
            "cohort_already_complete": not missing,
        }

    @staticmethod
    def _project_evidence_text(text: str) -> str:
        if not isinstance(text, str) or not text:
            raise ValueError("ANALYSIS_PRIMARY_EVIDENCE_TEXT_INVALID")
        marker = " Scope record for "
        if text.count(marker) != 1:
            raise ValueError("ANALYSIS_PRIMARY_SCOPE_MARKER_INVALID")
        prefix, remainder = text.split(marker, 1)
        boundary = (
            "The unit does not authorize a broader population, a longer time "
            "window, or a stronger causal verb than it explicitly reports."
        )
        suffix = ""
        boundary_index = remainder.find(boundary)
        if boundary_index >= 0:
            suffix = remainder[boundary_index + len(boundary) :].strip()
        return prefix.strip() + ((" " + suffix) if suffix else "")

    @classmethod
    def _exam_inputs(
        cls,
        *,
        pack: Any,
        source_schema: Mapping[str, Any],
        context: Mapping[str, Any],
        protocol: Any,
    ) -> dict[str, Any]:
        sample_slot = cls._sample_slot(context)
        forms: dict[str, dict[str, Any]] = {}
        golds: dict[str, dict[str, Any]] = {}
        selection: dict[str, list[dict[str, Any]]] = {}
        difficulty_cycle = ("hard", "medium", "easy")
        cohort_index = (sample_slot - 1) // cls.PANELS_REQUIRED_FOR_CACHE
        panel_index = (sample_slot - 1) % cls.PANELS_REQUIRED_FOR_CACHE
        family_order = list(protocol.FAMILIES)
        for form_id in ("A", "B"):
            source_form = _load_json(pack.member(f"forms/form_{form_id}.json"))
            source_gold = _load_json(
                pack.member(f"gold/form_{form_id}_gold.json")
            )
            form_by_id = {
                str(row["case_id"]): row for row in source_form.get("cases", [])
            }
            gold_by_id = {
                str(row["case_id"]): row for row in source_gold.get("cases", [])
            }
            selected_ids: list[str] = []
            selected_meta: list[dict[str, Any]] = []
            for family_index, family in enumerate(family_order):
                selected_difficulty = difficulty_cycle[
                    (panel_index + family_index) % len(difficulty_cycle)
                ]
                family_rows = [
                    row
                    for row in source_gold.get("cases", [])
                    if isinstance(row, Mapping)
                    and row.get("family") == family
                    and row.get("difficulty") == selected_difficulty
                ]
                family_rows.sort(key=lambda row: str(row.get("case_id")))
                if len(family_rows) < cls.PANELS_REQUIRED_FOR_CACHE:
                    raise ValueError(
                        "ANALYSIS_PRIMARY_FAMILY_DIFFICULTY_POPULATION_INVALID"
                    )
                index = (cohort_index + family_index) % len(family_rows)
                selected = family_rows[index]
                case_id = str(selected["case_id"])
                selected_ids.append(case_id)
                selected_meta.append(
                    {
                        "case_id": case_id,
                        "family": family,
                        "difficulty": selected.get("difficulty"),
                    }
                )
            if len(selected_ids) != 6 or len(set(selected_ids)) != 6:
                raise ValueError("ANALYSIS_PRIMARY_SAMPLE_INVALID")
            projected_cases: list[dict[str, Any]] = []
            for case_id in selected_ids:
                case = deepcopy(dict(form_by_id[case_id]))
                for unit in case.get("evidence_units", []):
                    unit["text"] = cls._project_evidence_text(str(unit["text"]))
                projected_cases.append(case)
            form = deepcopy(dict(source_form))
            form["cases"] = projected_cases
            form["subject_projection"] = {
                "revision": ANALYSIS_PRIMARY_SUBJECT_PROJECTION_REVISION,
                "removed_content": "REDUNDANT_SCOPE_BOILERPLATE_ONLY",
                "structured_scope_fields_preserved": True,
                "variant_specific_suffix_preserved": True,
            }
            gold = deepcopy(dict(source_gold))
            gold["cases"] = [deepcopy(gold_by_id[case_id]) for case_id in selected_ids]
            forms[form_id] = form
            golds[form_id] = gold
            selection[form_id] = selected_meta
        # Length is not a quality or structural-failure dimension.  Keep the
        # Core mean x2 value as a workload diagnostic, but remove field
        # maxLength from the subject/scoring schema so an otherwise complete
        # answer is retained and scored without a paid compression repair.
        # The accepted Quality protocol is immutable and still requires a
        # positive compatibility value.  Reuse it to build the schema, then
        # remove only its legacy text-length keywords in this successor
        # adapter.  Newer protocol copies accept ``None`` directly, but this
        # path deliberately remains compatible with the frozen Quality asset.
        schema = protocol.successor_output_schema(
            deepcopy(dict(source_schema)),
            text_max_length=512,
        )
        for artifact, field_name in (
            ("claim_map", "claim_text"),
            ("analysis_summary", "summary_text"),
        ):
            schema["properties"][artifact]["items"]["properties"][
                field_name
            ].pop("maxLength", None)
        for artifact in ("claim_map", "analysis_summary"):
            schema["properties"][artifact]["minItems"] = 6
            schema["properties"][artifact]["maxItems"] = 6
        manifest = {
            "schema_version": ANALYSIS_PRIMARY_SUCCESSOR_SAMPLE_REVISION,
            "sample_slot": sample_slot,
            "panel_cohort_slots": cls._panel_cohort_slots(sample_slot),
            "panels_required_for_cache": cls.PANELS_REQUIRED_FOR_CACHE,
            "panel_evidence_only": True,
            "single_panel_cache_eligible": False,
            "rotation_slots": cls.SAMPLE_ROTATION_SLOTS,
            "selected_case_count_per_form": 6,
            "selected_family_count_per_form": 6,
            "difficulty_rotation": (
                "LATIN_2H_2M_2E_PER_PANEL_AND_HME_PER_FAMILY_PER_COHORT"
            ),
            "selection": selection,
            "projection_revision": ANALYSIS_PRIMARY_SUBJECT_PROJECTION_REVISION,
            "coverage_semantics": (
                "ONE_FROZEN_TRANSPORT_PANEL; SCORE_ONLY_AFTER_THREE_"
                "NONOVERLAPPING_LATIN_PANELS_AGGREGATE_BEFORE_TAIL;_"
                "EACH_FAMILY_HAS_EASY_MEDIUM_HARD"
            ),
            "gold_in_subject_request": False,
        }
        manifest["manifest_sha256"] = _sha256(manifest)
        return {
            "forms": forms,
            "golds": golds,
            "schema": schema,
            "sample_metadata": manifest,
        }

    def _quality(self) -> dict[str, Any]:
        if self._modules is None:
            names = {
                "protocol": "analysis_exam_protocol",
                "exam": "analysis_exam",
                "reference": "reference_exam_pack",
            }
            self._modules = {
                key: importlib.import_module(f"{self._quality_package}.{suffix}")
                for key, suffix in names.items()
            }
        return self._modules

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        if node.get("node_id") != "analysis":
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        profile_kind = self._profile_kind(target)
        if profile_kind is None:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        try:
            preload_root = context.get(
                "workflow_exam_preloaded_initial_artifacts"
            )
            preload_binding = context.get(
                "workflow_exam_preload_binding_sha256"
            )
            if preload_root is not None and (
                not isinstance(preload_root, Mapping)
                or not isinstance(preload_binding, str)
                or preload_binding != _sha256(preload_root)
            ):
                raise ValueError("ANALYSIS_PRELOAD_CONTEXT_BINDING_INVALID")
            modules = self._quality()
            pack = modules["reference"].load_analysis_reference_exam_pack(
                self._reference_pack_root
            )
            cohort_basis = self._panel_cohort_basis(pack.pack_fingerprint)
            runtime_selection = self._runtime_panel_selection(
                context=context,
                target=target,
                cohort_basis=cohort_basis,
            )
            effective_context = {
                **dict(context),
                "workflow_exam_sample_slot": runtime_selection["sample_slot"],
            }
            source_schema = _load_json(
                pack.member("schemas/analysis_primary_output.schema.json")
            )
            inputs = self._exam_inputs(
                pack=pack,
                source_schema=source_schema,
                context=effective_context,
                protocol=modules["protocol"],
            )
            pricing = (
                self._analysis_pricing_binding(target)
                if profile_kind == "API"
                else None
            )
            (
                _model_binding,
                request_max_output_tokens,
                output_limit_mode,
            ) = self._structured_output_policy(target)
        except (ImportError, OSError, ValueError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_ASSETS_INVALID")
        manifest = dict(pack.manifest)
        if (
            manifest.get("classification") != "REFERENCE_EXAM_PACK"
            or manifest.get("reference_regression_only") is not True
            or manifest.get("qualification_eligible") is not False
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_PACK_NOT_ELIGIBLE")
        prompt_metrics: dict[str, dict[str, list[dict[str, int]]]] = {}
        maximum_effective_prompt_chars = 0
        for form_id in ("A", "B"):
            prompt_metrics[form_id] = {}
            for artifact in ("claim_map", "analysis_summary"):
                artifact_metrics: list[dict[str, int]] = []
                for shard in self._shards(
                    inputs["forms"][form_id],
                    form_id=form_id,
                    artifact=artifact,
                    profile_kind=profile_kind,
                ):
                    preload = self._preloaded_initial_artifact_rows(
                        context=context,
                        target=target,
                        sample_slot=int(runtime_selection["sample_slot"]),
                        form_id=form_id,
                        artifact=artifact,
                        target_case_ids=list(shard["target_ids"]),
                    )
                    target_ids = list(preload["missing_case_ids"])
                    if not target_ids:
                        continue
                    case_by_id = {
                        str(row["case_id"]): deepcopy(dict(row))
                        for row in shard["context_form"]["cases"]
                    }
                    context_form = deepcopy(dict(shard["context_form"]))
                    context_form["cases"] = [
                        deepcopy(case_by_id[case_id])
                        for case_id in target_ids
                    ]
                    schema = modules["protocol"].shard_artifact_schema(
                        inputs["schema"],
                        artifact,
                        target_count=len(target_ids),
                    )
                    prompt = modules["protocol"].build_artifact_prompt(
                        context_form,
                        artifact,
                        target_case_ids=target_ids,
                    )
                    request = {
                        "phase": "ANALYSIS_REFERENCE_EXAM",
                        "artifact": artifact,
                        "target_case_ids": target_ids,
                        "subject_prompt": prompt,
                    }
                    metrics = _effective_structured_prompt_metrics(
                        _serialized_request(
                            self._subject_request_projection(target, request)
                        ),
                        schema,
                    )
                    artifact_metrics.append(metrics)
                    maximum_effective_prompt_chars = max(
                        maximum_effective_prompt_chars,
                        metrics["effective_prompt_chars"],
                    )
                prompt_metrics[form_id][artifact] = artifact_metrics
        base_calls = sum(
            len(rows)
            for form_metrics in prompt_metrics.values()
            for rows in form_metrics.values()
        )
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA,
            "status": "READY",
            "mode": self.MODE,
            "reason": (
                "ANALYSIS_PRIMARY_THREE_PANEL_COHORT_REUSE_READY"
                if runtime_selection.get("cohort_already_complete", False)
                else "Quality_REFERENCE_REGRESSION_READY"
            ),
            "node_id": "analysis",
            "executor_ref": self.EXECUTOR_REF,
            "profile_kind": profile_kind,
            "profile_ref": target["config_id"],
            "model_name": target["model_name"],
            "model_digest": target.get("model_digest"),
            "thinking_mode": target.get("thinking_mode"),
            "output_limit_policy": output_limit_mode,
            "request_max_output_tokens": request_max_output_tokens,
            "calibrated_reply_token_ceiling": (
                WORKFLOW_EXAM_REPLY_TOKEN_CEILING
            ),
            "output_limit_contract": workflow_exam_output_limit_contract(),
            "provider_max_output_tokens": target.get(
                "provider_max_output_tokens"
            ),
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": manifest.get("pack_id"),
            "reference_pack_revision": manifest.get("pack_revision"),
            "reference_pack_sha256": pack.pack_fingerprint,
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "forms": ["A", "B"],
            "base_model_calls": (
                0
                if runtime_selection.get("cohort_already_complete", False)
                else base_calls
            ),
            "initial_exact_progress_reuse_requested": (
                preload_root is not None
            ),
            "initial_exact_progress_reuse_binding_sha256": (
                preload_binding if preload_root is not None else None
            ),
            "maximum_model_calls": (
                0
                if runtime_selection.get("cohort_already_complete", False)
                else self._request_cap(profile_kind)
            ),
            "structural_retry_limit_per_shard": 1,
            "structural_retry_limit_per_semantic_repair_shard": 1,
            "structural_retry_semantics": (
                "TRANSPORT_CONTRACT_CLOSURE_NOT_SEMANTIC_REPAIR_ROUND"
            ),
            "semantic_repair_round_limit": self.SEMANTIC_REPAIR_ROUND_LIMIT,
            "second_semantic_repair_allowed": True,
            "second_round_scope": (
                "ROUND1_RESIDUAL_EXACT_SET_ONLY_NO_FULL_FORM_RETRY"
            ),
            "second_round_penalty_semantics": (
                "CUMULATIVE_Quality_ANALYSIS_NONLINEAR_PLUS_"
                "CATEGORY_VOLUME_BURDEN"
            ),
            "sharding_revision": "LONG_DOCUMENT_EXACT_SET_NO_OVERLAP_ADAPTER_V1",
            "sample_revision": inputs["sample_metadata"]["schema_version"],
            "sample_slot": inputs["sample_metadata"]["sample_slot"],
            "sample_manifest_sha256": inputs["sample_metadata"][
                "manifest_sha256"
            ],
            "sample_case_count_per_form": 6,
            "sample_family_count_per_form": 6,
            "sample_rotation_slots": self.SAMPLE_ROTATION_SLOTS,
            "state_driven_panel_rotation": runtime_selection[
                "state_driven_rotation"
            ],
            "existing_panel_count": runtime_selection[
                "existing_panel_count"
            ],
            "cohort_already_complete": runtime_selection.get(
                "cohort_already_complete", False
            ),
            "panel_cohort_slots": inputs["sample_metadata"][
                "panel_cohort_slots"
            ],
            "panels_required_for_cache": self.PANELS_REQUIRED_FOR_CACHE,
            "panel_evidence_only": True,
            "single_panel_cache_eligible": False,
            "stable_score_cache_eligible": False,
            "aggregate_before_tail_components_required": True,
            "subject_projection_revision": (
                ANALYSIS_PRIMARY_SUBJECT_PROJECTION_REVISION
            ),
            "anchor_normalization_revision": (
                ANALYSIS_PRIMARY_ANCHOR_NORMALIZATION_REVISION
            ),
            "anchor_normalization_semantics": (
                "GOLD_FREE_UNAMBIGUOUS_CASE_LOCAL_ALIAS_RECOVERY_"
                "DOES_NOT_INCREMENT_SEMANTIC_REPAIR_ROUNDS"
            ),
            "structural_repair_projection_revision": (
                ANALYSIS_PRIMARY_STRUCTURAL_REPAIR_PROJECTION_REVISION
            ),
            "semantic_repair_sharding_revision": (
                ANALYSIS_PRIMARY_SEMANTIC_REPAIR_SHARDING_REVISION
            ),
            "repair_materiality_revision": (
                ANALYSIS_REPAIR_MATERIALITY_REVISION
            ),
            "repair_acceptance_revision": (
                ANALYSIS_REPAIR_ACCEPTANCE_REVISION
            ),
            "transport_target_cases_per_shard": self.TARGETS_PER_SHARD,
            "initial_effective_prompt_metrics": prompt_metrics,
            "maximum_initial_effective_prompt_chars": (
                maximum_effective_prompt_chars
            ),
            "workload_profile": core_primary_workload_profile(),
            "text_length_policy": (
                "NO_LENGTH_BASED_INVALIDATION_OR_PAID_REPAIR"
            ),
            "text_field_schema_ceiling_chars": None,
            "paid_repair_for_field_length_only": False,
            "visible_output_reference_ceiling_chars": (
                Core_PRIMARY_ANALYSIS_OUTPUT_CHAR_LIMIT
            ),
            "visible_output_reference_semantics": "DIAGNOSTIC_ONLY",
            "prompt_reference_ceiling_chars": (
                Core_PRIMARY_ANALYSIS_PROMPT_CHAR_SURROGATE_LIMIT
            ),
            "prompt_reference_semantics": (
                "SHARDING_GUIDANCE_ONLY_NOT_FAILURE_OR_MODEL_REPAIR"
            ),
            "maximum_initial_effective_prompt_exceeds_reference": (
                maximum_effective_prompt_chars
                > Core_PRIMARY_ANALYSIS_PROMPT_CHAR_SURROGATE_LIMIT
            ),
            "capacity_overflow_quality_semantics": "NOT_ASSESSED",
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "exam_category_id": self.EXAM_CATEGORY_ID,
            "horizontal_comparison_eligible": False,
            "horizontal_comparison_pending_panel_cohort": True,
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "budget_cap_cny": (
                pricing["budget_cap_cny"] if pricing is not None else None
            ),
            "pricing_snapshot_date": (
                pricing["snapshot_date"] if pricing is not None else None
            ),
            "pricing_profile_sha256": (
                pricing["sha256"] if pricing is not None else None
            ),
            "pricing_role": (
                pricing["pricing_role"] if pricing is not None else None
            ),
            "cost_semantics": (
                "CACHE_REUSE_NO_EXTERNAL_CALL"
                if runtime_selection.get("cohort_already_complete", False)
                else (
                    "FROZEN_PROFILE_ESTIMATE_CAP"
                    if profile_kind == "API"
                    else (
                        "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE"
                        if profile_kind == "CLI"
                        else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
                    )
                )
            ),
            "authorization_schema_version": AUTHORIZATION_SCHEMA,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }
        comparison_basis = {
            "category_id": self.EXAM_CATEGORY_ID,
            "reference_pack_sha256": plan["reference_pack_sha256"],
            "sample_revision": plan["sample_revision"],
            "panel_cohort_slots": plan["panel_cohort_slots"],
            "panels_required_for_cache": plan["panels_required_for_cache"],
            "subject_projection_revision": plan[
                "subject_projection_revision"
            ],
            "executor_ref": self.EXECUTOR_REF,
        }
        comparison_sha256 = _sha256(comparison_basis)
        plan["comparison_binding_sha256"] = comparison_sha256
        plan["comparison_cohort_id"] = (
            f"ANALYSIS_PRIMARY_SUCCESSOR:{comparison_sha256}"
        )
        panel_binding = {
            **comparison_basis,
            "sample_slot": plan["sample_slot"],
            "sample_manifest_sha256": plan["sample_manifest_sha256"],
        }
        plan["panel_binding_sha256"] = _sha256(panel_binding)
        plan["plan_sha256"] = _sha256(plan)
        return plan

    @staticmethod
    def _shards(
        form: Mapping[str, Any],
        *,
        form_id: str,
        artifact: str,
        profile_kind: str,
    ) -> list[dict[str, Any]]:
        cases = form.get("cases")
        if not isinstance(cases, list) or not cases:
            raise ValueError("WORKFLOW_MODEL_EXAM_ANALYSIS_FORM_INVALID")
        case_by_id = {
            str(row.get("case_id")): row
            for row in cases
            if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)
        }
        ordered = [str(row.get("case_id")) for row in cases]
        if len(case_by_id) != len(cases) or len(set(ordered)) != len(cases):
            raise ValueError("WORKFLOW_MODEL_EXAM_ANALYSIS_FORM_INVALID")
        if profile_kind not in {"API", "CLI", "LOCAL"}:
            raise ValueError("WORKFLOW_MODEL_EXAM_ANALYSIS_PROFILE_INVALID")
        if artifact not in {"claim_map", "analysis_summary"}:
            raise ValueError("WORKFLOW_MODEL_EXAM_ANALYSIS_ARTIFACT_INVALID")
        target_count = QualityAnalysisExamExecutor.TARGETS_PER_SHARD
        shards: list[dict[str, Any]] = []
        for start in range(0, len(ordered), target_count):
            target_ids = ordered[start : start + target_count]
            target_form = deepcopy(dict(form))
            target_form["cases"] = [deepcopy(case_by_id[key]) for key in target_ids]
            shards.append(
                {
                    "target_ids": target_ids,
                    "target_form": target_form,
                    "context_form": deepcopy(target_form),
                }
            )
        return shards

    @staticmethod
    def _preloaded_initial_artifact_rows(
        *,
        context: Mapping[str, Any],
        target: Mapping[str, Any],
        sample_slot: int,
        form_id: str,
        artifact: str,
        target_case_ids: list[str],
    ) -> dict[str, Any]:
        """Reuse immutable completed rows and request only the missing cases."""

        missing_default = list(target_case_ids)
        preload_root = context.get("workflow_exam_preloaded_initial_artifacts")
        if preload_root is None:
            return {
                "rows": [],
                "reused_case_ids": [],
                "missing_case_ids": missing_default,
                "source_calls": [],
                "provider_call_required_for_reused_rows": False,
            }
        if not isinstance(preload_root, Mapping):
            raise ValueError("ANALYSIS_PRELOAD_ROOT_INVALID")
        entry = preload_root.get(f"{form_id}:{artifact}")
        if entry is None:
            return {
                "rows": [],
                "reused_case_ids": [],
                "missing_case_ids": missing_default,
                "source_calls": [],
                "provider_call_required_for_reused_rows": False,
            }
        if not isinstance(entry, Mapping):
            raise ValueError("ANALYSIS_PRELOAD_ENTRY_INVALID")
        rows = entry.get("rows")
        source_calls = entry.get("source_calls")
        if (
            entry.get("sample_slot") != sample_slot
            or entry.get("requested_model") != target.get("model_name")
            or entry.get("returned_model") != target.get("model_name")
            or not isinstance(rows, list)
            or not rows
            or entry.get("rows_sha256") != _sha256(rows)
            or not isinstance(source_calls, list)
            or not source_calls
        ):
            raise ValueError("ANALYSIS_PRELOAD_BINDING_INVALID")
        target_set = set(target_case_ids)
        row_ids: list[str] = []
        for row in rows:
            if not isinstance(row, Mapping) or not isinstance(
                row.get("case_id"), str
            ):
                raise ValueError("ANALYSIS_PRELOAD_ROWS_INVALID")
            row_ids.append(str(row["case_id"]))
        if (
            len(row_ids) != len(set(row_ids))
            or not set(row_ids).issubset(target_set)
        ):
            raise ValueError("ANALYSIS_PRELOAD_EXACT_SET_INVALID")
        for source_call in source_calls:
            if not isinstance(source_call, Mapping) or any(
                not isinstance(source_call.get(field), str)
                or not source_call.get(field)
                for field in (
                    "call_id",
                    "receipt_sha256",
                    "provider_output_sha256",
                )
            ):
                raise ValueError("ANALYSIS_PRELOAD_SOURCE_CALL_INVALID")
            for field in ("receipt_sha256", "provider_output_sha256"):
                if not re.fullmatch(r"[A-F0-9]{64}", str(source_call[field])):
                    raise ValueError("ANALYSIS_PRELOAD_SOURCE_HASH_INVALID")
        missing = [case_id for case_id in target_case_ids if case_id not in row_ids]
        return {
            "rows": deepcopy(rows),
            "reused_case_ids": row_ids,
            "missing_case_ids": missing,
            "source_calls": deepcopy(source_calls),
            "provider_call_required_for_reused_rows": False,
        }

    def _stable_provider_call(self, **kwargs: Any) -> dict[str, Any]:
        request = kwargs.get("request")
        response_schema = kwargs.get("response_schema")
        target = kwargs.get("target")
        if not all(
            isinstance(value, Mapping)
            for value in (request, response_schema, target)
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_ANALYSIS_CALL_INVALID")
        metrics = _effective_structured_prompt_metrics(
            _serialized_request(
                self._subject_request_projection(
                    target,  # type: ignore[arg-type]
                    request,  # type: ignore[arg-type]
                )
            ),
            response_schema,  # type: ignore[arg-type]
        )
        return super()._stable_provider_call(**kwargs)

    @staticmethod
    def _normalize_case_local_anchor_aliases(
        *,
        form: Mapping[str, Any],
        response: Mapping[str, Any],
        artifact: str,
    ) -> dict[str, Any]:
        """Recover only an unambiguous case-local ``Uxx`` anchor alias.

        The Quality Analysis schema binds every anchor to its case, for
        example ``S13AIE08-U01``.  Some otherwise exact structured models
        return the local suffix ``U01``.  This projection is Gold-free: it
        expands the suffix only when the current source case contains exactly
        that canonical anchor.  All ambiguous or unknown values remain
        untouched for the frozen Quality audit.
        """

        projected = deepcopy(dict(response))
        rows = projected.get(artifact)
        if artifact != "claim_map" or not isinstance(rows, list):
            return {
                "schema_version": (
                    ANALYSIS_PRIMARY_ANCHOR_NORMALIZATION_REVISION
                ),
                "normalized_response": projected,
                "normalization_count": 0,
                "actions": [],
                "gold_consulted": False,
                "provider_call_required": False,
                "semantic_repair_round_increment": 0,
            }
        case_by_id = {
            str(case.get("case_id")): case
            for case in form.get("cases", [])
            if isinstance(case, Mapping)
            and isinstance(case.get("case_id"), str)
        }
        anchor_fields = (
            "supporting_anchors",
            "contradicting_anchors",
            "qualification_anchors",
            "context_only_anchors",
            "lineage_invalid_anchors",
        )
        actions: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            case_id = row.get("case_id")
            case = case_by_id.get(str(case_id))
            if case is None:
                continue
            canonical_anchors = {
                str(unit.get("anchor"))
                for unit in case.get("evidence_units", [])
                if isinstance(unit, Mapping)
                and isinstance(unit.get("anchor"), str)
            }
            for field in anchor_fields:
                values = row.get(field)
                if not isinstance(values, list):
                    continue
                for index, observed in enumerate(values):
                    if (
                        not isinstance(observed, str)
                        or not re.fullmatch(r"U[0-9]{2,}", observed)
                    ):
                        continue
                    canonical = f"{case_id}-{observed}"
                    if canonical not in canonical_anchors:
                        continue
                    values[index] = canonical
                    actions.append(
                        {
                            "case_id": str(case_id),
                            "field": field,
                            "item_index": index,
                            "observed_anchor": observed,
                            "normalized_anchor": canonical,
                            "rule": (
                                "EXPAND_UNAMBIGUOUS_CASE_LOCAL_ANCHOR_SUFFIX"
                            ),
                            "gold_consulted": False,
                            "provider_call_required": False,
                            "semantic_repair_round_increment": 0,
                        }
                    )
        return {
            "schema_version": ANALYSIS_PRIMARY_ANCHOR_NORMALIZATION_REVISION,
            "normalized_response": projected,
            "normalization_count": len(actions),
            "actions": actions,
            "gold_consulted": False,
            "provider_call_required": False,
            "semantic_repair_round_increment": 0,
        }

    @staticmethod
    def _bounded_structural_residual_projection(
        audit: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Compact the exhaustive audit into a repair-only residual view."""

        by_case: dict[str, dict[str, Any]] = {}
        aggregate_issue_codes: set[str] = set()
        defects = audit.get("defects")
        if not isinstance(defects, list):
            defects = []
        for defect in defects:
            if not isinstance(defect, Mapping):
                continue
            code = defect.get("code")
            if not isinstance(code, str) or not code:
                continue
            case_id = defect.get("case_id")
            if not isinstance(case_id, str) or not case_id:
                aggregate_issue_codes.add(code)
                continue
            row = by_case.setdefault(
                case_id,
                {
                    "case_id": case_id,
                    "issue_codes": set(),
                    "failed_output_fields": set(),
                    "defect_locators": [],
                },
            )
            row["issue_codes"].add(code)
            field = defect.get("field")
            if isinstance(field, str) and field:
                row["failed_output_fields"].add(field)
            locator: dict[str, Any] = {"code": code}
            anchor = defect.get("anchor")
            if isinstance(anchor, str) and anchor:
                locator["anchor"] = anchor
            if isinstance(field, str) and field:
                locator["field"] = field
            for key in ("observed", "expected"):
                if key not in defect:
                    continue
                value = defect.get(key)
                if value is None or isinstance(value, (bool, int, float)):
                    locator[key] = value
                elif isinstance(value, str) and len(value) <= 256:
                    locator[key] = value
            repair_action = defect.get("repair_action")
            if isinstance(repair_action, str) and repair_action:
                locator["repair_action"] = repair_action
            if len(locator) > 1 and locator not in row["defect_locators"]:
                row["defect_locators"].append(locator)
        ordered_ids = [
            str(case_id)
            for case_id in audit.get("repair_exact_set", [])
            if isinstance(case_id, str)
        ]
        for case_id in sorted(by_case):
            if case_id not in ordered_ids:
                ordered_ids.append(case_id)
        rows = []
        for case_id in ordered_ids:
            row = by_case.get(case_id)
            if row is None:
                continue
            rows.append(
                {
                    "case_id": case_id,
                    "issue_codes": sorted(row["issue_codes"]),
                    "failed_output_fields": sorted(
                        row["failed_output_fields"]
                    ),
                    "defect_locators": deepcopy(row["defect_locators"]),
                }
            )
        return {
            "schema_version": (
                ANALYSIS_PRIMARY_STRUCTURAL_REPAIR_PROJECTION_REVISION
            ),
            "audit_status": audit.get("status"),
            "audit_defect_count": audit.get("defect_count"),
            "repair_exact_set": ordered_ids,
            "case_residuals": rows,
            "aggregate_issue_codes": sorted(aggregate_issue_codes),
            "full_audit_sent_to_provider": False,
            "gold_consulted": False,
        }

    @staticmethod
    def _analysis_wrong_sets(
        scores: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, list[str]]:
        return deepcopy(
            analysis_material_repair_targets(scores)["targets_by_form"]
        )

    @staticmethod
    def _analysis_cohort_raw_projection(
        score: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Project only scorer signals needed by the local panel aggregator.

        Expected-answer and observed-answer fields in the Quality raw detail are
        deliberately omitted so a panel receipt cannot become a Gold sidecar.
        """

        details = score.get("details")
        if not isinstance(details, list):
            raise ValueError("ANALYSIS_PANEL_RAW_DETAILS_REQUIRED")
        projected_details: list[dict[str, Any]] = []
        allowed = {
            "case_id",
            "family",
            "difficulty",
            "score",
            "critical_reasons",
            "axes",
            "anchor_role_accuracy",
        }
        for detail in details:
            if not isinstance(detail, Mapping):
                raise ValueError("ANALYSIS_PANEL_RAW_DETAIL_INVALID")
            projected_details.append(
                {
                    key: deepcopy(detail[key])
                    for key in allowed
                    if key in detail
                }
            )
        return {
            "sample_count": len(projected_details),
            "details": projected_details,
            "family_scores": deepcopy(dict(score.get("family_scores") or {})),
            "axis_scores": deepcopy(dict(score.get("axis_scores") or {})),
            "form_score": score.get("form_score"),
            "critical_any": bool(score.get("critical_any", False)),
            "critical_count": int(score.get("critical_count", 0)),
            "critical_findings": [
                {
                    "case_id": str(detail.get("case_id")),
                    "critical_reasons": deepcopy(
                        list(detail.get("critical_reasons") or [])
                    ),
                }
                for detail in details
                if isinstance(detail, Mapping)
                and detail.get("critical_reasons")
            ],
            "quality_hard_gate": score.get("quality_hard_gate"),
            "max_single_sample_effect_points": score.get(
                "max_single_sample_effect_points"
            ),
            "gold_fields_projected": False,
        }

    @staticmethod
    def _analysis_case_local_acceptance_merge(
        *,
        original_responses: Mapping[str, Mapping[str, Any]],
        candidate_responses: Mapping[str, Mapping[str, Any]],
        accepted_by_form: Mapping[str, list[str]],
    ) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for form_id in ("A", "B"):
            original = original_responses.get(form_id)
            candidate = candidate_responses.get(form_id)
            if not isinstance(original, Mapping) or not isinstance(
                candidate, Mapping
            ):
                raise ValueError("ANALYSIS_REPAIR_RESPONSE_MAPPING_REQUIRED")
            accepted = set(accepted_by_form.get(form_id, []))
            form_value: dict[str, Any] = {}
            for artifact in ("claim_map", "analysis_summary"):
                original_rows = original.get(artifact)
                candidate_rows = candidate.get(artifact)
                if not isinstance(original_rows, list) or not isinstance(
                    candidate_rows, list
                ):
                    raise ValueError("ANALYSIS_REPAIR_ARTIFACT_ARRAY_REQUIRED")
                candidate_by_id = {
                    str(row.get("case_id")): row
                    for row in candidate_rows
                    if isinstance(row, Mapping)
                    and isinstance(row.get("case_id"), str)
                }
                original_ids = [
                    str(row.get("case_id"))
                    for row in original_rows
                    if isinstance(row, Mapping)
                    and isinstance(row.get("case_id"), str)
                ]
                if (
                    len(original_ids) != len(original_rows)
                    or set(candidate_by_id) != set(original_ids)
                ):
                    raise ValueError("ANALYSIS_REPAIR_FULL_CASE_SET_MISMATCH")
                form_value[artifact] = [
                    deepcopy(
                        dict(
                            candidate_by_id[case_id]
                            if case_id in accepted
                            else original_rows[index]
                        )
                    )
                    for index, case_id in enumerate(original_ids)
                ]
            merged[form_id] = form_value
        return merged

    @staticmethod
    def _analysis_category_burden(
        *,
        exam: Any,
        forms: Mapping[str, Mapping[str, Any]],
        scores: Mapping[str, Mapping[str, Any]],
        exact_sets: Mapping[str, list[str]],
    ) -> dict[str, Any]:
        by_form: dict[str, dict[str, Any]] = {}
        for form_id in ("A", "B"):
            target_ids = list(exact_sets.get(form_id, []))
            details = {
                str(detail.get("case_id")): detail
                for detail in scores[form_id].get("details", [])
                if isinstance(detail, Mapping)
                and isinstance(detail.get("case_id"), str)
            }
            if any(case_id not in details for case_id in target_ids):
                raise ValueError("ANALYSIS_REPAIR_RESIDUAL_DETAIL_MISSING")
            issue_categories: set[str] = set()
            severity_categories: set[str] = set()
            artifact_categories: set[str] = set()
            for case_id in target_ids:
                issues, _failed_fields, artifacts, severity = (
                    exam._analysis_exam_issue_metadata(details[case_id])
                )
                issue_categories.update(issues)
                severity_categories.add(severity)
                artifact_categories.update(artifacts)
            case_count = len(forms[form_id]["cases"])
            penalty = exam._analysis_exam_form_penalty(
                form_case_count=case_count,
                wrong_case_count=len(target_ids),
                issue_categories=issue_categories,
                severity_categories=severity_categories,
                artifact_categories=artifact_categories,
            )
            by_form[form_id] = {
                "target_case_ids": target_ids,
                "target_case_count": len(target_ids),
                **deepcopy(dict(penalty)),
            }
        return {
            "schema_version": "AnalysisCategoryRepairBurden-v1",
            "by_form": by_form,
            "total_mean": round(
                sum(float(row["total"]) for row in by_form.values()) / 2.0,
                6,
            ),
            "volume_mean": round(
                sum(
                    float(row["wrong_item_volume"])
                    for row in by_form.values()
                )
                / 2.0,
                6,
            ),
            "coefficient_source": "Quality_ANALYSIS_EXAM_REPAIR_PENALTY_V1",
            "cross_category_coefficients_reused": False,
        }

    @staticmethod
    def _project_analysis_repair_schema(
        repair_schema: Mapping[str, Any],
        *,
        form_id: str,
        case_ids: list[str],
    ) -> dict[str, Any]:
        form_key = f"form_{form_id}"
        projected = deepcopy(dict(repair_schema))
        properties = projected.get("properties")
        if (
            not isinstance(properties, Mapping)
            or not isinstance(properties.get(form_key), Mapping)
            or not case_ids
            or len(case_ids) != len(set(case_ids))
        ):
            raise ValueError("ANALYSIS_REPAIR_SHARD_SCHEMA_INVALID")
        projected["required"] = [form_key]
        projected["properties"] = {
            form_key: deepcopy(dict(properties[form_key]))
        }
        try:
            artifact_properties = projected["properties"][form_key][
                "properties"
            ]
            for artifact in ("claim_map", "analysis_summary"):
                rows = artifact_properties[artifact]
                rows["minItems"] = len(case_ids)
                rows["maxItems"] = len(case_ids)
                rows["items"]["properties"]["case_id"] = {
                    "type": "string",
                    "enum": list(case_ids),
                }
        except (KeyError, TypeError) as error:
            raise ValueError(
                "ANALYSIS_REPAIR_SHARD_SCHEMA_INVALID"
            ) from error
        return projected

    def _run_analysis_semantic_repair(
        self,
        *,
        run_id: str,
        run_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        package: Mapping[str, Any],
        lifecycle_contract: Mapping[str, Any],
        round_number: int,
        structured_chat: Callable[..., Mapping[str, Any]],
    ) -> dict[str, Any]:
        contract = package.get("contract")
        repair_schema = package.get("repair_schema")
        repair_targets = package.get("repair_targets")
        if not all(
            isinstance(value, Mapping)
            for value in (contract, repair_schema, repair_targets)
        ):
            raise ValueError("ANALYSIS_REPAIR_PACKAGE_INVALID")
        assert isinstance(contract, Mapping)
        assert isinstance(repair_schema, Mapping)
        assert isinstance(repair_targets, Mapping)
        exam = self._quality()["exam"]
        exact_sets = contract.get("wrong_item_exact_set")
        if not isinstance(exact_sets, Mapping):
            raise ValueError("ANALYSIS_REPAIR_PACKAGE_INVALID")
        merged: dict[str, Any] = {}
        receipts: list[dict[str, Any]] = []
        for form_id in ("A", "B"):
            case_ids = exact_sets.get(form_id)
            targets = repair_targets.get(form_id)
            if not isinstance(case_ids, list) or not isinstance(targets, list):
                raise ValueError("ANALYSIS_REPAIR_PACKAGE_INVALID")
            if not case_ids:
                continue
            target_by_id = {
                str(row.get("case_id")): row
                for row in targets
                if isinstance(row, Mapping)
                and isinstance(row.get("case_id"), str)
            }
            if list(target_by_id) != case_ids:
                raise ValueError("ANALYSIS_REPAIR_TARGET_ORDER_INVALID")

            def request_for(
                selected_ids: list[str],
                *,
                chunk_index: int,
                chunk_count: int,
            ) -> tuple[dict[str, Any], dict[str, Any]]:
                selected_schema = self._project_analysis_repair_schema(
                    repair_schema,
                    form_id=form_id,
                    case_ids=selected_ids,
                )
                selected_request = {
                    "phase": "ANALYSIS_EXAM_DIRECTED_REPAIR",
                    "semantic_repair_round": round_number,
                    "instruction": (
                        "Replace all and only the listed synthetic analysis "
                        "cases. Return complete claim_map and analysis_summary "
                        "rows for each listed case exactly once and no other "
                        "case. Use source_case as the only source of truth, "
                        "current_rows as the starting point, and correct every "
                        "listed issue_code and failed_output_field. The JSON "
                        "Schema is supplied once by the structured transport."
                    ),
                    "wrong_item_exact_set": {form_id: list(selected_ids)},
                    "repair_targets": {
                        form_id: [
                            deepcopy(dict(target_by_id[case_id]))
                            for case_id in selected_ids
                        ]
                    },
                    "parent_contract_sha256": contract.get(
                        "contract_sha256"
                    ),
                    "successor_lifecycle_sha256": lifecycle_contract.get(
                        "contract_sha256"
                    ),
                    "transport_shard": {
                        "form_id": form_id,
                        "chunk_index": chunk_index,
                        "chunk_count": chunk_count,
                        "case_ids_sha256": _sha256(selected_ids),
                    },
                }
                return selected_request, selected_schema

            chunks: list[list[str]] = []
            current: list[str] = []
            for case_id in case_ids:
                candidate = [*current, case_id]
                candidate_request, candidate_schema = request_for(
                    candidate,
                    chunk_index=99,
                    chunk_count=99,
                )
                metrics = _effective_structured_prompt_metrics(
                    _serialized_request(
                        self._subject_request_projection(
                            target, candidate_request
                        )
                    ),
                    candidate_schema,
                )
                if (
                    current
                    and metrics["effective_prompt_chars"]
                    > Core_PRIMARY_ANALYSIS_PROMPT_CHAR_SURROGATE_LIMIT
                ):
                    chunks.append(current)
                    current = [case_id]
                else:
                    current = candidate
            if current:
                chunks.append(current)
            form_rows = {"claim_map": [], "analysis_summary": []}
            for chunk_index, chunk_ids in enumerate(chunks, start=1):
                request, chunk_schema = request_for(
                    chunk_ids,
                    chunk_index=chunk_index,
                    chunk_count=len(chunks),
                )
                prompt_metrics = _effective_structured_prompt_metrics(
                    _serialized_request(
                        self._subject_request_projection(target, request)
                    ),
                    chunk_schema,
                )
                response = self._stable_provider_call(
                    run_id=run_id,
                    run_root=run_root,
                    state=state,
                    target=target,
                    request=request,
                    response_schema=chunk_schema,
                    structured_chat=structured_chat,
                    call_kind=(
                        f"ANALYSIS_SEMANTIC_REPAIR_ROUND_{round_number}_"
                        f"FORM_{form_id}_CHUNK_{chunk_index}_OF_{len(chunks)}"
                    ),
                )
                response_form = response.get(f"form_{form_id}")
                if not isinstance(response_form, Mapping):
                    raise ValueError("ANALYSIS_REPAIR_SHARD_RESPONSE_INVALID")
                source_cases = [
                    target_by_id[case_id].get("source_case")
                    for case_id in chunk_ids
                ]
                if not all(isinstance(case, Mapping) for case in source_cases):
                    raise ValueError("ANALYSIS_REPAIR_SOURCE_CASE_MISSING")
                normalization = self._normalize_case_local_anchor_aliases(
                    form={"cases": source_cases},
                    response=response_form,
                    artifact="claim_map",
                )
                response_form = normalization["normalized_response"]
                state["analysis_anchor_normalizations"].extend(
                    {
                        "form_id": form_id,
                        "artifact": "claim_map",
                        "transport_stage": (
                            f"SEMANTIC_REPAIR_ROUND_{round_number}"
                        ),
                        "shard_index": chunk_index,
                        **deepcopy(action),
                    }
                    for action in normalization["actions"]
                )

                subset_form = {
                    "form": form_id,
                    "cases": deepcopy(source_cases),
                }

                def audit_candidate(
                    candidate: Mapping[str, Any],
                ) -> dict[str, dict[str, Any]]:
                    try:
                        form_schema = chunk_schema["properties"][
                            f"form_{form_id}"
                        ]
                        artifact_properties = form_schema["properties"]
                    except (KeyError, TypeError) as error:
                        raise ValueError(
                            "ANALYSIS_REPAIR_SHARD_SCHEMA_INVALID"
                        ) from error
                    result: dict[str, dict[str, Any]] = {}
                    for artifact in ("claim_map", "analysis_summary"):
                        artifact_schema = {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [artifact],
                            "properties": {
                                artifact: deepcopy(
                                    artifact_properties[artifact]
                                )
                            },
                        }
                        result[artifact] = exam.audit_analysis_artifact(
                            form=subset_form,
                            response={artifact: candidate.get(artifact)},
                            schema=artifact_schema,
                            artifact=artifact,
                        )
                    return result

                audits = audit_candidate(response_form)
                structural_retry_used = 0
                structural_retry_prompt_metrics = None
                if any(
                    audit.get("status") != "PASS"
                    for audit in audits.values()
                ):
                    residual_projections = {
                        artifact: self._bounded_structural_residual_projection(
                            audit
                        )
                        for artifact, audit in audits.items()
                        if audit.get("status") != "PASS"
                    }
                    residual_id_set = {
                        str(case_id)
                        for projection in residual_projections.values()
                        for case_id in projection["repair_exact_set"]
                    }
                    structural_ids = [
                        case_id
                        for case_id in chunk_ids
                        if case_id in residual_id_set
                    ]
                    if not structural_ids:
                        structural_ids = list(chunk_ids)
                    structural_schema = self._project_analysis_repair_schema(
                        repair_schema,
                        form_id=form_id,
                        case_ids=structural_ids,
                    )
                    structural_source_cases = [
                        target_by_id[case_id]["source_case"]
                        for case_id in structural_ids
                    ]
                    structural_current_response = {
                        artifact: [
                            deepcopy(row)
                            for row in response_form.get(artifact, [])
                            if isinstance(row, Mapping)
                            and row.get("case_id") in structural_ids
                        ]
                        for artifact in ("claim_map", "analysis_summary")
                    }
                    structural_request = {
                        "phase": (
                            "ANALYSIS_SEMANTIC_REPAIR_SHARD_STRUCTURAL_REPAIR"
                        ),
                        "semantic_repair_round": round_number,
                        "instruction": (
                            "Repair all and only the listed residual rows from "
                            "the current directed-repair response. Preserve "
                            "its semantic decisions unless a listed structural "
                            "defect requires change. Return complete claim_map "
                            "and analysis_summary rows exactly once in the "
                            "listed order. Use source_cases only to resolve "
                            "anchor identity or role conflicts."
                        ),
                        "residual_exact_set": {
                            form_id: list(structural_ids)
                        },
                        "residual_projections": residual_projections,
                        "source_cases": {
                            form_id: deepcopy(structural_source_cases)
                        },
                        "current_response": {
                            f"form_{form_id}": structural_current_response
                        },
                        "parent_contract_sha256": contract.get(
                            "contract_sha256"
                        ),
                        "successor_lifecycle_sha256": (
                            lifecycle_contract.get("contract_sha256")
                        ),
                        "semantic_repair_round_increment": 0,
                    }
                    structural_retry_prompt_metrics = (
                        _effective_structured_prompt_metrics(
                            _serialized_request(
                                self._subject_request_projection(
                                    target, structural_request
                                )
                            ),
                            structural_schema,
                        )
                    )
                    structural_response = self._stable_provider_call(
                        run_id=run_id,
                        run_root=run_root,
                        state=state,
                        target=target,
                        request=structural_request,
                        response_schema=structural_schema,
                        structured_chat=structured_chat,
                        call_kind=(
                            f"ANALYSIS_SEMANTIC_REPAIR_ROUND_{round_number}_"
                            f"FORM_{form_id}_CHUNK_{chunk_index}_STRUCTURAL_REPAIR"
                        ),
                    )
                    structural_form = structural_response.get(
                        f"form_{form_id}"
                    )
                    if not isinstance(structural_form, Mapping):
                        raise ValueError(
                            "ANALYSIS_REPAIR_SHARD_STRUCTURAL_RESPONSE_INVALID"
                        )
                    structural_normalization = (
                        self._normalize_case_local_anchor_aliases(
                            form={"cases": structural_source_cases},
                            response=structural_form,
                            artifact="claim_map",
                        )
                    )
                    structural_form = structural_normalization[
                        "normalized_response"
                    ]
                    state["analysis_anchor_normalizations"].extend(
                        {
                            "form_id": form_id,
                            "artifact": "claim_map",
                            "transport_stage": (
                                f"SEMANTIC_REPAIR_ROUND_{round_number}_"
                                "STRUCTURAL_REPAIR"
                            ),
                            "shard_index": chunk_index,
                            **deepcopy(action),
                        }
                        for action in structural_normalization["actions"]
                    )
                    for artifact in ("claim_map", "analysis_summary"):
                        replacement_rows = structural_form.get(artifact)
                        observed_replacement_ids = [
                            row.get("case_id")
                            if isinstance(row, Mapping)
                            else None
                            for row in replacement_rows
                        ] if isinstance(replacement_rows, list) else None
                        if observed_replacement_ids != structural_ids:
                            raise ValueError(
                                "ANALYSIS_REPAIR_SHARD_STRUCTURAL_EXACT_SET_"
                                "MISMATCH"
                            )
                        replacement_by_id = {
                            str(row["case_id"]): deepcopy(row)
                            for row in replacement_rows
                        }
                        response_form[artifact] = [
                            replacement_by_id.get(
                                str(row.get("case_id")), deepcopy(row)
                            )
                            for row in response_form.get(artifact, [])
                            if isinstance(row, Mapping)
                        ]
                    structural_retry_used = 1
                    state[
                        "analysis_semantic_repair_structural_retries"
                    ] += 1
                    audits = audit_candidate(response_form)
                if any(
                    audit.get("status") != "PASS"
                    for audit in audits.values()
                ):
                    raise ValueError(
                        "ANALYSIS_REPAIR_SHARD_CONTRACT_UNCLOSED"
                    )
                for artifact in ("claim_map", "analysis_summary"):
                    rows = response_form.get(artifact)
                    observed = [
                        row.get("case_id")
                        if isinstance(row, Mapping)
                        else None
                        for row in rows
                    ] if isinstance(rows, list) else None
                    if observed != chunk_ids:
                        raise ValueError(
                            "ANALYSIS_REPAIR_SHARD_EXACT_SET_MISMATCH"
                        )
                    form_rows[artifact].extend(deepcopy(rows))
                receipts.append(
                    {
                        "form_id": form_id,
                        "chunk_index": chunk_index,
                        "chunk_count": len(chunks),
                        "case_ids": list(chunk_ids),
                        "case_ids_sha256": _sha256(chunk_ids),
                        "response_sha256": _sha256(response),
                        "normalized_response_form_sha256": _sha256(
                            response_form
                        ),
                        "effective_prompt_metrics": prompt_metrics,
                        "artifact_audits": {
                            artifact: {
                                "status": audit.get("status"),
                                "defect_count": audit.get("defect_count"),
                                "repair_exact_set": audit.get(
                                    "repair_exact_set"
                                ),
                                "audit_sha256": audit.get("audit_sha256"),
                            }
                            for artifact, audit in audits.items()
                        },
                        "structural_retry_used": structural_retry_used,
                        "structural_retry_prompt_metrics": (
                            structural_retry_prompt_metrics
                        ),
                    }
                )
            merged[f"form_{form_id}"] = form_rows
        _write_json_create_only(
            run_root
            / "responses"
            / f"analysis_semantic_repair_round_{round_number}_shards.json",
            {
                "schema_version": "AnalysisRepairTransportShards-v1",
                "semantic_repair_round": round_number,
                "parent_contract_sha256": contract.get("contract_sha256"),
                "sharding_revision": (
                    ANALYSIS_PRIMARY_SEMANTIC_REPAIR_SHARDING_REVISION
                ),
                "provider_received_answer_key": False,
                "shards": receipts,
                "merged_response_sha256": _sha256(merged),
            },
        )
        return merged

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
        run_root = self._scratch_root / "workflow_exams" / run_id
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
            "analysis_anchor_normalizations": [],
            "analysis_semantic_repair_structural_retries": 0,
            "analysis_preloaded_initial_rows": [],
        }
        try:
            if plan.get("cohort_already_complete") is True:
                cohort_basis = self._panel_cohort_basis(
                    str(plan["reference_pack_sha256"])
                )
                completed_records = self._completed_panel_records(
                    target=target,
                    cohort_basis=cohort_basis,
                    cohort_slots=list(plan["panel_cohort_slots"]),
                )
                if len(completed_records) != self.PANELS_REQUIRED_FOR_CACHE:
                    raise ValueError(
                        "ANALYSIS_PANEL_COHORT_REUSE_SOURCE_INCOMPLETE"
                    )
                source_panel = max(
                    completed_records,
                    key=lambda record: int(record["sample_slot"]),
                )
                source_run_id = str(source_panel["panel_id"])
                source_run_root = (
                    self._scratch_root / "workflow_exams" / source_run_id
                )
                source_cohort_path = (
                    source_run_root
                    / "panel_scores"
                    / "cohort_result.json"
                )
                source_result_path = source_run_root / "exam_result.json"
                source_cohort = _load_json(source_cohort_path)
                stored_cohort_hash = source_cohort.get(
                    "cohort_result_sha256"
                )
                unsigned_cohort = {
                    key: value
                    for key, value in source_cohort.items()
                    if key != "cohort_result_sha256"
                }
                if (
                    not isinstance(stored_cohort_hash, str)
                    or stored_cohort_hash != _sha256(unsigned_cohort)
                    or source_cohort.get("stable_score_cache_eligible")
                    is not True
                    or source_cohort.get("comparison_binding_sha256")
                    != plan["comparison_binding_sha256"]
                ):
                    raise ValueError(
                        "ANALYSIS_PANEL_COHORT_REUSE_SOURCE_INVALID"
                    )
                source_public_result = _load_json(source_result_path)
                if (
                    source_public_result.get("stable_score_cache_eligible")
                    is not True
                    or source_public_result.get("cohort_binding_sha256")
                    != source_cohort.get("cohort_binding_sha256")
                    or source_public_result.get(
                        "comparison_binding_sha256"
                    )
                    != plan["comparison_binding_sha256"]
                ):
                    raise ValueError(
                        "ANALYSIS_PANEL_COHORT_REUSE_RESULT_INVALID"
                    )
                copied_cohort_receipt = _write_json_create_only(
                    run_root / "panel_scores" / "cohort_result.json",
                    source_cohort,
                )
                reuse_receipt = {
                    "schema_version": (
                        "DesktopAnalysisPrimaryCohortReuseReceipt-v1"
                    ),
                    "source_run_id": source_run_id,
                    "source_exam_result_sha256": _file_sha256(
                        source_result_path
                    ),
                    "source_cohort_result_sha256": _file_sha256(
                        source_cohort_path
                    ),
                    "cohort_binding_sha256": source_cohort[
                        "cohort_binding_sha256"
                    ],
                    "comparison_binding_sha256": plan[
                        "comparison_binding_sha256"
                    ],
                    "external_model_calls": 0,
                    "provider_received_answer_key": False,
                }
                reuse_receipt["reuse_receipt_sha256"] = _sha256(
                    reuse_receipt
                )
                reuse_receipt_file = _write_json_create_only(
                    run_root / "score_cache_reuse_receipt.json",
                    reuse_receipt,
                )
                public_result = deepcopy(source_public_result)
                public_result.update(
                    {
                        "schema_version": (
                            "WorkflowModelExamRunResult-v1"
                        ),
                        "status": source_cohort["status"],
                        "reason": (
                            "ANALYSIS_PRIMARY_THREE_PANEL_COHORT_REUSED"
                        ),
                        "run_id": run_id,
                        "plan_sha256": plan["plan_sha256"],
                        "request_attempts": 0,
                        "provider_calls": 0,
                        "external_model_calls": 0,
                        "external_network_calls": 0,
                        "external_process_launches": 0,
                        "token_usage": {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                        },
                        "estimated_cost_cny": (
                            0.0
                            if plan["profile_kind"] in {"API", "LOCAL"}
                            else None
                        ),
                        "actual_cost": None,
                        "cost_semantics": (
                            "CACHE_REUSE_NO_EXTERNAL_CALL"
                        ),
                        "score_cache_reused": True,
                        "reused_from_run_id": source_run_id,
                        "cohort_result": (
                            "panel_scores/cohort_result.json"
                        ),
                        "cohort_result_file_sha256": (
                            copied_cohort_receipt["sha256"]
                        ),
                        "score_cache_reuse_receipt": (
                            "score_cache_reuse_receipt.json"
                        ),
                        "score_cache_reuse_receipt_sha256": (
                            reuse_receipt_file["sha256"]
                        ),
                        "panel_record": None,
                        "panel_record_sha256": None,
                        "current_panel_score_diagnostic": None,
                        "current_panel_form_scores": None,
                        "current_panel_first_pass_role_ability_score": None,
                        "semantic_repair_rounds": 0,
                        "semantic_repair_lifecycle": [],
                        "semantic_repair_acceptance": [],
                        "anchor_normalization_count": 0,
                        "anchor_normalization_receipt": None,
                        "anchor_normalization_receipt_sha256": None,
                    }
                )
                evidence = _write_json_create_only(
                    run_root / "exam_result.json", public_result
                )
                return {
                    **public_result,
                    "schema_version": RESULT_SCHEMA,
                    "kind": "WORKFLOW_NODE",
                    "duration_ms": max(
                        0, int((monotonic() - started) * 1000)
                    ),
                    "exam_mode": plan["mode"],
                    "exam_run_id": run_id,
                    "exam_run_root": str(run_root.resolve()),
                    "exam_evidence_sha256": evidence["sha256"],
                }
            modules = self._quality()
            protocol = modules["protocol"]
            exam = modules["exam"]
            pack = modules["reference"].load_analysis_reference_exam_pack(
                self._reference_pack_root
            )
            source_schema = _load_json(
                pack.member("schemas/analysis_primary_output.schema.json")
            )
            inputs = self._exam_inputs(
                pack=pack,
                source_schema=source_schema,
                context={
                    **dict(context),
                    "workflow_exam_sample_slot": plan["sample_slot"],
                },
                protocol=protocol,
            )
            full_schema = inputs["schema"]
            forms = inputs["forms"]
            golds = inputs["golds"]
            if inputs["sample_metadata"]["manifest_sha256"] != plan.get(
                "sample_manifest_sha256"
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_ANALYSIS_SAMPLE_MANIFEST_MISMATCH"
                )
            _write_json_create_only(
                run_root / "sample_manifest.json",
                inputs["sample_metadata"],
            )
            responses: dict[str, dict[str, Any]] = {}
            raw_scores: dict[str, dict[str, Any]] = {}
            for form_id in ("A", "B"):
                merged: dict[str, list[Any]] = {
                    "claim_map": [],
                    "analysis_summary": [],
                }
                for artifact in ("claim_map", "analysis_summary"):
                    shards = self._shards(
                        forms[form_id],
                        form_id=form_id,
                        artifact=artifact,
                        profile_kind=str(target["kind"]),
                    )
                    for shard_index, shard in enumerate(shards, start=1):
                        preload = self._preloaded_initial_artifact_rows(
                            context=context,
                            target=target,
                            sample_slot=int(plan["sample_slot"]),
                            form_id=form_id,
                            artifact=artifact,
                            target_case_ids=list(shard["target_ids"]),
                        )
                        case_by_id = {
                            str(row["case_id"]): deepcopy(dict(row))
                            for row in shard["target_form"]["cases"]
                        }

                        def subset_form(case_ids: list[str]) -> dict[str, Any]:
                            projected = deepcopy(dict(shard["target_form"]))
                            projected["cases"] = [
                                deepcopy(case_by_id[case_id])
                                for case_id in case_ids
                            ]
                            return projected

                        preloaded_rows = deepcopy(preload["rows"])
                        if preloaded_rows:
                            preloaded_form = subset_form(
                                list(preload["reused_case_ids"])
                            )
                            preloaded_schema = protocol.shard_artifact_schema(
                                full_schema,
                                artifact,
                                target_count=len(preloaded_rows),
                            )
                            preloaded_response = {artifact: preloaded_rows}
                            normalization = (
                                self._normalize_case_local_anchor_aliases(
                                    form=preloaded_form,
                                    response=preloaded_response,
                                    artifact=artifact,
                                )
                            )
                            preloaded_response = normalization[
                                "normalized_response"
                            ]
                            state["analysis_anchor_normalizations"].extend(
                                {
                                    "form_id": form_id,
                                    "artifact": artifact,
                                    "transport_stage": "PRELOADED_INITIAL",
                                    "shard_index": shard_index,
                                    **deepcopy(action),
                                }
                                for action in normalization["actions"]
                            )
                            preload_audit = exam.audit_analysis_artifact(
                                form=preloaded_form,
                                response=preloaded_response,
                                schema=preloaded_schema,
                                artifact=artifact,
                            )
                            if preload_audit.get("status") != "PASS":
                                raise ValueError(
                                    "ANALYSIS_PRELOAD_ARTIFACT_INVALID"
                                )
                            preloaded_rows = deepcopy(
                                preloaded_response[artifact]
                            )

                        live_rows: list[Any] = []
                        missing_case_ids = list(preload["missing_case_ids"])
                        if missing_case_ids:
                            missing_form = subset_form(missing_case_ids)
                            schema = protocol.shard_artifact_schema(
                                full_schema,
                                artifact,
                                target_count=len(missing_case_ids),
                            )
                            prompt = protocol.build_artifact_prompt(
                                missing_form,
                                artifact,
                                target_case_ids=missing_case_ids,
                            )
                            request = {
                                "phase": "ANALYSIS_REFERENCE_EXAM",
                                "artifact": artifact,
                                "target_case_ids": missing_case_ids,
                                "subject_prompt": prompt,
                            }
                            response = self._stable_provider_call(
                                run_id=run_id,
                                run_root=run_root,
                                state=state,
                                target=target,
                                request=request,
                                response_schema=schema,
                                structured_chat=structured_chat,
                                call_kind=(
                                    f"FORM_{form_id}_{artifact.upper()}_SHARD_{shard_index:02d}"
                                ),
                            )
                            normalization = self._normalize_case_local_anchor_aliases(
                                form=missing_form,
                                response=response,
                                artifact=artifact,
                            )
                            response = normalization["normalized_response"]
                            state["analysis_anchor_normalizations"].extend(
                                {
                                    "form_id": form_id,
                                    "artifact": artifact,
                                    "transport_stage": "INITIAL",
                                    "shard_index": shard_index,
                                    **deepcopy(action),
                                }
                                for action in normalization["actions"]
                            )
                            audit = exam.audit_analysis_artifact(
                                form=missing_form,
                                response=response,
                                schema=schema,
                                artifact=artifact,
                            )
                            if audit.get("status") != "PASS":
                                residual_projection = (
                                    self._bounded_structural_residual_projection(
                                        audit
                                    )
                                )
                                repair_request = {
                                    "phase": "ANALYSIS_STRUCTURAL_REPAIR",
                                    "instruction": (
                                        "Repair only the listed structural residuals in "
                                        "the current response. Use each defect_locator, "
                                        "including any named anchor, as an exact locator. "
                                        "Return the complete artifact rows for all and only "
                                        "target_case_ids in their declared order."
                                    ),
                                    "artifact": artifact,
                                    "target_case_ids": missing_case_ids,
                                    "residual_projection": residual_projection,
                                    "current_response": response,
                                    "subject_prompt": prompt,
                                }
                                response = self._stable_provider_call(
                                    run_id=run_id,
                                    run_root=run_root,
                                    state=state,
                                    target=target,
                                    request=repair_request,
                                    response_schema=schema,
                                    structured_chat=structured_chat,
                                    call_kind=(
                                        f"FORM_{form_id}_{artifact.upper()}_SHARD_{shard_index:02d}_REPAIR"
                                    ),
                                )
                                normalization = (
                                    self._normalize_case_local_anchor_aliases(
                                        form=missing_form,
                                        response=response,
                                        artifact=artifact,
                                    )
                                )
                                response = normalization[
                                    "normalized_response"
                                ]
                                state["analysis_anchor_normalizations"].extend(
                                    {
                                        "form_id": form_id,
                                        "artifact": artifact,
                                        "transport_stage": "STRUCTURAL_REPAIR",
                                        "shard_index": shard_index,
                                        **deepcopy(action),
                                    }
                                    for action in normalization["actions"]
                                )
                                audit = exam.audit_analysis_artifact(
                                    form=missing_form,
                                    response=response,
                                    schema=schema,
                                    artifact=artifact,
                                )
                            if audit.get("status") != "PASS":
                                raise ValueError(
                                    "WORKFLOW_MODEL_EXAM_ANALYSIS_SHARD_CONTRACT_UNCLOSED"
                                )
                            rows = response.get(artifact)
                            if not isinstance(rows, list):
                                raise ValueError(
                                    "WORKFLOW_MODEL_EXAM_ANALYSIS_SHARD_INVALID"
                                )
                            live_rows = deepcopy(rows)

                        combined_rows = preloaded_rows + live_rows
                        combined_by_id: dict[str, Any] = {}
                        for row in combined_rows:
                            if (
                                not isinstance(row, Mapping)
                                or not isinstance(row.get("case_id"), str)
                                or row["case_id"] in combined_by_id
                            ):
                                raise ValueError(
                                    "WORKFLOW_MODEL_EXAM_ANALYSIS_SHARD_INVALID"
                                )
                            combined_by_id[str(row["case_id"])] = deepcopy(row)
                        if set(combined_by_id) != set(shard["target_ids"]):
                            raise ValueError(
                                "WORKFLOW_MODEL_EXAM_ANALYSIS_SHARD_EXACT_SET_INVALID"
                            )
                        ordered_rows = [
                            combined_by_id[case_id]
                            for case_id in shard["target_ids"]
                        ]
                        combined_schema = protocol.shard_artifact_schema(
                            full_schema,
                            artifact,
                            target_count=len(shard["target_ids"]),
                        )
                        combined_audit = exam.audit_analysis_artifact(
                            form=shard["target_form"],
                            response={artifact: ordered_rows},
                            schema=combined_schema,
                            artifact=artifact,
                        )
                        if combined_audit.get("status") != "PASS":
                            raise ValueError(
                                "WORKFLOW_MODEL_EXAM_ANALYSIS_GLOBAL_AUDIT_FAILED"
                            )
                        if preloaded_rows:
                            state["analysis_preloaded_initial_rows"].append(
                                {
                                    "form_id": form_id,
                                    "artifact": artifact,
                                    "shard_index": shard_index,
                                    "reused_case_ids": list(
                                        preload["reused_case_ids"]
                                    ),
                                    "missing_case_ids": missing_case_ids,
                                    "source_calls": deepcopy(
                                        preload["source_calls"]
                                    ),
                                    "provider_calls_for_reused_rows": 0,
                                    "rows_sha256": _sha256(preloaded_rows),
                                }
                            )
                        merged[artifact].extend(deepcopy(ordered_rows))
                response = {
                    "claim_map": merged["claim_map"],
                    "analysis_summary": merged["analysis_summary"],
                }
                for artifact in ("claim_map", "analysis_summary"):
                    audit = exam.audit_analysis_artifact(
                        form=forms[form_id],
                        response={artifact: response[artifact]},
                        schema=protocol.artifact_schema(full_schema, artifact),
                        artifact=artifact,
                    )
                    if audit.get("status") != "PASS":
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_ANALYSIS_GLOBAL_AUDIT_FAILED"
                        )
                raw = protocol.score_response(
                    response,
                    golds[form_id],
                    full_schema,
                )
                responses[form_id] = deepcopy(response)
                raw_scores[form_id] = deepcopy(raw)

            initial_preload_receipt = None
            if state["analysis_preloaded_initial_rows"]:
                preload_payload = {
                    "schema_version": (
                        "DesktopAnalysisPrimaryExactProgressReuseReceipt-v1"
                    ),
                    "sample_slot": plan["sample_slot"],
                    "requested_model": requested_model,
                    "returned_model": requested_model,
                    "reuse_actions": deepcopy(
                        state["analysis_preloaded_initial_rows"]
                    ),
                    "reused_case_row_count": sum(
                        len(action["reused_case_ids"])
                        for action in state[
                            "analysis_preloaded_initial_rows"
                        ]
                    ),
                    "provider_calls_for_reused_rows": 0,
                    "provider_received_answer_key": False,
                    "length_only_repair_outputs_reused": False,
                    "completed_initial_answers_retested": False,
                }
                preload_payload["reuse_receipt_sha256"] = _sha256(
                    preload_payload
                )
                initial_preload_receipt = _write_json_create_only(
                    run_root / "initial_exact_progress_reuse_receipt.json",
                    preload_payload,
                )

            first_pass_form_scores = {
                form_id: float(
                    exam.score_analysis_form_v2(
                        raw_scoring=raw_scores[form_id]
                    )["score"]
                )
                for form_id in ("A", "B")
            }
            first_pass_role_ability_score = round(
                sum(first_pass_form_scores.values()) / 2.0, 2
            )
            for form_id in ("A", "B"):
                _write_json_create_only(
                    run_root
                    / "panel_scores"
                    / f"form_{form_id}_initial_raw_projection.json",
                    self._analysis_cohort_raw_projection(raw_scores[form_id]),
                )
            current_responses = deepcopy(responses)
            current_scores = deepcopy(raw_scores)
            initial_contract: Mapping[str, Any] | None = None
            first_round_exact_sets: dict[str, list[str]] | None = None
            repair_lifecycle: list[dict[str, Any]] = []
            repair_burdens: list[dict[str, Any]] = []
            repair_acceptance_receipts: list[dict[str, Any]] = []
            unchanged_surface_sha256 = _sha256(
                {
                    "forms": forms,
                    "schema": full_schema,
                    "category_id": self.EXAM_CATEGORY_ID,
                    "provider_receives_answer_key": False,
                }
            )
            for round_number in range(
                1, self.SEMANTIC_REPAIR_ROUND_LIMIT + 1
            ):
                wrong_sets = self._analysis_wrong_sets(current_scores)
                if not any(wrong_sets.values()):
                    break
                source_scores = deepcopy(current_scores)
                for form_id in ("A", "B"):
                    target_ids = set(wrong_sets[form_id])
                    source_scores[form_id]["details"] = [
                        deepcopy(detail)
                        for detail in source_scores[form_id].get(
                            "details", []
                        )
                        if isinstance(detail, Mapping)
                        and detail.get("case_id") in target_ids
                    ]
                if round_number == 2:
                    assert first_round_exact_sets is not None
                    for form_id in ("A", "B"):
                        allowed = set(first_round_exact_sets[form_id])
                        source_scores[form_id]["details"] = [
                            deepcopy(detail)
                            for detail in source_scores[form_id].get(
                                "details", []
                            )
                            if isinstance(detail, Mapping)
                            and detail.get("case_id") in allowed
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
                    schema=full_schema,
                )
                exact_sets = {
                    form_id: list(case_ids)
                    for form_id, case_ids in package["contract"][
                        "wrong_item_exact_set"
                    ].items()
                }
                if first_round_exact_sets is None:
                    first_round_exact_sets = deepcopy(exact_sets)
                    initial_contract = deepcopy(package["contract"])
                lifecycle = build_repair_round(
                    round_number=round_number,
                    category_id=self.EXAM_CATEGORY_ID,
                    targets_by_form=exact_sets,
                    prior_targets_by_form=(
                        first_round_exact_sets
                        if round_number == 2
                        else None
                    ),
                    prompt_projection=json.loads(package["prompt"]),
                    unchanged_surface_sha256=unchanged_surface_sha256,
                )
                repair_burdens.append(
                    self._analysis_category_burden(
                        exam=exam,
                        forms=forms,
                        scores=source_scores,
                        exact_sets=exact_sets,
                    )
                )
                repair_response = self._run_analysis_semantic_repair(
                    run_id=run_id,
                    run_root=run_root,
                    state=state,
                    target=target,
                    package=package,
                    lifecycle_contract=lifecycle,
                    round_number=round_number,
                    structured_chat=structured_chat,
                )
                merged_repair = exam.merge_exam_directed_repair(
                    forms=forms,
                    original_responses=current_responses,
                    repair_response=repair_response,
                    schema=full_schema,
                    repair_contract=package["contract"],
                )
                candidate_responses = deepcopy(merged_repair["responses"])
                candidate_scores = {
                    form_id: protocol.score_response(
                        candidate_responses[form_id],
                        golds[form_id],
                        full_schema,
                    )
                    for form_id in ("A", "B")
                }
                acceptance = analysis_repair_acceptance(
                    before_scores=current_scores,
                    candidate_scores=candidate_scores,
                    targets_by_form=exact_sets,
                )
                current_responses = self._analysis_case_local_acceptance_merge(
                    original_responses=current_responses,
                    candidate_responses=candidate_responses,
                    accepted_by_form=acceptance["accepted_by_form"],
                )
                current_scores = {
                    form_id: protocol.score_response(
                        current_responses[form_id],
                        golds[form_id],
                        full_schema,
                    )
                    for form_id in ("A", "B")
                }
                repair_acceptance_receipts.append(acceptance)
                repair_lifecycle.append(lifecycle)

            residual_materiality = analysis_material_repair_targets(
                current_scores
            )
            residual_sets = deepcopy(
                residual_materiality["targets_by_form"]
            )
            calibration_sets = deepcopy(
                residual_materiality["calibration_only_by_form"]
            )
            residual_burden = self._analysis_category_burden(
                exam=exam,
                forms=forms,
                scores=current_scores,
                exact_sets=residual_sets,
            )
            penalty_round_rows: list[dict[str, Any]] = []
            for round_index, burden in enumerate(repair_burdens, start=1):
                penalty_round_rows.append(
                    {
                        "round_number": round_index,
                        "category_id": self.EXAM_CATEGORY_ID,
                        "nonlinear_category_penalty": burden["total_mean"],
                        "fixed_penalty": 0.0,
                        "volume_penalty": 0.0,
                    }
                )
            cumulative_penalty = (
                cumulative_category_repair_penalty(penalty_round_rows)
                if penalty_round_rows
                else None
            )
            form_results: dict[str, dict[str, Any]] = {}
            form_scores: dict[str, float] = {}
            for form_id in ("A", "B"):
                visible_output_chars = len(
                    _serialized_request(current_responses[form_id])
                )
                piecewise = exam.score_analysis_form_v2(
                    raw_scoring=current_scores[form_id]
                )
                base_score = float(piecewise["score"])
                form_score = round(max(0.0, base_score), 2)
                form_scores[form_id] = form_score
                form_results[form_id] = {
                    "status": "SCORED",
                    "score": form_score,
                    "score_before_successor_repair_burden": base_score,
                    "repair_burden_applied_to_quality_score": False,
                    "quality_hard_gate": current_scores[form_id][
                        "quality_hard_gate"
                    ],
                    "critical_count": current_scores[form_id][
                        "critical_count"
                    ],
                    "repair_round_burdens": [
                        deepcopy(burden["by_form"][form_id])
                        for burden in repair_burdens
                    ],
                    "residual_burden": deepcopy(
                        residual_burden["by_form"][form_id]
                    ),
                    "calibration_only_case_ids": calibration_sets[form_id],
                    "visible_output_chars": visible_output_chars,
                    "visible_output_reference_ceiling_chars": (
                        Core_PRIMARY_ANALYSIS_OUTPUT_CHAR_LIMIT
                    ),
                    "visible_output_exceeds_reference": (
                        visible_output_chars
                        > Core_PRIMARY_ANALYSIS_OUTPUT_CHAR_LIMIT
                    ),
                    "visible_output_reference_semantics": "DIAGNOSTIC_ONLY",
                }
                _write_json_create_only(
                    run_root
                    / "panel_scores"
                    / f"form_{form_id}_final_raw_projection.json",
                    self._analysis_cohort_raw_projection(
                        current_scores[form_id]
                    ),
                )
                _write_json_create_only(
                    run_root / "responses" / f"form_{form_id}_final.json",
                    current_responses[form_id],
                )
            failed_item_count = sum(
                len(case_ids) for case_ids in residual_sets.values()
            )
            calibration_residual_count = sum(
                len(case_ids) for case_ids in calibration_sets.values()
            )
            publication = exam.publish_analysis_exam_result(
                provider=str(
                    target.get("provider")
                    or target.get("adapter_id")
                    or target.get("endpoint_kind")
                ),
                model=str(target["model_name"]),
                parameters={
                    "profile_kind": target["kind"],
                    "model_digest": target.get("model_digest"),
                    "base_model_calls": plan["base_model_calls"],
                    "maximum_model_calls": plan["maximum_model_calls"],
                },
                forms=form_results,
                evidence_refs=[
                    "responses/form_A_final.json",
                    "responses/form_B_final.json",
                ],
            )
            score = int(round(float(publication["total_score_out_of_100"])))
            panel_quality_candidate_verdict = (
                "PASS"
                if score >= 80
                and failed_item_count == 0
                and all(
                    current_scores[form_id]["quality_hard_gate"] == "PASS"
                    for form_id in ("A", "B")
                )
                else "FAIL"
            )
            blocking_failures: list[dict[str, Any]] = []
            for form_id in ("A", "B"):
                residual_ids = set(residual_sets[form_id])
                for detail in current_scores[form_id].get("details", []):
                    if (
                        not isinstance(detail, Mapping)
                        or detail.get("case_id") not in residual_ids
                    ):
                        continue
                    failed_fields = sorted(
                        {
                            str(reason)
                            for reason in (detail.get("critical_reasons") or [])
                            if isinstance(reason, str) and reason
                        }
                    )
                    if not failed_fields:
                        failed_fields = ["CASE_SCORE_BELOW_80_MATERIAL"]
                    blocking_failures.append(
                        {
                            "case_id": str(detail["case_id"]),
                            "failed_fields": failed_fields,
                            "evidence_anchor": (
                                f"scores/form_{form_id}.json#"
                                f"case_details/{detail['case_id']}"
                            ),
                            "blocking": True,
                            "model_fail_materiality": "MATERIAL",
                        }
                    )
            material_residual_case_ids = sorted(
                str(row["case_id"]) for row in blocking_failures
            )
            calibration_residual_case_ids = sorted(
                case_id
                for case_ids in calibration_sets.values()
                for case_id in case_ids
            )
            model_fail_projection = {
                "model_fail_policy_revision": (
                    PRIMARY_MODEL_FAIL_POLICY_REVISION
                ),
                "model_fail_verdict": "NOT_ASSESSED",
                "model_fail_established": False,
                "model_fail_minimum_distinct_material_cases": None,
                "material_residual_case_ids": material_residual_case_ids,
                "calibration_residual_case_ids": (
                    calibration_residual_case_ids
                ),
                "operational_eligibility_verdict": (
                    "PENDING_THREE_PANEL_COHORT"
                ),
                "category_id": self.EXAM_CATEGORY_ID,
                "category_local_score": round(float(score), 2),
                "numeric_score_is_not_model_fail_verdict": True,
                "single_panel_cannot_establish_model_fail": True,
                "cross_category_comparison_forbidden": True,
                "global_ranking_forbidden": True,
            }
            raw_quality_fail_admission = None
            quality_fail_admission = None
            verdict = "NOT_ASSESSED"
            cap = plan.get("budget_cap_cny")
            if cap is not None and state["estimated_cost_cny"] > float(cap):
                raise ValueError("WORKFLOW_MODEL_EXAM_COST_CAP_EXCEEDED")
            normalization_receipt = _write_json_create_only(
                run_root
                / "normalizations"
                / "analysis_case_local_anchor_aliases.json",
                {
                    "schema_version": (
                        ANALYSIS_PRIMARY_ANCHOR_NORMALIZATION_REVISION
                    ),
                    "normalization_count": len(
                        state["analysis_anchor_normalizations"]
                    ),
                    "actions": deepcopy(
                        state["analysis_anchor_normalizations"]
                    ),
                    "gold_consulted": False,
                    "provider_call_required": False,
                    "semantic_repair_round_increment": 0,
                },
            )
            panel_record = {
                "schema_version": "DesktopAnalysisPrimaryPanelRecord-v1",
                "panel_id": run_id,
                "sample_slot": plan["sample_slot"],
                "panel_binding_sha256": plan["panel_binding_sha256"],
                "cohort_basis": {
                    "category_id": self.EXAM_CATEGORY_ID,
                    "reference_pack_sha256": plan[
                        "reference_pack_sha256"
                    ],
                    "sample_revision": plan["sample_revision"],
                    "subject_projection_revision": plan[
                        "subject_projection_revision"
                    ],
                    "executor_ref": self.EXECUTOR_REF,
                },
                "model_identity": self._panel_model_identity(target),
                "selection": deepcopy(
                    inputs["sample_metadata"]["selection"]
                ),
                "initial_raw_scores": {
                    form_id: self._analysis_cohort_raw_projection(
                        raw_scores[form_id]
                    )
                    for form_id in ("A", "B")
                },
                "raw_scores": {
                    form_id: self._analysis_cohort_raw_projection(
                        current_scores[form_id]
                    )
                    for form_id in ("A", "B")
                },
                "panel_quality_candidate_verdict": (
                    panel_quality_candidate_verdict
                ),
                "panel_score_diagnostic": score,
                "first_pass_panel_score_diagnostic": (
                    first_pass_role_ability_score
                ),
                "material_residual_case_ids_by_form": deepcopy(
                    residual_sets
                ),
                "calibration_residual_case_ids_by_form": deepcopy(
                    calibration_sets
                ),
                "semantic_repair_rounds": len(repair_lifecycle),
                "semantic_repair_acceptance": deepcopy(
                    repair_acceptance_receipts
                ),
                "panel_evidence_only": True,
                "single_panel_cache_eligible": False,
                "provider_received_answer_key": False,
                "initial_exact_progress_reuse": bool(
                    state["analysis_preloaded_initial_rows"]
                ),
                "initial_reused_case_row_count": sum(
                    len(action["reused_case_ids"])
                    for action in state[
                        "analysis_preloaded_initial_rows"
                    ]
                ),
            }
            panel_record["panel_record_sha256"] = _sha256(panel_record)
            panel_record_receipt = _write_json_create_only(
                run_root / "panel_scores" / "panel_record.json",
                panel_record,
            )
            panel_score_diagnostic = score
            panel_form_scores = deepcopy(form_scores)
            panel_first_pass_role_ability_score = (
                first_pass_role_ability_score
            )
            result_reason = (
                "ANALYSIS_PRIMARY_PANEL_COMPLETED_COHORT_PENDING"
            )
            panel_evidence_only = True
            stable_score_cache_eligible = False
            horizontal_comparison_eligible = False
            horizontal_comparison_pending = True
            cohort_panel_count = 1
            cohort_sample_slots = [int(plan["sample_slot"])]
            cohort_binding_sha256: str | None = None
            cohort_result_receipt: dict[str, Any] | None = None
            cohort_difficulty_coverage: dict[str, Any] | None = None
            cohort_panel_score_range: float | None = None
            completed_panel_records = self._completed_panel_records(
                target=target,
                cohort_basis=self._panel_cohort_basis(
                    str(plan["reference_pack_sha256"])
                ),
                cohort_slots=list(plan["panel_cohort_slots"]),
            )
            cohort_panel_count = len(completed_panel_records)
            cohort_sample_slots = sorted(
                int(record["sample_slot"])
                for record in completed_panel_records
            )
            if len(completed_panel_records) == self.PANELS_REQUIRED_FOR_CACHE:
                final_cohort = build_analysis_panel_cohort(
                    completed_panel_records
                )
                initial_panel_records: list[dict[str, Any]] = []
                for record in completed_panel_records:
                    initial_scores = record.get("initial_raw_scores")
                    if not isinstance(initial_scores, Mapping):
                        raise ValueError(
                            "ANALYSIS_PANEL_INITIAL_RAW_SCORES_REQUIRED"
                        )
                    initial_record = deepcopy(record)
                    initial_record["raw_scores"] = deepcopy(
                        dict(initial_scores)
                    )
                    initial_panel_records.append(initial_record)
                initial_cohort = build_analysis_panel_cohort(
                    initial_panel_records
                )
                aggregate_raw_scores = final_cohort[
                    "aggregate_raw_scores"
                ]
                aggregate_initial_raw_scores = initial_cohort[
                    "aggregate_raw_scores"
                ]
                form_scores = {
                    form_id: round(
                        float(
                            exam.score_analysis_form_v2(
                                raw_scoring=aggregate_raw_scores[form_id]
                            )["score"]
                        ),
                        2,
                    )
                    for form_id in ("A", "B")
                }
                first_pass_form_scores = {
                    form_id: round(
                        float(
                            exam.score_analysis_form_v2(
                                raw_scoring=aggregate_initial_raw_scores[
                                    form_id
                                ]
                            )["score"]
                        ),
                        2,
                    )
                    for form_id in ("A", "B")
                }
                score = int(
                    round(sum(form_scores.values()) / len(form_scores))
                )
                first_pass_role_ability_score = round(
                    sum(first_pass_form_scores.values())
                    / len(first_pass_form_scores),
                    2,
                )
                cohort_materiality = analysis_material_repair_targets(
                    aggregate_raw_scores
                )
                residual_sets = deepcopy(
                    cohort_materiality["targets_by_form"]
                )
                calibration_sets = deepcopy(
                    cohort_materiality["calibration_only_by_form"]
                )
                material_rows: list[dict[str, Any]] = []
                for form_id in ("A", "B"):
                    detail_by_id = {
                        str(detail.get("case_id")): detail
                        for detail in aggregate_raw_scores[form_id].get(
                            "details", []
                        )
                        if isinstance(detail, Mapping)
                    }
                    owner_by_id: dict[str, str] = {}
                    for record in completed_panel_records:
                        selected = record.get("selection", {}).get(
                            form_id, []
                        )
                        if not isinstance(selected, list):
                            raise ValueError(
                                "ANALYSIS_PANEL_SELECTION_INVALID"
                            )
                        for item in selected:
                            if isinstance(item, Mapping) and isinstance(
                                item.get("case_id"), str
                            ):
                                owner_by_id[str(item["case_id"])] = str(
                                    record["panel_id"]
                                )
                    for case_id in residual_sets[form_id]:
                        detail = detail_by_id.get(case_id)
                        panel_id = owner_by_id.get(case_id)
                        if detail is None or panel_id is None:
                            raise ValueError(
                                "ANALYSIS_COHORT_RESIDUAL_OWNER_MISSING"
                            )
                        family = str(detail.get("family") or "")
                        if not family:
                            raise ValueError(
                                "ANALYSIS_COHORT_RESIDUAL_FAMILY_MISSING"
                            )
                        failed_fields = sorted(
                            {
                                str(reason)
                                for reason in (
                                    detail.get("critical_reasons") or []
                                )
                                if isinstance(reason, str) and reason
                            }
                        )
                        if not failed_fields:
                            failed_fields = [
                                "CASE_SCORE_BELOW_80_MATERIAL"
                            ]
                        material_rows.append(
                            {
                                "form_id": form_id,
                                "case_id": case_id,
                                "panel_id": panel_id,
                                "family": family,
                                "failed_fields": failed_fields,
                                "evidence_anchor": (
                                    "panel_scores/cohort_result.json#"
                                    f"aggregate_raw_scores/{form_id}/"
                                    f"details/{case_id}"
                                ),
                                "blocking": True,
                                "model_fail_materiality": "MATERIAL",
                            }
                        )
                failed_item_count = len(material_rows)
                calibration_residual_count = sum(
                    len(case_ids)
                    for case_ids in calibration_sets.values()
                )
                cohort_fail_decision = (
                    analysis_cohort_model_fail_decision(
                        score=float(score),
                        material_residuals=material_rows,
                        quality_hard_gate_pass=all(
                            aggregate_raw_scores[form_id][
                                "quality_hard_gate"
                            ]
                            == "PASS"
                            for form_id in ("A", "B")
                        ),
                    )
                )
                strict_model_fail = bool(
                    cohort_fail_decision["model_fail_established"]
                )
                cohort_quality_pass = (
                    cohort_fail_decision["status"] == "PASS"
                )
                verdict = str(cohort_fail_decision["status"])
                if verdict in {"PASS", "FAIL"}:
                    result_reason = (
                        "ANALYSIS_PRIMARY_THREE_PANEL_COHORT_COMPLETED"
                    )
                else:
                    verdict = "NOT_ASSESSED"
                    result_reason = (
                        "ANALYSIS_PRIMARY_THREE_PANEL_COHORT_INCONCLUSIVE"
                    )
                panel_quality_candidate_verdict = (
                    "PASS" if cohort_quality_pass else "FAIL"
                )
                blocking_failures = deepcopy(material_rows)
                material_residual_case_ids = sorted(
                    f"{row['form_id']}:{row['case_id']}"
                    for row in material_rows
                )
                calibration_residual_case_ids = sorted(
                    f"{form_id}:{case_id}"
                    for form_id, case_ids in calibration_sets.items()
                    for case_id in case_ids
                )
                model_fail_projection = {
                    "model_fail_policy_revision": (
                        PRIMARY_MODEL_FAIL_POLICY_REVISION
                    ),
                    "cohort_fail_policy_revision": (
                        cohort_fail_decision["schema_version"]
                    ),
                    "model_fail_verdict": cohort_fail_decision[
                        "model_fail_verdict"
                    ],
                    "model_fail_established": strict_model_fail,
                    "model_fail_minimum_distinct_material_cases": (
                        cohort_fail_decision[
                            "minimum_distinct_material_cases"
                        ]
                    ),
                    "model_fail_minimum_distinct_panels": (
                        cohort_fail_decision["minimum_distinct_panels"]
                    ),
                    "model_fail_minimum_distinct_families": (
                        cohort_fail_decision["minimum_distinct_families"]
                    ),
                    "model_fail_distinct_material_case_count": (
                        cohort_fail_decision[
                            "distinct_material_case_count"
                        ]
                    ),
                    "model_fail_distinct_panel_count": (
                        cohort_fail_decision[
                            "distinct_material_panel_count"
                        ]
                    ),
                    "model_fail_distinct_family_count": (
                        cohort_fail_decision[
                            "distinct_material_family_count"
                        ]
                    ),
                    "material_residual_case_ids": (
                        material_residual_case_ids
                    ),
                    "calibration_residual_case_ids": (
                        calibration_residual_case_ids
                    ),
                    "operational_eligibility_verdict": (
                        cohort_fail_decision[
                            "operational_eligibility_verdict"
                        ]
                    ),
                    "category_id": self.EXAM_CATEGORY_ID,
                    "category_local_score": round(float(score), 2),
                    "numeric_score_is_not_model_fail_verdict": True,
                    "single_panel_cannot_establish_model_fail": True,
                    "cross_category_comparison_forbidden": True,
                    "global_ranking_forbidden": True,
                }
                quality_fail_admission = None
                raw_quality_fail_admission = None
                if strict_model_fail:
                    quality_fail_admission = admit_quality_fail(
                        execution_outcome=(
                            ExecutionOutcome.COMPLETED.value
                        ),
                        scorability=Scorability.SCOREABLE.value,
                        repair_rounds_used=max(
                            int(record.get("semantic_repair_rounds", 0))
                            for record in completed_panel_records
                        ),
                        repair_round_limit=(
                            self.SEMANTIC_REPAIR_ROUND_LIMIT
                        ),
                        blocking_failures=blocking_failures,
                        exact_hashes={
                            "reference_pack_sha256": str(
                                plan["reference_pack_sha256"]
                            ),
                            "scoring_protocol_sha256": _file_sha256(
                                Path(str(exam.__file__))
                            ),
                            "executor_sha256": _file_sha256(
                                Path(__file__)
                            ),
                            "input_exact_set_sha256": str(
                                final_cohort["cohort_binding_sha256"]
                            ),
                        },
                        repair_cycle_closed=True,
                    )
                    raw_quality_fail_admission = deepcopy(
                        quality_fail_admission
                    )
                panel_evidence_only = False
                stable_score_cache_eligible = True
                horizontal_comparison_eligible = True
                horizontal_comparison_pending = False
                cohort_panel_count = int(final_cohort["panel_count"])
                cohort_sample_slots = list(final_cohort["sample_slots"])
                cohort_binding_sha256 = str(
                    final_cohort["cohort_binding_sha256"]
                )
                cohort_difficulty_coverage = deepcopy(
                    final_cohort[
                        "difficulty_coverage_by_form_family"
                    ]
                )
                cohort_panel_scores = [
                    float(record["panel_score_diagnostic"])
                    for record in completed_panel_records
                ]
                cohort_panel_score_range = round(
                    max(cohort_panel_scores) - min(cohort_panel_scores),
                    6,
                )
                cohort_result = {
                    "schema_version": "DesktopAnalysisPrimaryCohortResult-v1",
                    "status": verdict,
                    "reason": result_reason,
                    "comparison_binding_sha256": plan[
                        "comparison_binding_sha256"
                    ],
                    "cohort_binding_sha256": cohort_binding_sha256,
                    "model_identity": self._panel_model_identity(target),
                    "panel_ids": list(final_cohort["panel_ids"]),
                    "sample_slots": cohort_sample_slots,
                    "panel_count": cohort_panel_count,
                    "panel_score_diagnostics": cohort_panel_scores,
                    "panel_score_range": cohort_panel_score_range,
                    "panel_stability_is_diagnostic_only": True,
                    "panel_repair_diagnostics": [
                        {
                            "panel_id": str(record["panel_id"]),
                            "sample_slot": int(record["sample_slot"]),
                            "first_pass_panel_score": float(
                                record[
                                    "first_pass_panel_score_diagnostic"
                                ]
                            ),
                            "final_panel_score": float(
                                record["panel_score_diagnostic"]
                            ),
                            "semantic_repair_rounds": int(
                                record.get("semantic_repair_rounds", 0)
                            ),
                            "semantic_repair_acceptance": deepcopy(
                                record.get(
                                    "semantic_repair_acceptance", []
                                )
                            ),
                        }
                        for record in completed_panel_records
                    ],
                    "aggregate_case_count_per_form": final_cohort[
                        "aggregate_case_count_per_form"
                    ],
                    "aggregate_before_tail_components": True,
                    "difficulty_coverage_by_form_family": (
                        cohort_difficulty_coverage
                    ),
                    "first_pass_form_scores": first_pass_form_scores,
                    "first_pass_role_ability_score": (
                        first_pass_role_ability_score
                    ),
                    "form_scores": form_scores,
                    "score": score,
                    "aggregate_initial_raw_scores": (
                        aggregate_initial_raw_scores
                    ),
                    "aggregate_raw_scores": aggregate_raw_scores,
                    "material_residuals": material_rows,
                    "cohort_fail_decision": cohort_fail_decision,
                    "calibration_residual_case_ids_by_form": (
                        calibration_sets
                    ),
                    "strict_model_fail": strict_model_fail,
                    "model_fail_requires_two_cases_two_panels_two_families": (
                        True
                    ),
                    "provider_received_answer_key": False,
                    "stable_score_cache_eligible": True,
                    "horizontal_comparison_eligible": True,
                }
                cohort_result["cohort_result_sha256"] = _sha256(
                    cohort_result
                )
                cohort_result_receipt = _write_json_create_only(
                    run_root / "panel_scores" / "cohort_result.json",
                    cohort_result,
                )
            public_result = {
                "schema_version": "WorkflowModelExamRunResult-v1",
                "status": verdict,
                "reason": result_reason,
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "requested_model": requested_model,
                "returned_model": requested_model,
                "score": score,
                "first_pass_role_ability_score": first_pass_role_ability_score,
                "executor_ref": self.EXECUTOR_REF,
                "executor_sha256": _file_sha256(Path(__file__)),
                "first_pass_form_scores": first_pass_form_scores,
                "post_repair_score": score,
                "current_panel_score_diagnostic": panel_score_diagnostic,
                "current_panel_form_scores": panel_form_scores,
                "current_panel_first_pass_role_ability_score": (
                    panel_first_pass_role_ability_score
                ),
                "execution_outcome": ExecutionOutcome.COMPLETED.value,
                "scorability": Scorability.SCOREABLE.value,
                "raw_quality_candidate_verdict": (
                    panel_quality_candidate_verdict
                ),
                "raw_quality_fail_admission": raw_quality_fail_admission,
                "quality_fail_admission": quality_fail_admission,
                **model_fail_projection,
                "form_scores": form_scores,
                "failed_exam_item_count": failed_item_count,
                "semantic_repair_rounds": len(repair_lifecycle),
                "semantic_repair_round_limit": (
                    self.SEMANTIC_REPAIR_ROUND_LIMIT
                ),
                "semantic_repair_lifecycle": repair_lifecycle,
                "semantic_repair_acceptance": (
                    repair_acceptance_receipts
                ),
                "repair_materiality_revision": (
                    ANALYSIS_REPAIR_MATERIALITY_REVISION
                ),
                "repair_acceptance_revision": (
                    ANALYSIS_REPAIR_ACCEPTANCE_REVISION
                ),
                "cumulative_category_repair_penalty": cumulative_penalty,
                "repair_penalty_applied_to_role_ability": False,
                "repair_penalty_semantics": (
                    "SEPARATE_REPAIRABILITY_DIAGNOSTIC_ONLY"
                ),
                "residual_category_burden": residual_burden,
                "material_residual_count": failed_item_count,
                "calibration_residual_count": calibration_residual_count,
                "transport_retry_is_semantic_repair": False,
                "request_attempts": state["request_attempts"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "external_process_launches": state["external_process_launches"],
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
                "initial_exact_progress_reuse": bool(
                    state["analysis_preloaded_initial_rows"]
                ),
                "initial_reused_case_row_count": sum(
                    len(action["reused_case_ids"])
                    for action in state[
                        "analysis_preloaded_initial_rows"
                    ]
                ),
                "completed_initial_answers_retested": False,
                "length_only_repair_outputs_reused": False,
                "initial_exact_progress_reuse_receipt": (
                    "initial_exact_progress_reuse_receipt.json"
                    if initial_preload_receipt is not None
                    else None
                ),
                "initial_exact_progress_reuse_receipt_sha256": (
                    initial_preload_receipt["sha256"]
                    if initial_preload_receipt is not None
                    else None
                ),
                "sample_revision": plan["sample_revision"],
                "sample_slot": plan["sample_slot"],
                "sample_manifest_sha256": plan["sample_manifest_sha256"],
                "panel_cohort_slots": plan["panel_cohort_slots"],
                "panels_required_for_cache": plan[
                    "panels_required_for_cache"
                ],
                "panel_evidence_only": panel_evidence_only,
                "single_panel_cache_eligible": False,
                "stable_score_cache_eligible": (
                    stable_score_cache_eligible
                ),
                "cohort_panel_count": cohort_panel_count,
                "cohort_sample_slots": cohort_sample_slots,
                "cohort_binding_sha256": cohort_binding_sha256,
                "cohort_difficulty_coverage_by_form_family": (
                    cohort_difficulty_coverage
                ),
                "cohort_panel_score_range": (
                    cohort_panel_score_range
                ),
                "cohort_result": (
                    "panel_scores/cohort_result.json"
                    if cohort_result_receipt is not None
                    else None
                ),
                "cohort_result_file_sha256": (
                    cohort_result_receipt["sha256"]
                    if cohort_result_receipt is not None
                    else None
                ),
                "panel_record": "panel_scores/panel_record.json",
                "panel_record_sha256": panel_record_receipt["sha256"],
                "panel_binding_sha256": plan["panel_binding_sha256"],
                "subject_projection_revision": plan[
                    "subject_projection_revision"
                ],
                "anchor_normalization_revision": plan[
                    "anchor_normalization_revision"
                ],
                "anchor_normalization_count": len(
                    state["analysis_anchor_normalizations"]
                ),
                "anchor_normalization_receipt": (
                    "normalizations/analysis_case_local_anchor_aliases.json"
                ),
                "anchor_normalization_receipt_sha256": (
                    normalization_receipt["sha256"]
                ),
                "structural_repair_projection_revision": plan[
                    "structural_repair_projection_revision"
                ],
                "semantic_repair_sharding_revision": plan[
                    "semantic_repair_sharding_revision"
                ],
                "semantic_repair_structural_retry_count": state[
                    "analysis_semantic_repair_structural_retries"
                ],
                "structural_retry_is_semantic_repair": False,
                "workload_profile": plan["workload_profile"],
                "comparison_binding_sha256": plan[
                    "comparison_binding_sha256"
                ],
                "comparison_cohort_id": plan["comparison_cohort_id"],
                "horizontal_comparison_eligible": (
                    horizontal_comparison_eligible
                ),
                "horizontal_comparison_pending_panel_cohort": (
                    horizontal_comparison_pending
                ),
                "horizontal_comparison_allowed_only_with_same_cohort": True,
                "cross_category_comparison_forbidden": True,
                "global_ranking_forbidden": True,
            }
            evidence = _write_json_create_only(
                run_root / "exam_result.json", public_result
            )
            return {
                **public_result,
                "schema_version": RESULT_SCHEMA,
                "kind": "WORKFLOW_NODE",
                "duration_ms": max(0, int((monotonic() - started) * 1000)),
                "exam_mode": plan["mode"],
                "exam_run_id": run_id,
                "exam_run_root": str(run_root.resolve()),
                "exam_evidence_sha256": evidence["sha256"],
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
                "request_attempts": state["request_attempts"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "external_process_launches": state["external_process_launches"],
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
                request_attempts=state["request_attempts"],
                provider_calls=state["provider_calls"],
                external_model_calls=state["external_model_calls"],
                external_network_calls=state["external_network_calls"],
                external_process_launches=state["external_process_launches"],
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


class QualityEmbeddingExamExecutor:
    """Run the frozen Quality embedding ranking exam through API or local."""

    EXECUTOR_REF = "Desktop_MODEL_EXAM_SUCCESSOR_Quality_EMBEDDING_EXECUTOR_V2"
    MODE = "LIVE_Quality_EMBEDDING_REFERENCE_REGRESSION"
    LOGICAL_CORPUS_CALLS = 240
    CORPORA_PER_MODEL_REQUEST = 5
    INPUTS_PER_CORPUS = 49
    INPUTS_PER_MODEL_REQUEST = CORPORA_PER_MODEL_REQUEST * INPUTS_PER_CORPUS
    PHYSICAL_MODEL_CALLS = 48
    API_BUDGET_CAP_CNY = 0.40
    SAMPLE_REVISION = "FULL_Quality_AB_240_CORPUS_V1"
    SCORING_PROJECTION_REVISION = "Desktop_EMBEDDING_ORDER_PRESERVING_BATCH_V1"
    BATCH_EQUIVALENCE_REVISION = (
        "Desktop_EMBEDDING_FIVE_CORPUS_ORDER_PRESERVING_BATCH_V1"
    )

    def __init__(
        self,
        *,
        scratch_root: Path,
        quality_package: str,
        reference_pack_root: Path,
    ) -> None:
        if not isinstance(quality_package, str) or not SAFE_PACKAGE.fullmatch(
            quality_package
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_Quality_PACKAGE_INVALID")
        self._scratch_root = Path(scratch_root).resolve()
        self._quality_package = quality_package
        self._reference_pack_root = Path(reference_pack_root).resolve()
        self._modules: dict[str, Any] | None = None

    def _quality(self) -> dict[str, Any]:
        if self._modules is None:
            self._modules = {
                "exam": importlib.import_module(
                    f"{self._quality_package}.embedding_text_exam"
                )
            }
        return self._modules

    def _embedding_diagnostics_for_scores(
        self,
        scores: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any] | None:
        """Successors may expose scorer-native detail without changing r0.2."""

        del scores
        return None

    @staticmethod
    def _profile_kind(target: Mapping[str, Any]) -> str | None:
        if target.get("kind") == "API":
            if (
                "siliconflow" in _compact(target.get("provider"))
                and target.get("model_name") == "Pro/BAAI/bge-m3"
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "API"
            return None
        if target.get("kind") == "LOCAL":
            if (
                target.get("endpoint_kind") == "ollama"
                and target.get("capability") == "EMBEDDING"
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
        if node.get("node_id") != "chunk_embedding":
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        profile_kind = self._profile_kind(target)
        if profile_kind is None:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        try:
            exam = self._quality()["exam"]
            diagnosis = exam.inspect_reference_pack(self._reference_pack_root)
            scorer = exam._load_bundled_scorer(self._reference_pack_root)
            replay = scorer.verify_reference_pack(self._reference_pack_root)
        except (ImportError, OSError, ValueError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_ASSETS_INVALID")
        if (
            diagnosis.get("status") != "PASS"
            or replay.get("verification_result") != "PASS"
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_PACK_NOT_ELIGIBLE")
        manifest = _load_json(
            self._reference_pack_root / "manifest.reference.json"
        )
        reference_pack_sha256 = _file_sha256(
            self._reference_pack_root / "SHA256SUMS.txt"
        )
        scoring_protocol_sha256 = _file_sha256(
            self._reference_pack_root / "scoring" / "scoring_protocol.json"
        )
        comparison_basis = {
            "exam_category_id": "Quality_EMBEDDING_TEXT_REFERENCE_REGRESSION",
            "role_id": "EMBEDDING_TEXT",
            "reference_pack_sha256": reference_pack_sha256,
            "scoring_protocol_sha256": scoring_protocol_sha256,
            "scoring_projection_revision": self.SCORING_PROJECTION_REVISION,
            "sample_revision": self.SAMPLE_REVISION,
        }
        comparison_cohort_id = (
            "EMBEDDING_TEXT:" + _sha256(comparison_basis)
        )
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA,
            "status": "READY",
            "mode": self.MODE,
            "reason": "Quality_REFERENCE_REGRESSION_READY",
            "node_id": "chunk_embedding",
            "executor_ref": self.EXECUTOR_REF,
            "profile_kind": profile_kind,
            "profile_ref": target["config_id"],
            "model_name": target["model_name"],
            "model_digest": target.get("model_digest"),
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": manifest.get("pack_id"),
            "reference_pack_revision": manifest.get("pack_revision"),
            "reference_pack_sha256": reference_pack_sha256,
            "scoring_protocol_sha256": scoring_protocol_sha256,
            "scoring_projection_revision": comparison_basis[
                "scoring_projection_revision"
            ],
            "exam_category_id": comparison_basis["exam_category_id"],
            "role_id": comparison_basis["role_id"],
            "sample_revision": comparison_basis["sample_revision"],
            "comparison_cohort_id": comparison_cohort_id,
            "comparison_binding_sha256": _sha256(
                {**comparison_basis, "comparison_cohort_id": comparison_cohort_id}
            ),
            "horizontal_comparison_eligible": True,
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "forms": ["A", "B"],
            "logical_corpus_calls": self.LOGICAL_CORPUS_CALLS,
            "maximum_model_calls": self.PHYSICAL_MODEL_CALLS,
            "corpora_per_model_request": self.CORPORA_PER_MODEL_REQUEST,
            "inputs_per_corpus": self.INPUTS_PER_CORPUS,
            "maximum_inputs_per_model_request": self.INPUTS_PER_MODEL_REQUEST,
            "batch_equivalence_revision": self.BATCH_EQUIVALENCE_REVISION,
            "budget_cap_cny": (
                self.API_BUDGET_CAP_CNY if profile_kind == "API" else None
            ),
            "cost_semantics": (
                "FROZEN_Quality_EMBEDDING_PRICE_CAP"
                if profile_kind == "API"
                else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
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
        if authorization.get("acknowledged_subscription_no_per_call_price") is not False:
            return "WORKFLOW_MODEL_EXAM_COST_SEMANTICS_ACKNOWLEDGEMENT_REQUIRED"
        return None

    def execute(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
        *,
        authorization: Mapping[str, Any] | None,
        structured_chat: Callable[..., Mapping[str, Any]],
        embedding_call: Callable[..., Mapping[str, Any]],
    ) -> dict[str, Any]:
        del structured_chat
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
        reason = self._authorization_reason(authorization, plan)
        if reason is not None:
            return _not_run(reason, requested_model=requested_model)
        run_id = "workflow-exam-" + uuid.uuid4().hex
        run_root = self._scratch_root / "workflow_exams" / run_id
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
            exam = self._quality()["exam"]
            scorer = exam._load_bundled_scorer(self._reference_pack_root)
            scores: dict[str, dict[str, Any]] = {}
            dimension: int | None = None
            logical_call_index = 0
            for form_id in ("A", "B"):
                tasks = _load_json(
                    self._reference_pack_root / "forms" / f"form_{form_id}.json"
                )["tasks"]
                corpora = _load_json(
                    self._reference_pack_root
                    / "corpora"
                    / f"form_{form_id}_corpora.json"
                )["corpora"]
                gold_rows = _load_json(
                    self._reference_pack_root
                    / "gold"
                    / f"form_{form_id}_gold.json"
                )["gold"]
                tasks_by_corpus: dict[str, list[Mapping[str, Any]]] = {}
                for task in tasks:
                    tasks_by_corpus.setdefault(str(task["corpus_id"]), []).append(task)
                rankings: dict[str, list[str]] = {}
                work_rows: list[dict[str, Any]] = []
                for corpus_index, corpus in enumerate(corpora, start=1):
                    corpus_tasks = tasks_by_corpus[str(corpus["corpus_id"])]
                    documents = corpus["documents"]
                    inputs = [row["text"] for row in documents] + [
                        row["query"] for row in corpus_tasks
                    ]
                    if len(inputs) != self.INPUTS_PER_CORPUS:
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_EMBEDDING_CORPUS_INPUT_COUNT_MISMATCH"
                        )
                    work_rows.append(
                        {
                            "corpus_index": corpus_index,
                            "corpus": corpus,
                            "tasks": corpus_tasks,
                            "documents": documents,
                            "inputs": inputs,
                        }
                    )
                for batch_start in range(
                    0, len(work_rows), self.CORPORA_PER_MODEL_REQUEST
                ):
                    batch_rows = work_rows[
                        batch_start : batch_start + self.CORPORA_PER_MODEL_REQUEST
                    ]
                    inputs = [
                        value
                        for work in batch_rows
                        for value in work["inputs"]
                    ]
                    logical_call_index += 1
                    claim = {
                        "schema_version": "WorkflowEmbeddingPreSendClaim-v1",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "call_id": f"call-{logical_call_index:02d}",
                        "call_kind": "EMBEDDING_CORPUS_BATCH",
                        "profile_kind": plan["profile_kind"],
                        "profile_ref": plan["profile_ref"],
                        "purpose": "workflow_model_exam",
                        "route": (
                            "EXISTING_SETTINGS_API_TRANSPORT"
                            if plan["profile_kind"] == "API"
                            else "OLLAMA_LOOPBACK"
                        ),
                        "requested_model": requested_model,
                        "model_digest": target.get("model_digest"),
                        "form_id": form_id,
                        "input_count": len(inputs),
                        "input_exact_set_sha256": _sha256(inputs),
                        "behavior_sha256": _sha256(
                            {
                                "model": requested_model,
                                "inputs": inputs,
                                "purpose": "workflow_model_exam",
                            }
                        ),
                        "plan_sha256": plan["plan_sha256"],
                        "provider_received_gold": False,
                    }
                    claim_receipt = _write_json_create_only(
                        run_root
                        / "claims"
                        / f"call_{logical_call_index:02d}.json",
                        claim,
                    )
                    result = dict(
                        embedding_call(
                            profile_kind=str(target["kind"]),
                            service=target,
                            model=target,
                            inputs=inputs,
                            purpose="workflow_model_exam",
                            timeout_seconds=120,
                        )
                    )
                    for field in (
                        "provider_calls",
                        "external_model_calls",
                        "external_network_calls",
                        "external_process_launches",
                    ):
                        state[field] += int(result.get(field) or 0)
                    state["request_attempts"] += int(
                        result.get("external_model_calls") or 0
                    )
                    receipt = result.get("execution_receipt")
                    if isinstance(receipt, Mapping):
                        estimate = receipt.get("estimated_cost_cny")
                        if isinstance(estimate, (int, float)) and not isinstance(estimate, bool):
                            state["estimated_cost_cny"] += float(estimate)
                        usage = receipt.get("token_usage")
                        if isinstance(usage, Mapping):
                            for field in ("prompt_tokens", "completion_tokens"):
                                value = usage.get(field)
                                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                                    state["token_usage"][field] += value
                    if result.get("status") != "PASS":
                        raise ValueError(
                            str(result.get("reason") or "EMBEDDING_CALL_FAILED")
                        )
                    vectors = result.get("vectors")
                    if (
                        not isinstance(vectors, list)
                        or len(vectors) != len(inputs)
                    ):
                        raise ValueError("EMBEDDING_VECTORS_MISSING")
                    observed_dimension = scorer.validate_vectors(
                        vectors,
                        len(inputs),
                        expected_dim=(
                            1024
                            if str(target["kind"]) == "API"
                            else dimension
                        ),
                    )
                    if dimension is None:
                        dimension = observed_dimension
                    elif dimension != observed_dimension:
                        raise ValueError("EMBEDDING_DIMENSION_DRIFT")
                    vector_offset = 0
                    batch_corpus_receipts: list[dict[str, Any]] = []
                    for work in batch_rows:
                        corpus_vectors = vectors[
                            vector_offset : vector_offset + self.INPUTS_PER_CORPUS
                        ]
                        vector_offset += self.INPUTS_PER_CORPUS
                        documents = work["documents"]
                        corpus_tasks = work["tasks"]
                        doc_vectors = corpus_vectors[: len(documents)]
                        query_vectors = corpus_vectors[len(documents) :]
                        doc_ids = [row["unit_id"] for row in documents]
                        for task, query_vector in zip(
                            corpus_tasks, query_vectors
                        ):
                            cosine_rows, _ = scorer.rank_vectors(
                                doc_ids, doc_vectors, query_vector
                            )
                            rankings[str(task["task_id"])] = [
                                str(row["unit_id"]) for row in cosine_rows
                            ]
                        batch_corpus_receipts.append(
                            {
                                "corpus_index": work["corpus_index"],
                                "corpus_id": work["corpus"]["corpus_id"],
                                "input_count": self.INPUTS_PER_CORPUS,
                            }
                        )
                    _write_json_create_only(
                        run_root
                        / "receipts"
                        / (
                            f"form_{form_id}_corpus_batch_"
                            f"{batch_start // self.CORPORA_PER_MODEL_REQUEST + 1:03d}.json"
                        ),
                        {
                            "schema_version": "WorkflowEmbeddingCorpusBatchReceipt-v2",
                            "form": form_id,
                            "corpora": batch_corpus_receipts,
                            "input_count": len(inputs),
                            "physical_model_calls": result[
                                "external_model_calls"
                            ],
                            "pre_send_claim_sha256": claim_receipt["sha256"],
                            "order_preserving_batch_equivalent": True,
                            "execution_receipt": deepcopy(dict(receipt or {})),
                        },
                    )
                scores[form_id] = scorer.score_form(gold_rows, rankings)
                _write_json_create_only(
                    run_root / "scores" / f"form_{form_id}.json",
                    scores[form_id],
                )
            if (
                logical_call_index != self.PHYSICAL_MODEL_CALLS
                or state["request_attempts"] != self.PHYSICAL_MODEL_CALLS
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_CALL_COUNT_MISMATCH")
            mean = (
                float(scores["A"]["score"]) + float(scores["B"]["score"])
            ) / 2.0
            critical = int(scores["A"]["critical_miss_count"]) + int(
                scores["B"]["critical_miss_count"]
            )
            score = int(round(mean))
            embedding_diagnostics = self._embedding_diagnostics_for_scores(scores)
            cap = plan.get("budget_cap_cny")
            if cap is not None and state["estimated_cost_cny"] > float(cap):
                raise ValueError("WORKFLOW_MODEL_EXAM_COST_CAP_EXCEEDED")
            public_result = {
                "schema_version": "WorkflowModelExamRunResult-v1",
                "status": "PASS",
                "execution_outcome": "COMPLETED",
                "scorability": "SCOREABLE",
                "raw_quality_candidate_verdict": "NOT_ASSESSED",
                "reason": (
                    "Quality_EMBEDDING_REFERENCE_REGRESSION_"
                    "TECHNICALLY_COMPLETED"
                ),
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "requested_model": requested_model,
                "returned_model": requested_model,
                "score": score,
                "score_exact": round(mean, 6),
                "quality_verdict": "NOT_ASSESSED",
                "acceptance_verdict": "NOT_ASSESSED",
                "quality_threshold_defined": False,
                "score_is_descriptive_within_category": True,
                "form_scores": {
                    "A": scores["A"]["score"],
                    "B": scores["B"]["score"],
                },
                "exam_category_id": plan["exam_category_id"],
                "role_id": plan["role_id"],
                "sample_revision": plan["sample_revision"],
                "comparison_cohort_id": plan["comparison_cohort_id"],
                "comparison_binding_sha256": plan[
                    "comparison_binding_sha256"
                ],
                "horizontal_comparison_eligible": True,
                "horizontal_comparison_allowed_only_with_same_cohort": True,
                "cross_category_comparison_forbidden": True,
                "global_ranking_forbidden": True,
                "failed_exam_item_count": critical,
                "critical_miss_count": critical,
                "failed_exam_item_count_semantics": (
                    "DIAGNOSTIC_CRITICAL_MISS_COUNT_NOT_ADMISSION_FAIL"
                ),
                "dimension": dimension,
                **state,
                "estimated_cost_cny": (
                    round(state["estimated_cost_cny"], 9)
                    if plan["profile_kind"] == "API"
                    else 0.0
                ),
                "actual_cost": None,
                "cost_semantics": plan["cost_semantics"],
                "reference_regression_only": True,
                "blind_holdout_eligible": False,
                "qualification_eligible": False,
                "provider_received_answer_key": False,
            }
            if embedding_diagnostics is not None:
                public_result["embedding_diagnostics"] = embedding_diagnostics
            evidence = _write_json_create_only(
                run_root / "exam_result.json", public_result
            )
            return {
                **public_result,
                "schema_version": RESULT_SCHEMA,
                "kind": "WORKFLOW_NODE",
                "duration_ms": max(0, int((monotonic() - started) * 1000)),
                "exam_mode": plan["mode"],
                "exam_run_id": run_id,
                "exam_run_root": str(run_root.resolve()),
                "exam_evidence_sha256": evidence["sha256"],
            }
        except Exception as error:
            reason = str(error) or type(error).__name__
            if len(reason) > 200:
                reason = type(error).__name__
            failure = {
                "schema_version": "WorkflowModelExamRunFailure-v1",
                "status": "NOT_ASSESSED",
                "execution_outcome": classify_execution_outcome(
                    {"status": "ERROR", "reason": reason}
                ).value,
                "scorability": "NOT_ASSESSED",
                "quality_verdict": "NOT_ASSESSED",
                "reason": reason,
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                **state,
                "estimated_cost_cny": (
                    round(state["estimated_cost_cny"], 9)
                    if plan["profile_kind"] == "API"
                    else 0.0
                ),
                "score": None,
            }
            evidence = _write_json_create_only(
                run_root / "exam_failure.json", failure
            )
            result = _not_run(reason, requested_model=requested_model)
            projected_state = {
                **state,
                "estimated_cost_cny": failure["estimated_cost_cny"],
            }
            result.update(
                duration_ms=max(0, int((monotonic() - started) * 1000)),
                execution_outcome=failure["execution_outcome"],
                scorability="NOT_ASSESSED",
                quality_verdict="NOT_ASSESSED",
                exam_run_id=run_id,
                exam_run_root=str(run_root.resolve()),
                exam_evidence_sha256=evidence["sha256"],
                cost_semantics=plan["cost_semantics"],
                **projected_state,
            )
            return result


class QualityNewEmbeddingLegacyExamExecutor(QualityEmbeddingExamExecutor):
    """Keep the full 240-corpus exam while halving order-preserving requests."""

    EXECUTOR_REF = "Quality_NEW_Quality_EMBEDDING_EXECUTOR_V1"
    MODE = "LIVE_Quality_NEW_EMBEDDING_FULL_PACK_BATCHED_REFERENCE_REGRESSION"
    CORPORA_PER_MODEL_REQUEST = 10
    INPUTS_PER_MODEL_REQUEST = CORPORA_PER_MODEL_REQUEST * QualityEmbeddingExamExecutor.INPUTS_PER_CORPUS
    PHYSICAL_MODEL_CALLS = 24
    SCORING_PROJECTION_REVISION = (
        "Quality_NEW_EMBEDDING_ORDER_PRESERVING_BATCH_V1"
    )
    BATCH_EQUIVALENCE_REVISION = (
        "Quality_NEW_EMBEDDING_TEN_CORPUS_ORDER_PRESERVING_BATCH_V1"
    )

    def _embedding_diagnostics_for_scores(
        self,
        scores: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        forms: dict[str, Any] = {}
        task_count = 0
        critical_miss_count = 0
        for form_id in ("A", "B"):
            source = scores[form_id]
            task_scores = source.get("task_scores")
            observed_task_count = (
                len(task_scores) if isinstance(task_scores, list) else None
            )
            if observed_task_count is not None:
                task_count += observed_task_count
            form_critical = int(source.get("critical_miss_count") or 0)
            critical_miss_count += form_critical
            forms[form_id] = {
                "score": float(source["score"]),
                "component_raw_scores": deepcopy(
                    dict(source.get("component_raw_scores") or {})
                ),
                "family_means": deepcopy(
                    dict(source.get("family_means") or {})
                ),
                "difficulty_means": deepcopy(
                    dict(source.get("difficulty_means") or {})
                ),
                "critical_miss_count": form_critical,
                "task_count": observed_task_count,
            }
        mean = statistics.fmean(forms[form_id]["score"] for form_id in ("A", "B"))
        diagnostic: dict[str, Any] = {
            "schema_version": "QualityNewEmbeddingLiveDiagnostic-v1",
            "scoring_protocol_revision": "embedding_text_scoring_r0.2",
            "scoring_projection_revision": self.SCORING_PROJECTION_REVISION,
            "score_views": {
                "primary": {
                    "kind": "RAW_DEDUCTION_SCORE",
                    "mean": round(mean, 6),
                    "qualification_eligible": False,
                },
                "post_hoc_selection": {
                    "status": "NOT_APPLIED_TO_NEW_MODEL_RUN",
                    "qualification_eligible": False,
                },
            },
            "forms": forms,
            "critical_misses": {
                "count": critical_miss_count,
                "task_count": task_count or None,
                "rate_percent": (
                    round(critical_miss_count * 100.0 / task_count, 6)
                    if task_count
                    else None
                ),
                "semantics": "DIAGNOSTIC_CRITICAL_MISS_NOT_ADMISSION_FAIL",
            },
            "selection_contract": {
                "single_aggregate_is_sufficient_for_selection": False,
                "repeatability_and_operational_profile_required": True,
                "meaningful_pairwise_separation_must_be_demonstrated": True,
                "quality_threshold_defined": False,
                "model_failure_established": False,
            },
            "execution_provenance": {
                "physical_model_call_target": self.PHYSICAL_MODEL_CALLS,
                "corpora_per_model_request": self.CORPORA_PER_MODEL_REQUEST,
                "logical_corpus_count": self.LOGICAL_CORPUS_CALLS,
                "historical_quality_call_count_relabelled": False,
            },
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
        }
        diagnostic["diagnostic_sha256"] = _sha256(diagnostic)
        return diagnostic


class QualityNewEmbeddingExamExecutor(QualityEmbeddingExamExecutor):
    """Run the production-shaped successor and a bounded legacy diagnostic.

    The production track models the Core retrieval pool and uses Recall@15.
    A small, immutable sample of the original Quality short-CJK pack remains a
    separate adversarial diagnostic.  The two scores are deliberately never
    combined.  A successor overlay assesses first-stage role adequacy while
    formal qualification still requires a live production-shaped run.
    """

    EXECUTOR_REF = "Quality_NEW_Quality_EMBEDDING_SUCCESSOR_EXECUTOR_V2"
    MODE = "LIVE_Quality_NEW_EMBEDDING_PRODUCTION_AND_ADVERSARIAL_REFERENCE_REGRESSION"
    SAMPLE_REVISION = "EMBEDDING_PRODUCTION_R0_2_PLUS_FROZEN_V1_SAMPLE_V1"
    SCORING_PROJECTION_REVISION = (
        "Quality_NEW_EMBEDDING_PRODUCTION_RECALL_AT_15_ROLE_ADEQUACY_V2"
    )
    BATCH_EQUIVALENCE_REVISION = (
        "Quality_NEW_EMBEDDING_PROVIDER_LIMIT_ALIGNED_BATCH_V1"
    )
    REPEATABILITY_SELECTION_REVISION = (
        "TWO_DOCUMENTS_AND_TWO_QUERIES_PER_PRODUCTION_CORPUS_V1"
    )
    PRODUCTION_INPUT_COUNT = 392
    ADVERSARIAL_INPUT_COUNT = 294
    REPEATABILITY_INPUT_COUNT = 16
    BGE_BATCH_SIZE = 256
    QWEN_BATCH_SIZE = 32
    API_BUDGET_CAP_CNY = 0.40
    SUPPORTED_API_MODELS = {
        "BAAI/bge-m3",
        "Pro/BAAI/bge-m3",
        "Qwen/Qwen3-VL-Embedding-8B",
    }

    def __init__(
        self,
        *,
        scratch_root: Path,
        quality_package: str,
        reference_pack_root: Path,
    ) -> None:
        super().__init__(
            scratch_root=scratch_root,
            quality_package=quality_package,
            reference_pack_root=reference_pack_root,
        )
        self._legacy_reference_pack_root = (
            self._reference_pack_root.parent.parent
            / "v1"
            / "reference_pack"
        ).resolve()
        self._successor_assets: dict[str, Any] | None = None
        self._successor_scorer: Any | None = None

    @staticmethod
    def _profile_kind(target: Mapping[str, Any]) -> str | None:
        if target.get("kind") == "API":
            if (
                "siliconflow" in _compact(target.get("provider"))
                and target.get("model_name")
                in QualityNewEmbeddingExamExecutor.SUPPORTED_API_MODELS
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "API"
            return None
        if target.get("kind") == "LOCAL":
            if (
                target.get("endpoint_kind") == "ollama"
                and target.get("capability") == "EMBEDDING"
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

    @staticmethod
    def _batch_size(target: Mapping[str, Any]) -> int:
        # The reused Ollama transport sends one source text per physical request
        # (and keeps its existing no-truncation recovery). Plan and claims must
        # match that boundary; an API-sized batch falsely failed the receipt.
        if target.get("kind") == "LOCAL":
            return 1
        model_name = str(target.get("model_name") or "")
        return (
            QualityNewEmbeddingExamExecutor.QWEN_BATCH_SIZE
            if "qwen" in model_name.casefold()
            else QualityNewEmbeddingExamExecutor.BGE_BATCH_SIZE
        )

    @staticmethod
    def _expected_dimension(target: Mapping[str, Any]) -> int | None:
        if target.get("kind") != "API":
            return None
        if target.get("model_name") == "Qwen/Qwen3-VL-Embedding-8B":
            return 4096
        return 1024

    @staticmethod
    def _physical_call_count(batch_size: int) -> int:
        return sum(
            math.ceil(count / batch_size)
            for count in (
                QualityNewEmbeddingExamExecutor.PRODUCTION_INPUT_COUNT,
                QualityNewEmbeddingExamExecutor.ADVERSARIAL_INPUT_COUNT,
                QualityNewEmbeddingExamExecutor.REPEATABILITY_INPUT_COUNT,
            )
        )

    def _load_successor_scorer(self) -> Any:
        if self._successor_scorer is not None:
            return self._successor_scorer
        scorer_path = (
            self._reference_pack_root
            / "scoring"
            / "embedding_successor_scorer.py"
        )
        module_name = (
            "_memorive_embedding_successor_"
            + hashlib.sha256(str(scorer_path).encode("utf-8")).hexdigest()
        )
        spec = importlib.util.spec_from_file_location(module_name, scorer_path)
        if spec is None or spec.loader is None:
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_SCORER_INVALID")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not callable(getattr(module, "score_track", None)):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_SCORER_INVALID")
        self._successor_scorer = module
        return module

    def _verify_successor_checksums(self) -> dict[str, Any]:
        manifest_path = self._reference_pack_root / "manifest.reference.json"
        sums_path = self._reference_pack_root / "SHA256SUMS.txt"
        manifest = _load_json(manifest_path)
        if (
            manifest.get("schema_version")
            != "EmbeddingSuccessorReferenceManifest-v1"
            or manifest.get("status")
            != "FROZEN_EXECUTABLE_REFERENCE_PACK_CANDIDATE"
            or manifest.get("pack_revision")
            != "embedding_text_production_adversarial_successor_r0.2"
            or manifest.get("content_class") != "SYNTHETIC_REDISTRIBUTABLE"
            or manifest.get("gold_runtime_local_only") is not True
            or manifest.get("gold_sent_to_provider") is not False
            or manifest.get("real_pdf_or_chunk_text_included") is not False
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_MANIFEST_INVALID")
        self_hash = manifest.get("manifest_content_sha256")
        payload = deepcopy(manifest)
        payload.pop("manifest_content_sha256", None)
        if self_hash != _sha256(payload):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_MANIFEST_HASH_INVALID")

        checksums: dict[str, str] = {}
        for raw_line in sums_path.read_text(encoding="ascii").splitlines():
            parts = raw_line.split("  ", 1)
            if (
                len(parts) != 2
                or not re.fullmatch(r"[A-F0-9]{64}", parts[0])
                or not parts[1]
                or "\\" in parts[1]
                or parts[1].startswith("/")
                or ".." in parts[1].split("/")
                or parts[1] in checksums
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_SUMS_INVALID")
            checksums[parts[1]] = parts[0]
        expected_paths = {
            "manifest.reference.json",
            *(
                str(value)
                for value in (manifest.get("files") or {}).keys()
            ),
        }
        if set(checksums) != expected_paths:
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_SUMS_EXACT_SET_INVALID")
        for relative, expected_hash in checksums.items():
            path = self._reference_pack_root.joinpath(*relative.split("/"))
            if not path.is_file() or _file_sha256(path) != expected_hash:
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_FILE_HASH_INVALID")
        manifest_files = manifest.get("files")
        if not isinstance(manifest_files, Mapping) or any(
            checksums.get(str(relative)) != expected_hash
            for relative, expected_hash in manifest_files.items()
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_MANIFEST_FILES_INVALID")
        return manifest

    def _load_assets(self) -> dict[str, Any]:
        if self._successor_assets is not None:
            return self._successor_assets
        manifest = self._verify_successor_checksums()
        production = _load_json(
            self._reference_pack_root / "public" / "production_corpora.json"
        )
        gold_document = _load_json(
            self._reference_pack_root / "gold" / "production_gold.json"
        )
        sample = _load_json(
            self._reference_pack_root / "sampling" / "adversarial_sample.json"
        )
        protocol = _load_json(
            self._reference_pack_root / "scoring" / "scoring_protocol.json"
        )
        revision = manifest["pack_revision"]
        corpora = production.get("corpora")
        tasks = production.get("tasks")
        gold_rows = gold_document.get("gold")
        if (
            production.get("schema_version") != "EmbeddingProductionTrackForms-v1"
            or production.get("pack_revision") != revision
            or production.get("provider_received_gold") is not False
            or gold_document.get("schema_version") != "EmbeddingProductionTrackGold-v1"
            or gold_document.get("pack_revision") != revision
            or gold_document.get("provider_received_gold") is not False
            or protocol.get("schema_version") != "EmbeddingSuccessorScoringProtocol-v1"
            or protocol.get("single_aggregate_may_establish_fail") is not False
            or protocol.get("qualification_threshold_defined") is not False
            or not isinstance(corpora, list)
            or not isinstance(tasks, list)
            or not isinstance(gold_rows, list)
            or len(corpora) != 4
            or len(tasks) != 24
            or len(gold_rows) != 24
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_ASSETS_INVALID")

        corpus_by_id: dict[str, Mapping[str, Any]] = {}
        document_ids: set[str] = set()
        document_lengths: list[int] = []
        for corpus, expected_count in zip(corpora, (80, 80, 80, 128)):
            corpus_id = corpus.get("corpus_id") if isinstance(corpus, Mapping) else None
            documents = corpus.get("documents") if isinstance(corpus, Mapping) else None
            if (
                not isinstance(corpus_id, str)
                or not corpus_id
                or corpus_id in corpus_by_id
                or not isinstance(documents, list)
                or len(documents) != expected_count
                or corpus.get("scope_kind") not in {"PAPER_SCOPED", "CROSS_PAPER"}
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_CORPUS_INVALID")
            corpus_by_id[corpus_id] = corpus
            for document in documents:
                unit_id = document.get("unit_id") if isinstance(document, Mapping) else None
                text = document.get("text") if isinstance(document, Mapping) else None
                if (
                    not isinstance(unit_id, str)
                    or not unit_id
                    or unit_id in document_ids
                    or not isinstance(text, str)
                    or not 180 <= len(text) <= 810
                    or len(text.encode("utf-8")) > 65536
                ):
                    raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_DOCUMENT_INVALID")
                document_ids.add(unit_id)
                document_lengths.append(len(text))
        task_by_id: dict[str, Mapping[str, Any]] = {}
        tasks_by_corpus: dict[str, list[Mapping[str, Any]]] = {
            corpus_id: [] for corpus_id in corpus_by_id
        }
        cjk_query_count = 0
        query_lengths: list[int] = []
        for task in tasks:
            task_id = task.get("task_id") if isinstance(task, Mapping) else None
            corpus_id = task.get("corpus_id") if isinstance(task, Mapping) else None
            query = task.get("query") if isinstance(task, Mapping) else None
            if (
                not isinstance(task_id, str)
                or not task_id
                or task_id in task_by_id
                or corpus_id not in corpus_by_id
                or not isinstance(query, str)
                or not 150 <= len(query) <= 225
                or len(query.encode("utf-8")) > 65536
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_TASK_INVALID")
            task_by_id[task_id] = task
            tasks_by_corpus[str(corpus_id)].append(task)
            query_lengths.append(len(query))
            if re.search(r"[\u3400-\u9fff]", query):
                cjk_query_count += 1
        if (
            set(task_by_id) != {
                str(row.get("task_id"))
                for row in gold_rows
                if isinstance(row, Mapping)
            }
            or any(len(rows) != 6 for rows in tasks_by_corpus.values())
            or cjk_query_count != 4
            or len(document_ids) != 368
            or manifest.get("production_corpus_count") != 4
            or manifest.get("production_task_count") != 24
            or manifest.get("production_document_count") != 368
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_TOPOLOGY_INVALID")
        for gold in gold_rows:
            task_id = str(gold["task_id"])
            corpus_id = str(gold.get("corpus_id"))
            if corpus_id != task_by_id[task_id].get("corpus_id"):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_GOLD_SCOPE_INVALID")
            allowed_ids = {
                str(row["unit_id"])
                for row in corpus_by_id[corpus_id]["documents"]
            }
            relevant = set(gold.get("relevant_ids") or [])
            critical = set(gold.get("critical_ids") or [])
            forbidden = set(gold.get("forbidden_ids") or [])
            facets = [set(row) for row in (gold.get("facet_groups") or [])]
            if (
                not relevant
                or not critical
                or not facets
                or not relevant <= allowed_ids
                or not critical <= relevant
                or not forbidden <= allowed_ids
                or relevant & forbidden
                or any(not facet or not facet <= relevant for facet in facets)
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_GOLD_INVALID")

        exam = self._quality()["exam"]
        legacy_sha256 = _file_sha256(
            self._legacy_reference_pack_root / "SHA256SUMS.txt"
        )
        legacy_scorer = exam._load_bundled_scorer(
            self._legacy_reference_pack_root
        )
        diagnosis = exam.inspect_reference_pack(self._legacy_reference_pack_root)
        replay = legacy_scorer.verify_reference_pack(
            self._legacy_reference_pack_root
        )
        if (
            diagnosis.get("status") != "PASS"
            or replay.get("verification_result") != "PASS"
            or sample.get("schema_version")
            != "EmbeddingAdversarialSampleManifest-v1"
            or sample.get("source_pack_revision")
            != "embedding_text_reference_exam_r0.5"
            or sample.get("source_pack_sha256") != legacy_sha256
            or manifest.get("adversarial_source_pack_sha256") != legacy_sha256
            or sample.get("corpus_count") != 6
            or sample.get("task_count") != 42
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_LEGACY_SAMPLE_INVALID")

        adversarial_forms: dict[str, Any] = {}
        indices_by_form = sample.get("corpus_indices_by_form")
        if not isinstance(indices_by_form, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_LEGACY_SAMPLE_INVALID")
        for form_id in ("A", "B"):
            indices = indices_by_form.get(form_id)
            if (
                not isinstance(indices, list)
                or len(indices) != 3
                or any(not isinstance(index, int) or isinstance(index, bool) for index in indices)
                or len(set(indices)) != len(indices)
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_LEGACY_SAMPLE_INVALID")
            form_tasks = _load_json(
                self._legacy_reference_pack_root / "forms" / f"form_{form_id}.json"
            )["tasks"]
            form_corpora = _load_json(
                self._legacy_reference_pack_root
                / "corpora"
                / f"form_{form_id}_corpora.json"
            )["corpora"]
            form_gold = _load_json(
                self._legacy_reference_pack_root
                / "gold"
                / f"form_{form_id}_gold.json"
            )["gold"]
            selected_corpora = [form_corpora[index - 1] for index in indices]
            selected_ids = {str(row["corpus_id"]) for row in selected_corpora}
            selected_tasks = [
                row for row in form_tasks if str(row["corpus_id"]) in selected_ids
            ]
            selected_task_ids = {str(row["task_id"]) for row in selected_tasks}
            selected_gold = [
                row for row in form_gold if str(row["task_id"]) in selected_task_ids
            ]
            if (
                len(selected_tasks) != 21
                or len(selected_gold) != 21
                or any(len(row["documents"]) != 42 for row in selected_corpora)
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_LEGACY_SAMPLE_INVALID")
            adversarial_forms[form_id] = {
                "corpora": selected_corpora,
                "tasks": selected_tasks,
                "gold": selected_gold,
            }

        self._load_successor_scorer()
        self._successor_assets = {
            "manifest": manifest,
            "production": production,
            "gold": gold_rows,
            "corpus_by_id": corpus_by_id,
            "tasks_by_corpus": tasks_by_corpus,
            "protocol": protocol,
            "reference_pack_sha256": _file_sha256(
                self._reference_pack_root / "SHA256SUMS.txt"
            ),
            "legacy_reference_pack_sha256": legacy_sha256,
            "legacy_scorer": legacy_scorer,
            "adversarial_forms": adversarial_forms,
            "workload_shape": {
                "document_mean_chars": round(statistics.fmean(document_lengths), 6),
                "document_median_chars": round(statistics.median(document_lengths), 6),
                "document_max_chars": max(document_lengths),
                "query_mean_chars": round(statistics.fmean(query_lengths), 6),
                "query_median_chars": round(statistics.median(query_lengths), 6),
                "query_max_chars": max(query_lengths),
                "cjk_query_count": cjk_query_count,
            },
        }
        return self._successor_assets

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        if node.get("node_id") != "chunk_embedding":
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        profile_kind = self._profile_kind(target)
        if profile_kind is None:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        try:
            assets = self._load_assets()
        except (ImportError, OSError, ValueError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_ASSETS_INVALID")
        batch_size = self._batch_size(target)
        maximum_model_calls = self._physical_call_count(batch_size)
        manifest = assets["manifest"]
        scoring_protocol_sha256 = _file_sha256(
            self._reference_pack_root / "scoring" / "scoring_protocol.json"
        )
        comparison_basis = {
            "exam_category_id": "Quality_NEW_EMBEDDING_PRODUCTION_RECALL_REFERENCE_REGRESSION",
            "role_id": "EMBEDDING_TEXT",
            "reference_pack_sha256": assets["reference_pack_sha256"],
            "scoring_protocol_sha256": scoring_protocol_sha256,
            "scoring_projection_revision": self.SCORING_PROJECTION_REVISION,
            "sample_revision": self.SAMPLE_REVISION,
            "input_modality": "TEXT_ONLY",
        }
        comparison_cohort_id = "EMBEDDING_TEXT:" + _sha256(comparison_basis)
        adversarial_basis = {
            "exam_category_id": "Quality_EMBEDDING_TEXT_ADVERSARIAL_DIAGNOSTIC_SAMPLE",
            "role_id": "EMBEDDING_TEXT",
            "source_pack_sha256": assets["legacy_reference_pack_sha256"],
            "sample_revision": self.SAMPLE_REVISION,
        }
        adversarial_cohort_id = "EMBEDDING_TEXT_ADVERSARIAL:" + _sha256(
            adversarial_basis
        )
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA,
            "status": "READY",
            "mode": self.MODE,
            "reason": "Quality_NEW_EMBEDDING_SUCCESSOR_REFERENCE_REGRESSION_READY",
            "node_id": "chunk_embedding",
            "executor_ref": self.EXECUTOR_REF,
            "profile_kind": profile_kind,
            "profile_ref": target["config_id"],
            "model_name": target["model_name"],
            "model_digest": target.get("model_digest"),
            "expected_dimension": self._expected_dimension(target),
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": manifest["pack_id"],
            "reference_pack_revision": manifest["pack_revision"],
            "reference_pack_sha256": assets["reference_pack_sha256"],
            "legacy_reference_pack_sha256": assets["legacy_reference_pack_sha256"],
            "scoring_protocol_sha256": scoring_protocol_sha256,
            "scoring_projection_revision": self.SCORING_PROJECTION_REVISION,
            "exam_category_id": comparison_basis["exam_category_id"],
            "role_id": comparison_basis["role_id"],
            "sample_revision": self.SAMPLE_REVISION,
            "comparison_cohort_id": comparison_cohort_id,
            "comparison_binding_sha256": _sha256(
                {**comparison_basis, "comparison_cohort_id": comparison_cohort_id}
            ),
            "adversarial_comparison_cohort_id": adversarial_cohort_id,
            "adversarial_comparison_binding_sha256": _sha256(
                {**adversarial_basis, "comparison_cohort_id": adversarial_cohort_id}
            ),
            "horizontal_comparison_eligible": True,
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "production_primary_score": "PRODUCTION_RECALL_AT_15",
            "adversarial_score_is_independent": True,
            "single_aggregate_may_establish_fail": False,
            "pack_native_quality_threshold_defined": False,
            "quality_threshold_defined": True,
            "role_adequacy_policy": deepcopy(
                EMBEDDING_ROLE_ADEQUACY_POLICY
            ),
            "single_reference_regression_may_establish_model_fail": False,
            "production_input_count": self.PRODUCTION_INPUT_COUNT,
            "adversarial_input_count": self.ADVERSARIAL_INPUT_COUNT,
            "repeatability_input_count": self.REPEATABILITY_INPUT_COUNT,
            "maximum_model_calls": maximum_model_calls,
            "maximum_inputs_per_model_request": batch_size,
            "batch_equivalence_revision": self.BATCH_EQUIVALENCE_REVISION,
            "repeatability_selection_revision": self.REPEATABILITY_SELECTION_REVISION,
            "workload_shape": deepcopy(assets["workload_shape"]),
            "budget_cap_cny": (
                self.API_BUDGET_CAP_CNY if profile_kind == "API" else None
            ),
            "cost_semantics": (
                "MODEL_SPECIFIC_EMBEDDING_INPUT_PRICE_ESTIMATE"
                if profile_kind == "API"
                else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
            ),
            "authorization_schema_version": AUTHORIZATION_SCHEMA,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }
        plan["plan_sha256"] = _sha256(plan)
        return plan

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right) or not left:
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_VECTOR_DIMENSION_INVALID")
        dot = math.fsum(float(a) * float(b) for a, b in zip(left, right))
        left_norm = math.sqrt(math.fsum(float(value) ** 2 for value in left))
        right_norm = math.sqrt(math.fsum(float(value) ** 2 for value in right))
        if left_norm <= 0.0 or right_norm <= 0.0:
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_ZERO_NORM_VECTOR")
        return dot / (left_norm * right_norm)

    @classmethod
    def _rank_ids(
        cls,
        document_ids: Sequence[str],
        document_vectors: Sequence[Sequence[float]],
        query_vector: Sequence[float],
    ) -> list[str]:
        if len(document_ids) != len(document_vectors):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_RANKING_INPUT_INVALID")
        scored = [
            (cls._cosine(vector, query_vector), index, str(document_id))
            for index, (document_id, vector) in enumerate(
                zip(document_ids, document_vectors)
            )
        ]
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [row[2] for row in scored]

    @staticmethod
    def _state_cost(state: Mapping[str, Any], profile_kind: str) -> float | None:
        if profile_kind == "LOCAL":
            return 0.0
        return (
            round(float(state["estimated_cost_cny"]), 9)
            if state.get("cost_estimate_complete") is True
            else None
        )

    def execute(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
        *,
        authorization: Mapping[str, Any] | None,
        structured_chat: Callable[..., Mapping[str, Any]],
        embedding_call: Callable[..., Mapping[str, Any]],
    ) -> dict[str, Any]:
        del structured_chat
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
        reason = self._authorization_reason(authorization, plan)
        if reason is not None:
            return _not_run(reason, requested_model=requested_model)
        run_id = "workflow-exam-" + uuid.uuid4().hex
        run_root = self._scratch_root / "workflow_exams" / run_id
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
        state: dict[str, Any] = {
            "request_attempts": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_network_calls": 0,
            "external_process_launches": 0,
            "local_metadata_calls": 0,
            "estimated_cost_cny": 0.0,
            "cost_estimate_complete": True,
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
        try:
            assets = self._load_assets()
            production = assets["production"]
            production_tasks = production["tasks"]
            tasks_by_corpus = assets["tasks_by_corpus"]
            production_rows: list[dict[str, str]] = []
            for corpus in production["corpora"]:
                corpus_id = str(corpus["corpus_id"])
                production_rows.extend(
                    {
                        "key": f"PROD:D:{row['unit_id']}",
                        "item_id": str(row["unit_id"]),
                        "text": str(row["text"]),
                        "corpus_id": corpus_id,
                    }
                    for row in corpus["documents"]
                )
                production_rows.extend(
                    {
                        "key": f"PROD:Q:{row['task_id']}",
                        "item_id": str(row["task_id"]),
                        "text": str(row["query"]),
                        "corpus_id": corpus_id,
                    }
                    for row in tasks_by_corpus[corpus_id]
                )
            adversarial_rows: list[dict[str, str]] = []
            adversarial_tasks_by_form: dict[str, dict[str, list[Mapping[str, Any]]]] = {}
            for form_id in ("A", "B"):
                form = assets["adversarial_forms"][form_id]
                grouped: dict[str, list[Mapping[str, Any]]] = {}
                for task in form["tasks"]:
                    grouped.setdefault(str(task["corpus_id"]), []).append(task)
                adversarial_tasks_by_form[form_id] = grouped
                for corpus in form["corpora"]:
                    corpus_id = str(corpus["corpus_id"])
                    adversarial_rows.extend(
                        {
                            "key": f"ADV:{form_id}:D:{row['unit_id']}",
                            "item_id": str(row["unit_id"]),
                            "text": str(row["text"]),
                            "corpus_id": corpus_id,
                            "form_id": form_id,
                        }
                        for row in corpus["documents"]
                    )
                    adversarial_rows.extend(
                        {
                            "key": f"ADV:{form_id}:Q:{row['task_id']}",
                            "item_id": str(row["task_id"]),
                            "text": str(row["query"]),
                            "corpus_id": corpus_id,
                            "form_id": form_id,
                        }
                        for row in grouped[corpus_id]
                    )
            repeat_rows: list[dict[str, str]] = []
            for corpus in production["corpora"]:
                corpus_id = str(corpus["corpus_id"])
                for row in corpus["documents"][:2]:
                    source_key = f"PROD:D:{row['unit_id']}"
                    repeat_rows.append(
                        {
                            "key": f"REPEAT:{source_key}",
                            "source_key": source_key,
                            "item_id": str(row["unit_id"]),
                            "text": str(row["text"]),
                            "corpus_id": corpus_id,
                        }
                    )
                for row in tasks_by_corpus[corpus_id][:2]:
                    source_key = f"PROD:Q:{row['task_id']}"
                    repeat_rows.append(
                        {
                            "key": f"REPEAT:{source_key}",
                            "source_key": source_key,
                            "item_id": str(row["task_id"]),
                            "text": str(row["query"]),
                            "corpus_id": corpus_id,
                        }
                    )
            if (
                len(production_rows) != self.PRODUCTION_INPUT_COUNT
                or len(adversarial_rows) != self.ADVERSARIAL_INPUT_COUNT
                or len(repeat_rows) != self.REPEATABILITY_INPUT_COUNT
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_INPUT_TOPOLOGY_INVALID")

            call_index = 0
            dimension: int | None = plan["expected_dimension"]
            observed_returned_model: str | None = None

            def execute_lane(
                lane_id: str,
                rows: Sequence[Mapping[str, str]],
            ) -> dict[str, list[float]]:
                nonlocal call_index, dimension, observed_returned_model
                collected: dict[str, list[float]] = {}
                batch_size = int(plan["maximum_inputs_per_model_request"])
                for start_index in range(0, len(rows), batch_size):
                    batch_rows = rows[start_index : start_index + batch_size]
                    inputs = [str(row["text"]) for row in batch_rows]
                    input_ids = [str(row["key"]) for row in batch_rows]
                    call_index += 1
                    behavior_sha256 = _sha256(
                        {
                            "model": requested_model,
                            "inputs": inputs,
                            "purpose": "workflow_model_exam",
                        }
                    )
                    claim = {
                        "schema_version": "WorkflowEmbeddingPreSendClaim-v2",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "call_id": f"call-{call_index:03d}",
                        "call_kind": "EMBEDDING_SUCCESSOR_BATCH",
                        "lane_id": lane_id,
                        "profile_kind": plan["profile_kind"],
                        "profile_ref": plan["profile_ref"],
                        "purpose": "workflow_model_exam",
                        "route": (
                            "EXISTING_SETTINGS_API_TRANSPORT"
                            if plan["profile_kind"] == "API"
                            else "OLLAMA_LOOPBACK"
                        ),
                        "requested_model": requested_model,
                        "model_digest": target.get("model_digest"),
                        "input_count": len(inputs),
                        "input_id_exact_set_sha256": _sha256(input_ids),
                        "input_exact_set_sha256": _sha256(inputs),
                        "behavior_sha256": behavior_sha256,
                        "plan_sha256": plan["plan_sha256"],
                        "provider_received_gold": False,
                    }
                    claim_receipt = _write_json_create_only(
                        run_root / "claims" / f"call_{call_index:03d}.json",
                        claim,
                    )
                    result = dict(
                        embedding_call(
                            profile_kind=str(target["kind"]),
                            service=target,
                            model=target,
                            inputs=inputs,
                            purpose="workflow_model_exam",
                            timeout_seconds=300,
                        )
                    )
                    for field in (
                        "provider_calls",
                        "external_model_calls",
                        "external_network_calls",
                        "external_process_launches",
                        "local_metadata_calls",
                    ):
                        state[field] += int(result.get(field) or 0)
                    state["request_attempts"] += int(
                        result.get("external_model_calls") or 0
                    )
                    receipt = result.get("execution_receipt")
                    if isinstance(receipt, Mapping):
                        estimate = receipt.get("estimated_cost_cny")
                        if isinstance(estimate, (int, float)) and not isinstance(estimate, bool):
                            state["estimated_cost_cny"] += float(estimate)
                        elif plan["profile_kind"] == "API":
                            state["cost_estimate_complete"] = False
                        usage = receipt.get("token_usage")
                        if isinstance(usage, Mapping):
                            for field in ("prompt_tokens", "completion_tokens"):
                                value = usage.get(field)
                                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                                    state["token_usage"][field] += value
                    if result.get("status") != "PASS":
                        raise ValueError(str(result.get("reason") or "EMBEDDING_CALL_FAILED"))
                    if int(result.get("external_model_calls") or 0) != 1:
                        raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_PHYSICAL_CALL_BINDING_INVALID")
                    returned_model = result.get("returned_model")
                    if returned_model != requested_model:
                        raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_RETURNED_MODEL_MISMATCH")
                    if observed_returned_model is None:
                        observed_returned_model = str(returned_model)
                    elif observed_returned_model != returned_model:
                        raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_RETURNED_MODEL_DRIFT")
                    if (
                        not isinstance(receipt, Mapping)
                        or receipt.get("behavior_sha256") != behavior_sha256
                    ):
                        raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_BEHAVIOR_RECEIPT_INVALID")
                    vectors = result.get("vectors")
                    if not isinstance(vectors, list):
                        raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_VECTORS_MISSING")
                    observed_dimension = assets["legacy_scorer"].validate_vectors(
                        vectors,
                        len(inputs),
                        expected_dim=dimension,
                    )
                    if dimension is None:
                        dimension = observed_dimension
                    elif dimension != observed_dimension:
                        raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_DIMENSION_DRIFT")
                    for input_id, vector in zip(input_ids, vectors):
                        if input_id in collected:
                            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_INPUT_ID_DUPLICATE")
                        collected[input_id] = [float(value) for value in vector]
                    _write_json_create_only(
                        run_root / "receipts" / f"call_{call_index:03d}.json",
                        {
                            "schema_version": "WorkflowEmbeddingBatchReceipt-v3",
                            "call_id": claim["call_id"],
                            "lane_id": lane_id,
                            "input_count": len(inputs),
                            "input_id_exact_set_sha256": claim["input_id_exact_set_sha256"],
                            "response_vector_sha256": _sha256(vectors),
                            "dimension": dimension,
                            "returned_model": returned_model,
                            "physical_model_calls": 1,
                            "pre_send_claim_sha256": claim_receipt["sha256"],
                            "provider_received_gold": False,
                            "execution_receipt": deepcopy(dict(receipt)),
                        },
                    )
                return collected

            production_vectors = execute_lane("PRODUCTION_PRIMARY", production_rows)
            adversarial_vectors = execute_lane("ADVERSARIAL_DIAGNOSTIC", adversarial_rows)
            repeat_vectors = execute_lane("REPEATABILITY", repeat_rows)
            if (
                call_index != int(plan["maximum_model_calls"])
                or state["request_attempts"] != int(plan["maximum_model_calls"])
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_CALL_COUNT_MISMATCH")

            production_rankings: dict[str, list[str]] = {}
            for task in production_tasks:
                corpus = assets["corpus_by_id"][str(task["corpus_id"])]
                document_ids = [str(row["unit_id"]) for row in corpus["documents"]]
                document_vectors = [
                    production_vectors[f"PROD:D:{unit_id}"]
                    for unit_id in document_ids
                ]
                production_rankings[str(task["task_id"])] = self._rank_ids(
                    document_ids,
                    document_vectors,
                    production_vectors[f"PROD:Q:{task['task_id']}"],
                )
            production_score = self._load_successor_scorer().score_track(
                assets["gold"], production_rankings
            )

            adversarial_scores: dict[str, dict[str, Any]] = {}
            adversarial_rankings: dict[str, list[str]] = {}
            for form_id in ("A", "B"):
                form = assets["adversarial_forms"][form_id]
                grouped = adversarial_tasks_by_form[form_id]
                form_rankings: dict[str, list[str]] = {}
                for corpus in form["corpora"]:
                    corpus_id = str(corpus["corpus_id"])
                    document_ids = [str(row["unit_id"]) for row in corpus["documents"]]
                    document_vectors = [
                        adversarial_vectors[f"ADV:{form_id}:D:{unit_id}"]
                        for unit_id in document_ids
                    ]
                    for task in grouped[corpus_id]:
                        ranked = self._rank_ids(
                            document_ids,
                            document_vectors,
                            adversarial_vectors[
                                f"ADV:{form_id}:Q:{task['task_id']}"
                            ],
                        )
                        form_rankings[str(task["task_id"])] = ranked
                        adversarial_rankings[
                            f"{form_id}:{task['task_id']}"
                        ] = ranked
                adversarial_scores[form_id] = assets["legacy_scorer"].score_form(
                    form["gold"], form_rankings
                )
            adversarial_mean = statistics.fmean(
                float(adversarial_scores[form_id]["score"])
                for form_id in ("A", "B")
            )

            similarities = [
                self._cosine(
                    production_vectors[str(row["source_key"])],
                    repeat_vectors[str(row["key"])],
                )
                for row in repeat_rows
            ]
            repeatability = {
                "schema_version": "EmbeddingRepeatabilityScore-v1",
                "selection_revision": self.REPEATABILITY_SELECTION_REVISION,
                "sample_count": len(similarities),
                "mean_vector_cosine": round(statistics.fmean(similarities), 12),
                "minimum_vector_cosine": round(min(similarities), 12),
                "below_0_999999_count": sum(value < 0.999999 for value in similarities),
                "qualification_threshold_defined": False,
            }
            role_adequacy = assess_embedding_role_adequacy(
                production_score,
                repeatability,
            )
            cap = plan.get("budget_cap_cny")
            estimated_cost = self._state_cost(state, str(plan["profile_kind"]))
            if cap is not None and (
                estimated_cost is None or estimated_cost > float(cap)
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_COST_CAP_NOT_VERIFIABLE_OR_EXCEEDED")

            _write_json_create_only(
                run_root / "rankings" / "production_rankings.json",
                {
                    "schema_version": "EmbeddingProductionRankings-v1",
                    "rankings": production_rankings,
                    "provider_received_gold": False,
                },
            )
            _write_json_create_only(
                run_root / "rankings" / "adversarial_rankings.json",
                {
                    "schema_version": "EmbeddingAdversarialRankings-v1",
                    "rankings": adversarial_rankings,
                    "provider_received_gold": False,
                },
            )
            _write_json_create_only(
                run_root / "scores" / "production_score.local.json",
                production_score,
            )
            _write_json_create_only(
                run_root / "scores" / "adversarial_score.local.json",
                adversarial_scores,
            )
            _write_json_create_only(
                run_root / "scores" / "repeatability.json",
                repeatability,
            )

            production_score_exact = float(
                production_score["production_recall_score"]
            )
            embedding_diagnostics = {
                "schema_version": "QualityNewEmbeddingLiveDiagnostic-v2",
                "score_views": {
                    "production_primary": {
                        "kind": "PRODUCTION_RECALL_AT_15",
                        "score_exact": production_score_exact,
                        "critical_evidence_recall_at_15": production_score[
                            "critical_evidence_recall_at_15"
                        ],
                        "facet_coverage_at_15": production_score[
                            "facet_coverage_at_15"
                        ],
                        "source_scope_integrity_at_15": production_score[
                            "source_scope_integrity_at_15"
                        ],
                        "clean_control_recall_at_10": production_score[
                            "clean_control_recall_at_10"
                        ],
                        "qualification_eligible": False,
                        "quality_verdict": role_adequacy["quality_verdict"],
                    },
                    "adversarial_diagnostic": {
                        "kind": "LEGACY_NONLINEAR_DEDUCTION_SAMPLE",
                        "score_exact": round(adversarial_mean, 6),
                        "form_scores": {
                            form_id: adversarial_scores[form_id]["score"]
                            for form_id in ("A", "B")
                        },
                        "independent_from_production_primary": True,
                        "qualification_eligible": False,
                    },
                    "repeatability": repeatability,
                },
                "workload_shape": deepcopy(assets["workload_shape"]),
                "selection_contract": {
                    "single_aggregate_is_sufficient_for_selection": False,
                    "production_and_adversarial_scores_must_not_be_combined": True,
                    "repeatability_and_operational_profile_required": True,
                    "meaningful_pairwise_separation_must_be_demonstrated": True,
                    "pack_native_quality_threshold_defined": False,
                    "quality_threshold_defined": True,
                    "role_adequacy_policy_revision": (
                        EMBEDDING_ROLE_ADEQUACY_POLICY["revision"]
                    ),
                    "formal_qualification_eligible": False,
                    "model_failure_established": False,
                },
                "role_adequacy_assessment": role_adequacy,
                "execution_provenance": {
                    "physical_model_call_target": plan["maximum_model_calls"],
                    "maximum_inputs_per_model_request": plan[
                        "maximum_inputs_per_model_request"
                    ],
                    "production_input_count": self.PRODUCTION_INPUT_COUNT,
                    "adversarial_input_count": self.ADVERSARIAL_INPUT_COUNT,
                    "repeatability_input_count": self.REPEATABILITY_INPUT_COUNT,
                    "historical_quality_call_count_relabelled": False,
                },
                "cross_category_comparison_forbidden": True,
                "global_ranking_forbidden": True,
            }
            embedding_diagnostics["diagnostic_sha256"] = _sha256(
                embedding_diagnostics
            )
            public_result = {
                "schema_version": "WorkflowModelExamRunResult-v1",
                "status": "PASS",
                "execution_outcome": "COMPLETED",
                "scorability": "SCOREABLE",
                "raw_quality_candidate_verdict": role_adequacy[
                    "quality_verdict"
                ],
                "reason": "Quality_NEW_EMBEDDING_SUCCESSOR_TECHNICALLY_COMPLETED",
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "requested_model": requested_model,
                "returned_model": observed_returned_model,
                "score": int(round(production_score_exact)),
                "score_exact": round(production_score_exact, 6),
                "score_semantics": (
                    "FIRST_STAGE_HIGH_RECALL_AT_15_ROLE_ADEQUACY"
                ),
                "production_recall_score_exact": round(production_score_exact, 6),
                "adversarial_diagnostic_score_exact": round(adversarial_mean, 6),
                "quality_verdict": role_adequacy["quality_verdict"],
                "acceptance_verdict": "NOT_ASSESSED",
                "quality_threshold_defined": True,
                "score_is_descriptive_within_category": False,
                "role_adequacy_assessment": role_adequacy,
                "model_fail_verdict": "NOT_ESTABLISHED",
                "model_fail_established": False,
                "exam_category_id": plan["exam_category_id"],
                "role_id": plan["role_id"],
                "sample_revision": plan["sample_revision"],
                "comparison_cohort_id": plan["comparison_cohort_id"],
                "comparison_binding_sha256": plan["comparison_binding_sha256"],
                "adversarial_comparison_cohort_id": plan[
                    "adversarial_comparison_cohort_id"
                ],
                "horizontal_comparison_eligible": True,
                "horizontal_comparison_allowed_only_with_same_cohort": True,
                "cross_category_comparison_forbidden": True,
                "global_ranking_forbidden": True,
                "failed_exam_item_count": int(production_score["critical_miss_count"]),
                "critical_miss_count": int(production_score["critical_miss_count"]),
                "failed_exam_item_count_semantics": (
                    "PRODUCTION_DIAGNOSTIC_CRITICAL_MISS_NOT_ADMISSION_FAIL"
                ),
                "dimension": dimension,
                "request_attempts": state["request_attempts"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "external_process_launches": state["external_process_launches"],
                "local_metadata_calls": state["local_metadata_calls"],
                "token_usage": state["token_usage"],
                "estimated_cost_cny": estimated_cost,
                "actual_cost": None,
                "cost_semantics": plan["cost_semantics"],
                "reference_regression_only": True,
                "blind_holdout_eligible": False,
                "qualification_eligible": False,
                "provider_received_answer_key": False,
                "provider_received_gold": False,
                "embedding_diagnostics": embedding_diagnostics,
            }
            evidence = _write_json_create_only(
                run_root / "exam_result.json", public_result
            )
            return {
                **public_result,
                "schema_version": RESULT_SCHEMA,
                "kind": "WORKFLOW_NODE",
                "duration_ms": max(0, int((monotonic() - started) * 1000)),
                "exam_mode": plan["mode"],
                "exam_run_id": run_id,
                "exam_run_root": str(run_root.resolve()),
                "exam_evidence_sha256": evidence["sha256"],
            }
        except Exception as error:
            reason = str(error) or type(error).__name__
            if len(reason) > 200:
                reason = type(error).__name__
            estimated_cost = self._state_cost(state, str(plan["profile_kind"]))
            failure = {
                "schema_version": "WorkflowModelExamRunFailure-v1",
                "status": "NOT_ASSESSED",
                "execution_outcome": classify_execution_outcome(
                    {"status": "ERROR", "reason": reason}
                ).value,
                "scorability": "NOT_ASSESSED",
                "quality_verdict": "NOT_ASSESSED",
                "reason": reason,
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "request_attempts": state["request_attempts"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "external_process_launches": state["external_process_launches"],
                "local_metadata_calls": state["local_metadata_calls"],
                "token_usage": state["token_usage"],
                "estimated_cost_cny": estimated_cost,
                "score": None,
                "provider_received_gold": False,
            }
            evidence = _write_json_create_only(
                run_root / "exam_failure.json", failure
            )
            result = _not_run(reason, requested_model=requested_model)
            result.update(
                duration_ms=max(0, int((monotonic() - started) * 1000)),
                execution_outcome=failure["execution_outcome"],
                scorability="NOT_ASSESSED",
                quality_verdict="NOT_ASSESSED",
                exam_run_id=run_id,
                exam_run_root=str(run_root.resolve()),
                exam_evidence_sha256=evidence["sha256"],
                cost_semantics=plan["cost_semantics"],
                request_attempts=state["request_attempts"],
                provider_calls=state["provider_calls"],
                external_model_calls=state["external_model_calls"],
                external_network_calls=state["external_network_calls"],
                external_process_launches=state["external_process_launches"],
                local_metadata_calls=state["local_metadata_calls"],
                token_usage=state["token_usage"],
                estimated_cost_cny=estimated_cost,
                provider_received_gold=False,
            )
            return result


__all__ = [
    "QualityAnalysisExamExecutor",
    "QualityEmbeddingExamExecutor",
    "QualityNewEmbeddingLegacyExamExecutor",
    "QualityNewEmbeddingExamExecutor",
    "QualityCardDistillerDirectExamExecutor",
    "QualityNewCardDistillerLanguageBankExamExecutor",
    "QualityCardDistillerExamExecutor",
    "QualityCardReviewerExamExecutor",
    "QualityCardReviewerExamSuccessorExecutor",
    "build_card_reviewer_successor_sample",
    "card_reviewer_comparison_metadata",
    "execute_card_reviewer_successor_form",
    "mechanically_normalize_successor_candidate_pointers",
    "core_card_reviewer_workload_profile",
    "core_primary_workload_profile",
    "score_card_reviewer_role_projection",
    "score_card_reviewer_successor_form",
]
