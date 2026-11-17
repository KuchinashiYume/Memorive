"""Card distiller exam policy: passing score, call and repair bounds, revisions, and the helpers the
executors share (residual materiality, candidate output metrics and size limit, the call bound).
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping
from memorive.exams.common import (
    Core_PRIMARY_CARD_OUTPUT_BYTE_LIMIT,
    Core_PRIMARY_CARD_OUTPUT_CHAR_LIMIT,
    _serialized_request,
)


PASSING_SCORE = 85.0


MAX_CALLS_PER_STAGE = 17


MAX_STRUCTURAL_REPAIR_ROUNDS = 2


CARD_DISTILLER_EXAM_CATEGORY_ID = "Quality_CARD_DISTILLER_REFERENCE_REGRESSION"


CARD_DISTILLER_SUCCESSOR_SAMPLE_REVISION = (
    "Desktop_CARD_DISTILLER_Core_X2_ROTATING_7_OF_8_PACKS_V1"
)


CARD_DISTILLER_REPAIR_ISSUE_GUIDANCE_REVISION = (
    "Desktop_CARD_DISTILLER_GOLD_FREE_ISSUE_GUIDANCE_V1"
)


CARD_DISTILLER_RESIDUAL_MATERIALITY_REVISION = (
    "Desktop_CARD_DISTILLER_D3_IMPLICIT_COMPARATOR_CALIBRATION_V1"
)


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


def _distiller_call_bound(forms: Mapping[str, Any], profile_kind: str) -> int:
    fact_size = 12 if profile_kind == 'LOCAL' else 36
    shards = sum(math.ceil(len(form['cases']) / fact_size)
                 + math.ceil(len(form.get('packs', [])) / 4) for form in forms.values())
    final_repair = (sum(math.ceil(len(form['cases']) / 12) for form in forms.values())
                    if profile_kind == 'LOCAL' else 1)
    return shards * (1 + MAX_STRUCTURAL_REPAIR_ROUNDS) + final_repair
