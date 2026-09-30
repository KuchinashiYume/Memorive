"""Provider-blind directed-repair primitives for the Card Distiller exam.

The ordinary dual-form exam remains immutable.  This module builds one
all-and-only repair round from the major/critical exam wrong items found after
both Forms have been scored, validates the returned exact set, merges only the
authorised rows, and derives the final score from the repaired answer with
frozen initial-burden and residual-error penalties.

Gold is required only by the local scorer.  It is never accepted by the repair
package builder and therefore cannot enter a provider prompt through this API.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator

from .card_distiller_exam_protocol import score_response


REPAIR_PACKAGE_SCHEMA_VERSION = (
    "model_evaluation-card-distiller-exam-directed-repair-package-v4"
)
REPAIR_CONTRACT_SCHEMA_VERSION = (
    "model_evaluation-card-distiller-exam-directed-repair-contract-v4"
)
REPAIR_RESULT_SCHEMA_VERSION = (
    "model_evaluation-card-distiller-exam-directed-repair-result-v4"
)
FORM_IDS = ("A", "B")
REPAIRABLE_SEVERITIES = ("major", "critical")
REPAIR_SCOPE_PENALTY_KNOTS = (
    (0.00, 0.00),
    (0.05, 0.50),
    (0.10, 0.80),
    (0.25, 1.50),
    (0.50, 2.00),
    (1.00, 4.00),
)
REPAIR_PENALTY_CONTRACT = {
    "schema_version": "model_evaluation-card-distiller-repair-penalty-v3",
    "volume_knots": [list(item) for item in REPAIR_SCOPE_PENALTY_KNOTS],
    "artifact_category_points": 0.5,
    "severity_category_points": {"major": 0.5, "critical": 1.0},
    "distinct_issue_category_points": 0.1,
    "distinct_issue_category_cap": 1.5,
    "successful_repair_ceiling": 89.99,
    "residual_wrong_item_points": {"major": 0.1, "critical": 0.5},
    "failed_exam_item_definition": (
        "POST_REPAIR_CASE_WITH_MAJOR_OR_CRITICAL_REASON"
    ),
    "failed_exam_item_unit": "CASE",
    "failed_exam_item_is_formal_core_disposition_candidate": True,
    "formal_core_disposition_candidate_policy": (
        "DOWNGRADE_OR_DELETE_CANDIDATE"
    ),
    "failed_exam_item_count_is_first_class_parameter": True,
    "failed_exam_item_count_changes_v2_numeric_weights": False,
    "continuous_score_floor": 0.0,
    "passing_score_minimum": 85.0,
    "structural_contract_failure_final_score": 0.0,
}


class DirectedRepairError(RuntimeError):
    """Raised when a directed-repair boundary cannot be closed."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest().upper()


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DirectedRepairError(f"MAPPING_REQUIRED:{label}")
    return value


def _verify_self_hash(value: Mapping[str, Any], field: str) -> None:
    observed = value.get(field)
    payload = {key: deepcopy(item) for key, item in value.items() if key != field}
    if not isinstance(observed, str) or observed != sha256(payload):
        raise DirectedRepairError(f"SELF_HASH_MISMATCH:{field}")


def _interpolate(value: float, knots: Iterable[tuple[float, float]]) -> float:
    points = list(knots)
    if value <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if value <= x1:
            ratio = (value - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    return points[-1][1]


def _wrong_details(score: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    details = score.get("details")
    if not isinstance(details, list):
        raise DirectedRepairError("SCORE_DETAILS_REQUIRED")
    result = []
    for detail in details:
        if not isinstance(detail, Mapping):
            raise DirectedRepairError("SCORE_DETAIL_MAPPING_REQUIRED")
        critical = [
            item
            for item in (detail.get("critical_reasons") or [])
            if isinstance(item, str) and item
        ]
        major = [
            item
            for item in (detail.get("major_reasons") or [])
            if isinstance(item, str) and item
        ]
        if critical or major:
            result.append(detail)
    return result


def _form_case_order(form: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[str]]:
    cases = form.get("cases")
    if not isinstance(cases, list) or not cases:
        raise DirectedRepairError("FORM_CASES_REQUIRED")
    case_ids = [
        item.get("case_id") if isinstance(item, Mapping) else None
        for item in cases
    ]
    if (
        any(not isinstance(item, str) or not item for item in case_ids)
        or len(case_ids) != len(set(case_ids))
    ):
        raise DirectedRepairError("FORM_CASE_IDS_INVALID")
    return cases, list(case_ids)


def _response_rows(
    response: Mapping[str, Any],
    artifact: str,
    id_field: str,
) -> tuple[list[Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    rows = response.get(artifact)
    if not isinstance(rows, list):
        raise DirectedRepairError(f"RESPONSE_ARTIFACT_ARRAY_REQUIRED:{artifact}")
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise DirectedRepairError(f"RESPONSE_ROW_MAPPING_REQUIRED:{artifact}")
        item_id = row.get(id_field)
        if not isinstance(item_id, str) or item_id in by_id:
            raise DirectedRepairError(f"RESPONSE_ROW_ID_INVALID:{artifact}:{item_id}")
        by_id[item_id] = row
    return rows, by_id


def _failed_fields(detail: Mapping[str, Any]) -> list[str]:
    checks = detail.get("checks")
    if not isinstance(checks, Mapping):
        return []
    return sorted(
        key
        for key, value in checks.items()
        if isinstance(key, str) and value is False
    )


def _repair_schema(
    schema: Mapping[str, Any],
    exact_sets: Mapping[str, list[str]],
) -> dict[str, Any]:
    root: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {},
    }
    source_fact = (
        _require_mapping(schema.get("properties"), "schema.properties")
        .get("fact_map")
    )
    source_fact = _require_mapping(source_fact, "schema.fact_map")
    item_schema = deepcopy(
        dict(_require_mapping(source_fact.get("items"), "schema.fact_map.items"))
    )
    for form_id in FORM_IDS:
        case_ids = exact_sets.get(form_id, [])
        if not case_ids:
            continue
        form_key = f"form_{form_id}"
        row_schema = deepcopy(item_schema)
        row_properties = _require_mapping(
            row_schema.get("properties"),
            f"{form_key}.fact_map.items.properties",
        )
        row_properties["case_id"] = {
            "type": "string",
            "enum": list(case_ids),
        }
        root["required"].append(form_key)
        root["properties"][form_key] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["fact_map"],
            "properties": {
                "fact_map": {
                    "type": "array",
                    "minItems": len(case_ids),
                    "maxItems": len(case_ids),
                    "items": row_schema,
                }
            },
        }
    if not root["required"]:
        raise DirectedRepairError("NO_EXAM_WRONG_ITEMS")
    return root


def build_exam_directed_repair_package(
    *,
    forms: Mapping[str, Mapping[str, Any]],
    original_responses: Mapping[str, Mapping[str, Any]],
    original_scores: Mapping[str, Mapping[str, Any]],
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one all-and-only repair round for all major/critical wrong items."""

    schema = _require_mapping(schema, "schema")
    exact_sets: dict[str, list[str]] = {}
    repair_targets: dict[str, list[dict[str, Any]]] = {}
    source_form_hashes: dict[str, str] = {}
    source_response_hashes: dict[str, str] = {}
    source_score_hashes: dict[str, str] = {}
    issue_categories: set[str] = set()
    severity_categories: set[str] = set()
    total_wrong_items = 0

    for form_id in FORM_IDS:
        form = _require_mapping(forms.get(form_id), f"forms.{form_id}")
        response = _require_mapping(
            original_responses.get(form_id),
            f"original_responses.{form_id}",
        )
        score = _require_mapping(
            original_scores.get(form_id),
            f"original_scores.{form_id}",
        )
        cases, case_order = _form_case_order(form)
        case_by_id = {item["case_id"]: item for item in cases}
        _, response_by_id = _response_rows(response, "fact_map", "case_id")
        detail_by_id = {
            detail.get("case_id"): detail for detail in _wrong_details(score)
        }
        unknown = sorted(set(detail_by_id) - set(case_order))
        if unknown:
            raise DirectedRepairError(f"SCORE_WRONG_ITEM_UNKNOWN_CASE:{unknown}")
        exact_set = [
            case_id for case_id in case_order if case_id in detail_by_id
        ]
        exact_sets[form_id] = exact_set
        total_wrong_items += len(exact_set)
        targets = []
        for case_id in exact_set:
            detail = detail_by_id[case_id]
            critical_reasons = sorted(
                {
                    item
                    for item in (detail.get("critical_reasons") or [])
                    if isinstance(item, str) and item
                }
            )
            major_reasons = sorted(
                {
                    item
                    for item in (detail.get("major_reasons") or [])
                    if isinstance(item, str) and item
                }
            )
            severity = "critical" if critical_reasons else "major"
            severity_categories.add(severity)
            reasons = critical_reasons + major_reasons
            issue_categories.update(reasons)
            current_row = response_by_id.get(case_id)
            if current_row is None:
                raise DirectedRepairError(f"WRONG_ITEM_CURRENT_ROW_MISSING:{case_id}")
            targets.append(
                {
                    "case_id": case_id,
                    "issue_codes": reasons,
                    "failed_output_fields": _failed_fields(detail),
                    "source_case": deepcopy(dict(case_by_id[case_id])),
                    "source_case_sha256": sha256(case_by_id[case_id]),
                    "current_row": deepcopy(dict(current_row)),
                    "current_row_sha256": sha256(current_row),
                    "allowed_actions": ["REPLACE_FACT_ROW"],
                    "requires_full_row": True,
                }
            )
        repair_targets[form_id] = targets
        source_form_hashes[form_id] = sha256(form)
        source_response_hashes[form_id] = sha256(response)
        source_score_hashes[form_id] = sha256(score)

    if total_wrong_items == 0:
        raise DirectedRepairError("NO_EXAM_WRONG_ITEMS")
    repair_schema = _repair_schema(schema, exact_sets)
    contract: dict[str, Any] = {
        "schema_version": REPAIR_CONTRACT_SCHEMA_VERSION,
        "repair_round_limit": 1,
        "whole_exam_regeneration_forbidden": True,
        "wrong_item_definition": (
            "CASE_WITH_MAJOR_OR_CRITICAL_REASON_IN_FROZEN_INITIAL_SCORE"
        ),
        "wrong_item_exact_set": exact_sets,
        "wrong_item_count": total_wrong_items,
        "repair_artifact_categories": ["fact_map"],
        "severity_categories": sorted(severity_categories),
        "issue_categories": sorted(issue_categories),
        "source_form_sha256": source_form_hashes,
        "source_response_sha256": source_response_hashes,
        "source_score_sha256": source_score_hashes,
        "source_schema_sha256": sha256(schema),
        "repair_targets_sha256": sha256(repair_targets),
        "repair_schema_sha256": sha256(repair_schema),
        "requires_exact_case_id_order": True,
        "requires_full_post_merge_rescore": True,
        "remaining_exam_wrong_items_are_continuously_scored": True,
        "failed_exam_item_definition": (
            "POST_REPAIR_CASE_WITH_MAJOR_OR_CRITICAL_REASON"
        ),
        "failed_exam_item_count_is_first_class_parameter": True,
        "failed_exam_items_are_formal_core_disposition_candidates": True,
        "formal_core_disposition_candidate_policy": (
            "DOWNGRADE_OR_DELETE_CANDIDATE"
        ),
        "only_structural_contract_failure_disqualifies": True,
        "subject_receives_answer_key": False,
        "provider_receives_answer_key": False,
        "penalty_contract": deepcopy(REPAIR_PENALTY_CONTRACT),
    }
    contract["contract_sha256"] = sha256(contract)
    prompt_payload = {
        "instruction": (
            "Complete the single directed-repair round for this synthetic Card "
            "Distiller examination. Replace all and only the listed fact rows. "
            "Return every listed case exactly once in the declared form and case "
            "order, and return no other case. Use each source_case as the only "
            "source of truth, use current_row as the starting point, fix every "
            "machine issue_code and failed_output_field, and recheck the complete "
            "row. Do not regenerate either whole Form."
        ),
        "wrong_item_exact_set": exact_sets,
        "repair_targets": repair_targets,
        "output_schema": repair_schema,
        "contract_sha256": contract["contract_sha256"],
    }
    prompt = json.dumps(
        prompt_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    lowered = prompt.lower()
    for forbidden in (
        '"expected"',
        '"gold"',
        '"difficulty"',
        '"family"',
        "score_cap",
        "penalty_contract",
    ):
        if forbidden in lowered:
            raise DirectedRepairError(
                f"REPAIR_PROMPT_FORBIDDEN_SCORING_FIELD:{forbidden}"
            )
    package = {
        "schema_version": REPAIR_PACKAGE_SCHEMA_VERSION,
        "status": "READY",
        "contract": contract,
        "repair_schema": repair_schema,
        "repair_targets": repair_targets,
        "prompt": prompt,
        "gold_sent_to_subject": False,
        "gold_sent_to_provider": False,
    }
    package["package_sha256"] = sha256(package)
    return package


def merge_exam_directed_repair(
    *,
    forms: Mapping[str, Mapping[str, Any]],
    original_responses: Mapping[str, Mapping[str, Any]],
    repair_response: Mapping[str, Any],
    schema: Mapping[str, Any],
    repair_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one exact repair response and merge only authorised fact rows."""

    repair_contract = _require_mapping(repair_contract, "repair_contract")
    _verify_self_hash(repair_contract, "contract_sha256")
    if repair_contract.get("schema_version") != REPAIR_CONTRACT_SCHEMA_VERSION:
        raise DirectedRepairError("REPAIR_CONTRACT_SCHEMA_VERSION_MISMATCH")
    if repair_contract.get("source_schema_sha256") != sha256(schema):
        raise DirectedRepairError("REPAIR_CONTRACT_SCHEMA_HASH_MISMATCH")
    exact_sets = _require_mapping(
        repair_contract.get("wrong_item_exact_set"),
        "repair_contract.wrong_item_exact_set",
    )
    repair_schema = _repair_schema(schema, exact_sets)
    if repair_contract.get("repair_schema_sha256") != sha256(repair_schema):
        raise DirectedRepairError("REPAIR_SCHEMA_HASH_MISMATCH")
    errors = sorted(
        Draft202012Validator(repair_schema).iter_errors(repair_response),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    if errors:
        raise DirectedRepairError(
            f"REPAIR_RESPONSE_SCHEMA_INVALID:{len(errors)}:{errors[0].message}"
        )

    merged_responses: dict[str, dict[str, Any]] = {}
    change_receipts: dict[str, Any] = {}
    for form_id in FORM_IDS:
        form = _require_mapping(forms.get(form_id), f"forms.{form_id}")
        original = _require_mapping(
            original_responses.get(form_id),
            f"original_responses.{form_id}",
        )
        if repair_contract["source_form_sha256"].get(form_id) != sha256(form):
            raise DirectedRepairError(f"REPAIR_FORM_HASH_MISMATCH:{form_id}")
        if (
            repair_contract["source_response_sha256"].get(form_id)
            != sha256(original)
        ):
            raise DirectedRepairError(f"REPAIR_RESPONSE_HASH_MISMATCH:{form_id}")
        _, case_order = _form_case_order(form)
        original_rows, original_by_id = _response_rows(
            original,
            "fact_map",
            "case_id",
        )
        target_ids = list(exact_sets.get(form_id, []))
        if target_ids:
            form_key = f"form_{form_id}"
            repair_rows = repair_response[form_key]["fact_map"]
            observed_ids = [
                row.get("case_id") if isinstance(row, Mapping) else None
                for row in repair_rows
            ]
            if observed_ids != target_ids or len(observed_ids) != len(
                set(observed_ids)
            ):
                raise DirectedRepairError(
                    f"REPAIR_CASE_ID_EXACT_SET_MISMATCH:{form_id}"
                )
            replacements = {row["case_id"]: row for row in repair_rows}
        else:
            replacements = {}
        merged_fact = []
        changed = []
        retained = []
        for case_id in case_order:
            original_row = original_by_id.get(case_id)
            if original_row is None:
                raise DirectedRepairError(
                    f"ORIGINAL_CASE_ROW_MISSING:{form_id}:{case_id}"
                )
            if case_id in replacements:
                merged_fact.append(deepcopy(replacements[case_id]))
                changed.append(
                    {
                        "case_id": case_id,
                        "before_sha256": sha256(original_row),
                        "after_sha256": sha256(replacements[case_id]),
                    }
                )
            else:
                merged_fact.append(deepcopy(dict(original_row)))
                retained.append(
                    {
                        "case_id": case_id,
                        "sha256": sha256(original_row),
                    }
                )
        merged = deepcopy(dict(original))
        merged["fact_map"] = merged_fact
        for row in retained:
            merged_row = next(
                item
                for item in merged_fact
                if item.get("case_id") == row["case_id"]
            )
            if sha256(merged_row) != row["sha256"]:
                raise DirectedRepairError(
                    f"NON_TARGET_ROW_CHANGED:{form_id}:{row['case_id']}"
                )
        merged_responses[form_id] = merged
        change_receipts[form_id] = {
            "target_case_ids": target_ids,
            "changed_rows": changed,
            "retained_row_count": len(retained),
            "original_fact_map_sha256": sha256(original_rows),
            "merged_fact_map_sha256": sha256(merged_fact),
            "card_projection_unchanged": (
                sha256(original.get("card_projection"))
                == sha256(merged.get("card_projection"))
            ),
        }
    return {
        "schema_version": "model_evaluation-card-distiller-directed-repair-merge-v2",
        "status": "MERGED_EXACT_SET",
        "responses": merged_responses,
        "change_receipts": change_receipts,
        "repair_response_sha256": sha256(repair_response),
    }


def _form_penalty(
    *,
    form_case_count: int,
    wrong_case_count: int,
    issue_categories: set[str],
    severity_categories: set[str],
    artifact_categories: set[str],
) -> dict[str, Any]:
    ratio = wrong_case_count / form_case_count
    volume = _interpolate(ratio, REPAIR_SCOPE_PENALTY_KNOTS)
    artifacts = (
        len(artifact_categories)
        * float(REPAIR_PENALTY_CONTRACT["artifact_category_points"])
    )
    severities = sum(
        float(REPAIR_PENALTY_CONTRACT["severity_category_points"][item])
        for item in severity_categories
    )
    issue_category_penalty = min(
        float(REPAIR_PENALTY_CONTRACT["distinct_issue_category_cap"]),
        len(issue_categories)
        * float(
            REPAIR_PENALTY_CONTRACT["distinct_issue_category_points"]
        ),
    )
    total = volume + artifacts + severities + issue_category_penalty
    return {
        "wrong_item_count": wrong_case_count,
        "form_case_count": form_case_count,
        "wrong_item_ratio": round(ratio, 9),
        "wrong_item_volume": round(volume, 6),
        "artifact_categories": sorted(artifact_categories),
        "artifact_category_penalty": round(artifacts, 6),
        "severity_categories": sorted(severity_categories),
        "severity_category_penalty": round(severities, 6),
        "issue_categories": sorted(issue_categories),
        "issue_categories_penalty": round(issue_category_penalty, 6),
        "total": round(total, 6),
    }


def _residual_error_penalty(
    details: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    points = REPAIR_PENALTY_CONTRACT["residual_wrong_item_points"]
    severity_counts = {"major": 0, "critical": 0}
    issue_counts: dict[str, int] = {}
    case_ids: list[str] = []
    for detail in details:
        case_id = detail.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise DirectedRepairError("POST_REPAIR_CASE_ID_REQUIRED")
        critical = {
            item
            for item in (detail.get("critical_reasons") or [])
            if isinstance(item, str) and item
        }
        major = {
            item
            for item in (detail.get("major_reasons") or [])
            if isinstance(item, str) and item
        }
        severity = "critical" if critical else "major"
        severity_counts[severity] += 1
        case_ids.append(case_id)
        for reason in sorted(critical | major):
            issue_counts[reason] = issue_counts.get(reason, 0) + 1
    severity_points = {
        severity: round(
            severity_counts[severity] * float(points[severity]),
            6,
        )
        for severity in ("major", "critical")
    }
    total = round(sum(severity_points.values()), 6)
    return {
        "wrong_item_count": len(case_ids),
        "failed_exam_item_count": len(case_ids),
        "case_ids": sorted(case_ids),
        "severity_counts": severity_counts,
        "per_item_points": deepcopy(points),
        "severity_points": severity_points,
        "issue_counts": dict(sorted(issue_counts.items())),
        "total": total,
    }


def _score_band(score: float) -> str:
    if score < 60.0:
        return "UNSTABLE_OR_INSUFFICIENT"
    if score < 75.0:
        return "BARELY_USABLE_AND_UNSTABLE"
    if score < 85.0:
        return "USABLE_BELOW_Core_PRIMARY_TIER"
    return "Core_PRIMARY_TIER_IF_HARD_GATES_PASS"


def score_exam_after_directed_repair(
    *,
    forms: Mapping[str, Mapping[str, Any]],
    golds: Mapping[str, Mapping[str, Any]],
    original_scores: Mapping[str, Mapping[str, Any]],
    merged_responses: Mapping[str, Mapping[str, Any]],
    schema: Mapping[str, Any],
    repair_contract: Mapping[str, Any],
    schemas_by_form: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rescore repaired Forms and publish the only final score for the successor."""

    repair_contract = _require_mapping(repair_contract, "repair_contract")
    if schemas_by_form is not None and set(schemas_by_form) != set(FORM_IDS):
        raise DirectedRepairError("REPAIR_FORM_SCHEMA_EXACT_SET_MISMATCH")
    _verify_self_hash(repair_contract, "contract_sha256")
    exact_sets = _require_mapping(
        repair_contract.get("wrong_item_exact_set"),
        "repair_contract.wrong_item_exact_set",
    )
    post_scores: dict[str, dict[str, Any]] = {}
    remaining: dict[str, list[str]] = {}
    new_wrong_items: dict[str, list[str]] = {}
    post_wrong_details: dict[str, list[Mapping[str, Any]]] = {}
    form_results: dict[str, Any] = {}
    form_case_counts: dict[str, int] = {}
    original_wrong_sets: dict[str, set[str]] = {
        form_id: set(exact_sets.get(form_id, [])) for form_id in FORM_IDS
    }

    for form_id in FORM_IDS:
        form = _require_mapping(forms.get(form_id), f"forms.{form_id}")
        gold = _require_mapping(golds.get(form_id), f"golds.{form_id}")
        merged = _require_mapping(
            merged_responses.get(form_id),
            f"merged_responses.{form_id}",
        )
        original_score = _require_mapping(
            original_scores.get(form_id),
            f"original_scores.{form_id}",
        )
        if (
            repair_contract["source_score_sha256"].get(form_id)
            != sha256(original_score)
        ):
            raise DirectedRepairError(
                f"REPAIR_ORIGINAL_SCORE_HASH_MISMATCH:{form_id}"
            )
        score = score_response(
            dict(merged),
            dict(form),
            dict(gold),
            dict(schemas_by_form[form_id] if schemas_by_form is not None else schema),
            repair_count=0,
        )
        post_scores[form_id] = score
        post_wrong_details[form_id] = _wrong_details(score)
        wrong_after = {
            item["case_id"] for item in post_wrong_details[form_id]
        }
        remaining[form_id] = sorted(
            wrong_after & original_wrong_sets[form_id]
        )
        new_wrong_items[form_id] = sorted(
            wrong_after - original_wrong_sets[form_id]
        )

    remaining_count = sum(len(value) for value in remaining.values())
    new_wrong_count = sum(len(value) for value in new_wrong_items.values())
    final_form_scores: dict[str, float] = {}
    form_penalties: dict[str, dict[str, Any]] = {}
    residual_penalties: dict[str, dict[str, Any]] = {}
    for form_id in FORM_IDS:
        original_score = original_scores[form_id]
        details = {
            item["case_id"]: item
            for item in _wrong_details(original_score)
        }
        target_ids = list(exact_sets.get(form_id, []))
        issue_categories: set[str] = set()
        severity_categories: set[str] = set()
        for case_id in target_ids:
            detail = details[case_id]
            critical = {
                item
                for item in (detail.get("critical_reasons") or [])
                if isinstance(item, str) and item
            }
            major = {
                item
                for item in (detail.get("major_reasons") or [])
                if isinstance(item, str) and item
            }
            issue_categories.update(critical)
            issue_categories.update(major)
            severity_categories.add("critical" if critical else "major")
        form_case_count = len(_form_case_order(forms[form_id])[1])
        form_case_counts[form_id] = form_case_count
        penalty = _form_penalty(
            form_case_count=form_case_count,
            wrong_case_count=len(target_ids),
            issue_categories=issue_categories,
            severity_categories=severity_categories,
            artifact_categories={"fact_map"} if target_ids else set(),
        )
        form_penalties[form_id] = penalty
        residual_penalty = _residual_error_penalty(
            post_wrong_details[form_id]
        )
        residual_penalties[form_id] = residual_penalty
        base = float(post_scores[form_id]["form_score"])
        pre_penalty = (
            min(
                base,
                float(REPAIR_PENALTY_CONTRACT["successful_repair_ceiling"]),
            )
            if target_ids
            else base
        )
        combined_penalty = (
            float(penalty["total"])
            + float(residual_penalty["total"])
        )
        final = max(
            float(REPAIR_PENALTY_CONTRACT["continuous_score_floor"]),
            pre_penalty - combined_penalty,
        )
        final_form_scores[form_id] = round(final, 2)
        failed_exam_item_count = int(
            residual_penalty["failed_exam_item_count"]
        )
        failed_exam_item_rate = round(
            failed_exam_item_count / form_case_count,
            9,
        )
        form_results[form_id] = {
            "post_repair_score_before_penalty": round(base, 2),
            "successful_repair_ceiling_applied": (
                bool(target_ids)
                and base
                > float(
                    REPAIR_PENALTY_CONTRACT["successful_repair_ceiling"]
                )
            ),
            "initial_repair_burden_penalty": penalty,
            "residual_error_penalty": residual_penalty,
            "failed_exam_item_count": failed_exam_item_count,
            "failed_exam_item_rate": failed_exam_item_rate,
            "failed_exam_item_case_ids": deepcopy(
                residual_penalty["case_ids"]
            ),
            "failed_exam_item_severity_counts": deepcopy(
                residual_penalty["severity_counts"]
            ),
            "failed_exam_item_issue_counts": deepcopy(
                residual_penalty["issue_counts"]
            ),
            "would_degrade_or_delete_count": failed_exam_item_count,
            "production_disposition": {
                "policy": "DOWNGRADE_OR_DELETE_CANDIDATE",
                "actual_disposition_requires_formal_core_policy": True,
                "candidate_count": failed_exam_item_count,
                "candidate_rate_of_form_exam_items": failed_exam_item_rate,
                "penalty_points": residual_penalty["total"],
            },
            "combined_penalty": round(combined_penalty, 6),
            "final_form_score": round(final, 2),
            "quality_hard_gate": post_scores[form_id]["quality_hard_gate"],
            "critical_count": post_scores[form_id]["critical_count"],
            "major_count": post_scores[form_id]["major_count"],
        }
    final_mean = round(
        sum(final_form_scores.values()) / len(FORM_IDS),
        2,
    )
    pre_penalty_mean = round(
        sum(float(post_scores[item]["form_score"]) for item in FORM_IDS)
        / len(FORM_IDS),
        2,
    )
    initial_penalty_mean = round(
        sum(float(form_penalties[item]["total"]) for item in FORM_IDS)
        / len(FORM_IDS),
        6,
    )
    residual_penalty_mean = round(
        sum(float(residual_penalties[item]["total"]) for item in FORM_IDS)
        / len(FORM_IDS),
        6,
    )
    total_penalty = round(
        initial_penalty_mean + residual_penalty_mean,
        6,
    )
    residual_wrong_count = remaining_count + new_wrong_count
    failed_exam_item_count_by_form = {
        form_id: int(
            residual_penalties[form_id]["failed_exam_item_count"]
        )
        for form_id in FORM_IDS
    }
    failed_exam_item_count = sum(
        failed_exam_item_count_by_form.values()
    )
    if failed_exam_item_count != residual_wrong_count:
        raise DirectedRepairError(
            "FAILED_EXAM_ITEM_COUNT_INTERNAL_MISMATCH"
        )
    total_exam_case_count = sum(form_case_counts.values())
    failed_exam_item_rate_by_form = {
        form_id: round(
            failed_exam_item_count_by_form[form_id]
            / form_case_counts[form_id],
            9,
        )
        for form_id in FORM_IDS
    }
    failed_exam_item_rate = round(
        failed_exam_item_count / total_exam_case_count,
        9,
    )
    failed_exam_item_severity_counts = {"major": 0, "critical": 0}
    failed_exam_item_issue_counts: dict[str, int] = {}
    failed_exam_items_by_form: dict[str, list[str]] = {}
    for form_id in FORM_IDS:
        residual = residual_penalties[form_id]
        failed_exam_items_by_form[form_id] = deepcopy(
            residual["case_ids"]
        )
        for severity in ("major", "critical"):
            failed_exam_item_severity_counts[severity] += int(
                residual["severity_counts"][severity]
            )
        for issue, count in residual["issue_counts"].items():
            failed_exam_item_issue_counts[issue] = (
                failed_exam_item_issue_counts.get(issue, 0)
                + int(count)
            )
    if (
        sum(failed_exam_item_severity_counts.values())
        != failed_exam_item_count
    ):
        raise DirectedRepairError(
            "FAILED_EXAM_ITEM_SEVERITY_COUNT_INTERNAL_MISMATCH"
        )
    initial_wrong_item_count = int(repair_contract["wrong_item_count"])
    repaired_wrong_count = max(
        0,
        initial_wrong_item_count - remaining_count,
    )
    repair_completion_rate = round(
        repaired_wrong_count
        / initial_wrong_item_count,
        9,
    )
    failed_rate_of_initial_repair_targets = round(
        failed_exam_item_count / initial_wrong_item_count,
        9,
    )
    unrecovered_initial_target_rate = round(
        remaining_count / initial_wrong_item_count,
        9,
    )
    hard_gate = (
        "PASS"
        if all(
            post_scores[item]["quality_hard_gate"] == "PASS"
            for item in FORM_IDS
        )
        else "FAIL"
    )
    qualification = (
        "PASS"
        if (
            hard_gate == "PASS"
            and final_mean
            >= float(REPAIR_PENALTY_CONTRACT["passing_score_minimum"])
        )
        else "FAIL"
    )
    status = (
        "DIRECTED_REPAIR_SUCCEEDED"
        if residual_wrong_count == 0
        else "DIRECTED_REPAIR_SCORED_WITH_RESIDUAL_ERRORS"
    )
    return {
        "schema_version": REPAIR_RESULT_SCHEMA_VERSION,
        "status": status,
        "qualification_verdict": qualification,
        "repair_rounds": 1,
        "wrong_item_count": repair_contract["wrong_item_count"],
        "repaired_wrong_item_count": repaired_wrong_count,
        "repair_completion_rate": repair_completion_rate,
        "remaining_wrong_items": remaining,
        "remaining_initial_wrong_item_count": remaining_count,
        "new_wrong_items": new_wrong_items,
        "new_wrong_item_count": new_wrong_count,
        "remaining_wrong_item_count": residual_wrong_count,
        "total_exam_item_count": total_exam_case_count,
        "failed_exam_item_count_assessed": True,
        "failed_exam_item_count": failed_exam_item_count,
        "failed_exam_item_rate": failed_exam_item_rate,
        "failed_exam_item_count_by_form": (
            failed_exam_item_count_by_form
        ),
        "failed_exam_item_rate_by_form": failed_exam_item_rate_by_form,
        "failed_exam_items_by_form": failed_exam_items_by_form,
        "failed_exam_item_severity_counts": (
            failed_exam_item_severity_counts
        ),
        "failed_exam_item_issue_counts": dict(
            sorted(failed_exam_item_issue_counts.items())
        ),
        "failed_rate_of_initial_repair_targets": (
            failed_rate_of_initial_repair_targets
        ),
        "unrecovered_initial_target_rate": (
            unrecovered_initial_target_rate
        ),
        "would_degrade_or_delete_count": failed_exam_item_count,
        "would_degrade_or_delete_rate": failed_exam_item_rate,
        "production_disposition": {
            "policy": "DOWNGRADE_OR_DELETE_CANDIDATE",
            "actual_disposition_requires_formal_core_policy": True,
            "exam_item_unit": "CASE",
            "total_exam_item_count": total_exam_case_count,
            "initial_repair_target_count": initial_wrong_item_count,
            "recovered_initial_target_count": repaired_wrong_count,
            "remaining_initial_target_count": remaining_count,
            "new_failed_exam_item_count": new_wrong_count,
            "failed_after_repair_count": failed_exam_item_count,
            "failed_rate_of_all_exam_items": failed_exam_item_rate,
            "failed_rate_of_initial_repair_targets": (
                failed_rate_of_initial_repair_targets
            ),
            "failed_count_by_form": failed_exam_item_count_by_form,
            "failed_rate_by_form": failed_exam_item_rate_by_form,
            "failed_severity_counts": (
                failed_exam_item_severity_counts
            ),
            "failed_issue_counts": dict(
                sorted(failed_exam_item_issue_counts.items())
            ),
            "failed_item_penalty_total_mean": residual_penalty_mean,
            "scored_not_automatic_zero": True,
        },
        "post_repair_score_before_penalty": pre_penalty_mean,
        "repair_penalty": {
            "initial_burden_by_form": form_penalties,
            "residual_errors_by_form": residual_penalties,
            "wrong_item_volume": round(
                sum(
                    float(form_penalties[item]["wrong_item_volume"])
                    for item in FORM_IDS
                )
                / len(FORM_IDS),
                6,
            ),
            "issue_categories": round(
                sum(
                    float(
                        form_penalties[item]["issue_categories_penalty"]
                    )
                    for item in FORM_IDS
                )
                / len(FORM_IDS),
                6,
            ),
            "initial_burden_total_mean": initial_penalty_mean,
            "residual_error_total_mean": residual_penalty_mean,
            "total_mean": total_penalty,
        },
        "forms": form_results,
        "final_form_scores": final_form_scores,
        "final_score": final_mean,
        "final_min": round(min(final_form_scores.values()), 2),
        "final_gap": round(
            abs(final_form_scores["A"] - final_form_scores["B"]),
            2,
        ),
        "quality_hard_gate": hard_gate,
        "score_band": _score_band(final_mean),
        "provider_blind_formula": True,
        "scored_repair_result_is_final_score": True,
        "residual_exam_errors_do_not_force_zero": True,
        "failed_exam_item_count_is_first_class_parameter": True,
    }
