"""Deterministic audit, directed-repair, and scoring core for M14 card exams."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import statistics
from typing import Any

from jsonschema import Draft202012Validator

from .errors import M14CardReviewerExamError


AUDIT_SCHEMA_VERSION = "m14-card-reviewer-response-audit-v4"
REPAIR_PACKAGE_SCHEMA_VERSION = "m14-card-reviewer-directed-repair-package-v2"
REPAIR_MERGE_SCHEMA_VERSION = "m14-card-reviewer-directed-repair-merge-v2"
FORM_SCORE_SCHEMA_VERSION = "m14-card-reviewer-form-score-v4"
DUAL_FORM_RESULT_SCHEMA_VERSION = "m14-card-reviewer-dual-form-result-v4"
EXAM_REPAIR_PACKAGE_SCHEMA_VERSION = (
    "m14-card-reviewer-exam-directed-repair-package-v3"
)
EXAM_REPAIR_CONTRACT_SCHEMA_VERSION = (
    "m14-card-reviewer-exam-directed-repair-contract-v3"
)
EXAM_REPAIR_MERGE_SCHEMA_VERSION = (
    "m14-card-reviewer-exam-directed-repair-merge-v3"
)
EXAM_REPAIR_RESULT_SCHEMA_VERSION = (
    "m14-card-reviewer-exam-directed-repair-result-v2"
)
EXAM_REPAIR_SCOPE_PENALTY_KNOTS = (
    (0.00, 0.00),
    (0.05, 0.50),
    (0.10, 0.80),
    (0.25, 1.50),
    (0.50, 2.00),
    (1.00, 4.00),
)
EXAM_REPAIR_PENALTY_CONTRACT = {
    "schema_version": "m14-card-reviewer-exam-repair-penalty-v1",
    "volume_knots": [
        list(item) for item in EXAM_REPAIR_SCOPE_PENALTY_KNOTS
    ],
    "artifact_category_points": 0.5,
    "severity_category_points": {"major": 0.5, "critical": 1.0},
    "distinct_issue_category_points": 0.1,
    "distinct_issue_category_cap": 1.5,
    "successful_repair_ceiling": 89.99,
    "failed_repair_final_score": 0.0,
}
DEFECT_FAMILIES = (
    "numeric_direction",
    "population_mismatch",
    "boundary_omission",
    "causal_overclaim",
    "stale_lineage",
)


ReviewerExamError = M14CardReviewerExamError
POINTER_MISSING = object()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest().upper()


def verify_self_hash(value: Mapping[str, Any], field: str) -> None:
    observed = value.get(field)
    unsigned = dict(value)
    unsigned.pop(field, None)
    if not isinstance(observed, str) or observed != sha256(unsigned):
        raise ReviewerExamError(f"{field.upper()}_MISMATCH")


def require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewerExamError(f"{label.upper()}_MAPPING_REQUIRED")
    return value


def form_cases(form: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[str]]:
    cases = form.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ReviewerExamError("FORM_CASES_NONEMPTY_LIST_REQUIRED")
    ids: list[str] = []
    normalized: list[Mapping[str, Any]] = []
    for index, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise ReviewerExamError(f"FORM_CASE_OBJECT_REQUIRED:{index}")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ReviewerExamError(f"FORM_CASE_ID_REQUIRED:{index}")
        ids.append(case_id)
        normalized.append(case)
    if len(ids) != len(set(ids)):
        raise ReviewerExamError("FORM_CASE_IDS_MUST_BE_UNIQUE")
    return normalized, ids


def row_schema(schema: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        value = schema["properties"]["reviews"]["items"]
    except (KeyError, TypeError) as exc:
        raise ReviewerExamError("OUTPUT_ROW_SCHEMA_REQUIRED") from exc
    if not isinstance(value, Mapping):
        raise ReviewerExamError("OUTPUT_ROW_SCHEMA_MAPPING_REQUIRED")
    return value


def finding(
    code: str,
    *,
    case_id: str | None = None,
    field: str | None = None,
    observed: Any = None,
    expected: Any = None,
    action: str,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "code": code,
        "case_id": case_id,
        "field": field,
        "repair_action": action,
    }
    if observed is not None:
        value["observed"] = observed
    if expected is not None:
        value["expected"] = expected
    return value


def parse_aware_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def resolve_json_pointer(value: Any, pointer: Any) -> tuple[bool, Any]:
    if not isinstance(pointer, str):
        return False, POINTER_MISSING
    if pointer == "":
        return True, value
    if not pointer.startswith("/"):
        return False, POINTER_MISSING
    current = value
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if part not in current:
                return False, POINTER_MISSING
            current = current[part]
        elif isinstance(current, list):
            if not part.isdigit() or int(part) >= len(current):
                return False, POINTER_MISSING
            current = current[int(part)]
        else:
            return False, POINTER_MISSING
    return True, current


def nonexact_defect_target_pointer(
    *,
    case: Mapping[str, Any],
    row: Mapping[str, Any],
) -> tuple[bool, dict[str, Any]]:
    if row.get("verdict") != "FAIL":
        return False, {}
    observed_pointer = row.get("target_pointer")
    canonical_pointer = observed_pointer
    if (
        isinstance(observed_pointer, str)
        and observed_pointer.startswith("/")
        and not observed_pointer.startswith("/candidate_card")
    ):
        canonical_pointer = "/candidate_card" + observed_pointer
    exists, resolved = resolve_json_pointer(case, canonical_pointer)
    boundary_container_allowed = (
        row.get("issue_family") == "boundary_omission"
        and canonical_pointer == "/candidate_card/boundary_conditions"
        and exists
        and isinstance(resolved, list)
    )
    scalar_leaf = (
        exists
        and isinstance(canonical_pointer, str)
        and canonical_pointer.startswith("/candidate_card/")
        and not isinstance(resolved, (Mapping, list))
    )
    if boundary_container_allowed or scalar_leaf:
        return False, {}
    if not exists:
        resolved_kind = "missing"
    elif isinstance(resolved, Mapping):
        resolved_kind = "object"
    elif isinstance(resolved, list):
        resolved_kind = "array"
    else:
        resolved_kind = "scalar"
    return True, {
        "target_pointer": observed_pointer,
        "canonical_pointer": canonical_pointer,
        "pointer_exists": exists,
        "resolved_value_kind": resolved_kind,
        "issue_family": row.get("issue_family"),
    }


def misses_explicit_lineage_timestamp_conflict(
    *,
    case: Mapping[str, Any],
    row: Mapping[str, Any],
) -> tuple[bool, dict[str, Any]]:
    card = case.get("candidate_card")
    lineage = case.get("lineage_context")
    if not isinstance(card, Mapping) or not isinstance(lineage, Mapping):
        return False, {}
    source_timestamp = card.get("source_timestamp")
    correction_published_at = lineage.get("correction_published_at")
    source_time = parse_aware_timestamp(source_timestamp)
    correction_time = parse_aware_timestamp(correction_published_at)
    if (
        source_time is None
        or correction_time is None
        or source_time >= correction_time
    ):
        return False, {}
    claims_clean = (
        row.get("verdict") == "PASS"
        or row.get("issue_family") == "none"
        or row.get("status") == "SUPPORTED"
        or row.get("action") == "no_issue"
    )
    return claims_clean, {
        "source_timestamp": source_timestamp,
        "correction_published_at": correction_published_at,
        "subject_judgment": {
            field: row.get(field)
            for field in (
                "verdict",
                "issue_family",
                "status",
                "action",
                "target_pointer",
            )
        },
    }


def audit_response(
    *,
    form: Mapping[str, Any],
    response: Any,
    schema: Mapping[str, Any],
    apply_gold_blind_semantic_guards: bool = True,
) -> dict[str, Any]:
    form = require_mapping(form, "form")
    schema = require_mapping(schema, "schema")
    cases, expected_ids = form_cases(form)
    expected_set = set(expected_ids)
    case_by_id = {case["case_id"]: case for case in cases}
    item_schema = row_schema(schema)
    try:
        Draft202012Validator.check_schema(schema)
        item_validator = Draft202012Validator(item_schema)
    except Exception as exc:
        raise ReviewerExamError(f"OUTPUT_SCHEMA_INVALID:{type(exc).__name__}:{exc}") from exc

    findings: list[dict[str, Any]] = []
    mechanical_actions: list[dict[str, Any]] = []
    repair_ids: set[str] = set()
    unrecoverable = False
    rows: list[Any] = []

    if not isinstance(response, Mapping):
        findings.append(
            finding(
                "ROOT_OBJECT_REQUIRED",
                observed=type(response).__name__,
                expected="object",
                action="REJECT_RESPONSE",
            )
        )
        unrecoverable = True
    else:
        for key in sorted(str(key) for key in response if key != "reviews"):
            findings.append(finding("UNEXPECTED_ROOT_FIELD", field=key, action="REMOVE_ROOT_FIELD"))
            mechanical_actions.append({"code": "REMOVE_ROOT_FIELD", "field": key})
        observed_rows = response.get("reviews")
        if not isinstance(observed_rows, list):
            findings.append(
                finding(
                    "REVIEWS_ARRAY_REQUIRED",
                    field="reviews",
                    observed=type(observed_rows).__name__,
                    expected="array",
                    action="REJECT_RESPONSE",
                )
            )
            unrecoverable = True
        else:
            rows = observed_rows

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    unbound_rows = 0
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            findings.append(
                finding(
                    "REVIEW_ROW_OBJECT_REQUIRED",
                    observed={"index": index, "type": type(row).__name__},
                    action="REJECT_RESPONSE",
                )
            )
            unbound_rows += 1
            unrecoverable = True
            continue
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            findings.append(
                finding(
                    "REVIEW_ROW_CASE_ID_REQUIRED",
                    observed={"index": index},
                    action="REJECT_RESPONSE",
                )
            )
            unbound_rows += 1
            unrecoverable = True
            continue
        grouped[case_id].append(row)

    unknown_ids = sorted(case_id for case_id in grouped if case_id not in expected_set)
    for case_id in unknown_ids:
        findings.append(
            finding(
                "UNKNOWN_CASE_ROW",
                case_id=case_id,
                observed=len(grouped[case_id]),
                action="REMOVE_UNKNOWN_CASE_ROW",
            )
        )
        mechanical_actions.append({"code": "REMOVE_UNKNOWN_CASE_ROW", "case_id": case_id})

    missing_ids = [case_id for case_id in expected_ids if case_id not in grouped]
    for case_id in missing_ids:
        findings.append(
            finding(
                "MISSING_CASE_ROW",
                case_id=case_id,
                observed=0,
                expected=1,
                action="REPLACE_REVIEW_ROW",
            )
        )
        repair_ids.add(case_id)

    identical_duplicates: list[str] = []
    nonidentical_duplicates: list[str] = []
    for case_id in expected_ids:
        values = grouped.get(case_id, [])
        if len(values) <= 1:
            continue
        unique = {canonical_bytes(value) for value in values}
        if len(unique) == 1:
            identical_duplicates.append(case_id)
            findings.append(
                finding(
                    "IDENTICAL_DUPLICATE_CASE_ROW",
                    case_id=case_id,
                    observed=len(values),
                    expected=1,
                    action="DEDUPE_IDENTICAL_ROW",
                )
            )
            mechanical_actions.append({"code": "DEDUPE_IDENTICAL_ROW", "case_id": case_id})
        else:
            nonidentical_duplicates.append(case_id)
            findings.append(
                finding(
                    "NONIDENTICAL_DUPLICATE_CASE_ROW",
                    case_id=case_id,
                    observed=len(values),
                    expected=1,
                    action="REPLACE_REVIEW_ROW",
                )
            )
            repair_ids.add(case_id)

    observed_order: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        case_id = row.get("case_id")
        if case_id in expected_set and case_id not in seen:
            observed_order.append(case_id)
            seen.add(case_id)
    expected_observed_order = [case_id for case_id in expected_ids if case_id in seen]
    if observed_order != expected_observed_order:
        findings.append(
            finding(
                "CASE_ORDER_MISMATCH",
                observed=observed_order,
                expected=expected_observed_order,
                action="REORDER_CASE_ROWS",
            )
        )
        mechanical_actions.append({"code": "REORDER_CASE_ROWS"})

    for case_id in expected_ids:
        rows_for_case = grouped.get(case_id, [])
        if not rows_for_case:
            continue
        row = rows_for_case[0]
        errors = sorted(
            item_validator.iter_errors(row),
            key=lambda error: (tuple(str(part) for part in error.absolute_path), str(error.validator), error.message),
        )
        for error in errors:
            findings.append(
                finding(
                    f"ROW_SCHEMA_{str(error.validator).upper()}",
                    case_id=case_id,
                    field="/".join(str(part) for part in error.absolute_path) or None,
                    observed={"message": error.message},
                    expected=error.validator_value,
                    action="REPLACE_REVIEW_ROW",
                )
            )
            repair_ids.add(case_id)
        refs = row.get("evidence_refs")
        if isinstance(refs, list):
            allowed = {
                unit.get("evidence_id")
                for unit in case_by_id[case_id].get("evidence_units", [])
                if isinstance(unit, Mapping) and isinstance(unit.get("evidence_id"), str)
            }
            unknown_refs = sorted({ref for ref in refs if isinstance(ref, str)} - allowed)
            if unknown_refs:
                findings.append(
                    finding(
                        "UNKNOWN_EVIDENCE_REFERENCE",
                        case_id=case_id,
                        field="evidence_refs",
                        observed=unknown_refs,
                        expected=sorted(allowed),
                        action="REPLACE_REVIEW_ROW",
                    )
                )
                repair_ids.add(case_id)
        if apply_gold_blind_semantic_guards:
            nonexact_pointer, observed_pointer = nonexact_defect_target_pointer(
                case=case_by_id[case_id],
                row=row,
            )
            if nonexact_pointer:
                findings.append(
                    finding(
                        "GOLD_BLIND_TARGET_POINTER_NOT_EXACT",
                        case_id=case_id,
                        field="target_pointer",
                        observed=observed_pointer,
                        expected={
                            "constraint": (
                                "Use an existing exact Card JSON pointer to the deepest "
                                "existing scalar leaf that carries the defect."
                            ),
                            "boundary_omission_exception": (
                                "/candidate_card/boundary_conditions may identify the "
                                "container when the defect is an omitted condition."
                            ),
                        },
                        action="REPLACE_REVIEW_ROW",
                    )
                )
                repair_ids.add(case_id)
            misses_timestamp, observed_timestamp = (
                misses_explicit_lineage_timestamp_conflict(
                    case=case_by_id[case_id],
                    row=row,
                )
            )
            if misses_timestamp:
                findings.append(
                    finding(
                        "GOLD_BLIND_LINEAGE_TIMESTAMP_CONFLICT",
                        case_id=case_id,
                        field="candidate_card/source_timestamp",
                        observed=observed_timestamp,
                        expected={
                            "constraint": (
                                "An active Card snapshot must not predate a published "
                                "lineage correction without acknowledging the conflict."
                            ),
                            "comparison": (
                                "candidate_card.source_timestamp >= "
                                "lineage_context.correction_published_at"
                            ),
                        },
                        action="REPLACE_REVIEW_ROW",
                    )
                )
                repair_ids.add(case_id)

    if unrecoverable:
        status = "UNRECOVERABLE"
    elif repair_ids:
        status = "REPAIRABLE"
    elif findings:
        status = "MECHANICAL_RECOVERY"
    else:
        status = "PASS"
    value: dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "status": status,
        "form_hash": sha256(form),
        "response_hash": sha256(response),
        "schema_hash": sha256(schema),
        "expected_case_count": len(expected_ids),
        "observed_row_count": len(rows),
        "findings": findings,
        "finding_count": len(findings),
        "repair_exact_set": [case_id for case_id in expected_ids if case_id in repair_ids],
        "mechanical_actions": mechanical_actions,
        "missing_case_ids": missing_ids,
        "unknown_case_ids": unknown_ids,
        "identical_duplicate_case_ids": identical_duplicates,
        "nonidentical_duplicate_case_ids": nonidentical_duplicates,
        "unbound_row_count": unbound_rows,
        "unrecoverable": unrecoverable,
        "structure_counts": {
            "repair_case_count": len(repair_ids),
            "mechanical_action_count": len(mechanical_actions),
            "missing_case_count": len(missing_ids),
            "unknown_case_count": len(unknown_ids),
            "duplicate_case_count": len(identical_duplicates) + len(nonidentical_duplicates),
            "schema_finding_count": sum(item["code"].startswith("ROW_SCHEMA_") for item in findings),
            "unknown_evidence_reference_count": sum(item["code"] == "UNKNOWN_EVIDENCE_REFERENCE" for item in findings),
            "gold_blind_lineage_timestamp_conflict_count": sum(
                item["code"] == "GOLD_BLIND_LINEAGE_TIMESTAMP_CONFLICT"
                for item in findings
            ),
            "gold_blind_target_pointer_not_exact_count": sum(
                item["code"] == "GOLD_BLIND_TARGET_POINTER_NOT_EXACT"
                for item in findings
            ),
        },
    }
    value["audit_sha256"] = sha256(value)
    return value


def mechanically_normalize(
    *,
    form: Mapping[str, Any],
    response: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    audit = audit_response(form=form, response=response, schema=schema)
    if audit["status"] not in {"PASS", "MECHANICAL_RECOVERY"}:
        raise ReviewerExamError(f"RESPONSE_NOT_MECHANICALLY_NORMALIZABLE:{audit['status']}")
    cases, expected_ids = form_cases(form)
    expected_set = set(expected_ids)
    case_by_id = {case["case_id"]: case for case in cases}
    selected: dict[str, Mapping[str, Any]] = {}
    for row in response.get("reviews", []):
        if not isinstance(row, Mapping):
            continue
        case_id = row.get("case_id")
        if case_id in expected_set and case_id not in selected:
            normalized_row = deepcopy(dict(row))
            pointer = normalized_row.get("target_pointer")
            if isinstance(pointer, str) and pointer.startswith("/") and not pointer.startswith("/candidate_card/"):
                candidate_pointer = "/candidate_card" + pointer
                if json_pointer_exists(case_by_id[case_id], candidate_pointer):
                    normalized_row["target_pointer"] = candidate_pointer
            selected[case_id] = normalized_row
    normalized = {"reviews": [deepcopy(selected[case_id]) for case_id in expected_ids]}
    pointer_normalizations = sum(
        1
        for original in response.get("reviews", [])
        if isinstance(original, Mapping)
        and original.get("case_id") in selected
        and original.get("target_pointer") != selected[original["case_id"]].get("target_pointer")
    )
    final_audit = audit_response(form=form, response=normalized, schema=schema)
    if final_audit["status"] != "PASS":
        raise ReviewerExamError(f"MECHANICAL_NORMALIZATION_FAILED:{final_audit['status']}")
    return {
        "status": "PASS",
        "normalized_response": normalized,
        "initial_audit": audit,
        "final_audit": final_audit,
        "model_api_call_required": False,
        "repair_turns": 0,
        "pointer_normalization_count": pointer_normalizations,
    }


def json_pointer_exists(value: Any, pointer: str) -> bool:
    return resolve_json_pointer(value, pointer)[0]


def directed_repair_schema(schema: Mapping[str, Any], case_ids: list[str]) -> dict[str, Any]:
    value = deepcopy(dict(schema))
    reviews = value["properties"]["reviews"]
    reviews["minItems"] = len(case_ids)
    reviews["maxItems"] = len(case_ids)
    reviews["items"]["properties"]["case_id"] = {"type": "string", "enum": list(case_ids)}
    return value


def build_directed_repair_package(
    *,
    form: Mapping[str, Any],
    original_response: Mapping[str, Any],
    audit: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    verify_self_hash(audit, "audit_sha256")
    if audit.get("form_hash") != sha256(form):
        raise ReviewerExamError("AUDIT_FORM_HASH_MISMATCH")
    if audit.get("response_hash") != sha256(original_response):
        raise ReviewerExamError("AUDIT_RESPONSE_HASH_MISMATCH")
    if audit.get("schema_hash") != sha256(schema):
        raise ReviewerExamError("AUDIT_SCHEMA_HASH_MISMATCH")
    if audit.get("status") != "REPAIRABLE" or audit.get("unrecoverable") is True:
        raise ReviewerExamError("DIRECTED_REPAIR_REQUIRES_REPAIRABLE_AUDIT")
    cases, expected_ids = form_cases(form)
    case_by_id = {case["case_id"]: case for case in cases}
    repair_ids = audit.get("repair_exact_set")
    if not isinstance(repair_ids, list) or not repair_ids:
        raise ReviewerExamError("REPAIR_EXACT_SET_REQUIRED")
    if repair_ids != [case_id for case_id in expected_ids if case_id in set(repair_ids)]:
        raise ReviewerExamError("REPAIR_EXACT_SET_ORDER_MISMATCH")
    current_rows: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in original_response.get("reviews", []):
        if isinstance(row, Mapping) and row.get("case_id") in set(repair_ids):
            current_rows[row["case_id"]].append(row)
    targets = []
    for case_id in repair_ids:
        findings_for_case = [
            item for item in audit.get("findings", [])
            if isinstance(item, Mapping) and item.get("case_id") == case_id
        ]
        existing = current_rows.get(case_id, [])
        targets.append(
            {
                "case_id": case_id,
                "case_hash": sha256(case_by_id[case_id]),
                "current_rows": deepcopy(existing),
                "current_rows_hash": sha256(existing),
                "findings": deepcopy(findings_for_case),
                "allowed_actions": ["REPLACE_REVIEW_ROW"],
            }
        )
    repair_form = {
        **{key: deepcopy(value) for key, value in form.items() if key != "cases"},
        "cases": [deepcopy(case_by_id[case_id]) for case_id in repair_ids],
    }
    repair_schema = directed_repair_schema(schema, repair_ids)
    contract: dict[str, Any] = {
        "schema_version": "m14-card-reviewer-directed-repair-binding-v2",
        "original_form_hash": sha256(form),
        "original_response_hash": sha256(original_response),
        "output_schema_hash": sha256(schema),
        "repair_form_hash": sha256(repair_form),
        "repair_schema_hash": sha256(repair_schema),
        "repair_exact_set": list(repair_ids),
        "repair_targets": targets,
        "maximum_model_repair_turns": 1,
        "whole_form_regeneration_forbidden": True,
    }
    contract["contract_sha256"] = sha256(contract)
    prompt_payload = {
        "instruction": (
            "Replace all and only the listed review rows. Return one row for every target case and no other case. "
            "Use only the supplied target cases, current rows, machine findings, and output schema. "
            "For a defect, target_pointer must identify an existing exact Card field: use the deepest existing scalar leaf, "
            "except that boundary_omission may use /candidate_card/boundary_conditions for an omitted condition. "
            "Do not regenerate the whole form."
        ),
        "targets": targets,
        "repair_form": repair_form,
        "output_schema": repair_schema,
        "contract_sha256": contract["contract_sha256"],
    }
    prompt = json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    lowered = prompt.lower()
    for forbidden in ("expected_verdict", "required_evidence_refs", "family_quota", '"gold"'):
        if forbidden in lowered:
            raise ReviewerExamError(f"REPAIR_PROMPT_FORBIDDEN_SCORING_FIELD:{forbidden}")
    package = {
        "schema_version": REPAIR_PACKAGE_SCHEMA_VERSION,
        "contract": contract,
        "repair_form": repair_form,
        "repair_schema": repair_schema,
        "prompt": prompt,
        "gold_sent_to_subject": False,
        "gold_sent_to_provider": False,
    }
    package["package_sha256"] = sha256(package)
    return package


def merge_directed_repair(
    *,
    form: Mapping[str, Any],
    original_response: Mapping[str, Any],
    repair_response: Mapping[str, Any],
    schema: Mapping[str, Any],
    repair_contract: Mapping[str, Any],
) -> dict[str, Any]:
    verify_self_hash(repair_contract, "contract_sha256")
    if repair_contract.get("original_form_hash") != sha256(form):
        raise ReviewerExamError("REPAIR_CONTRACT_FORM_HASH_MISMATCH")
    if repair_contract.get("original_response_hash") != sha256(original_response):
        raise ReviewerExamError("REPAIR_CONTRACT_RESPONSE_HASH_MISMATCH")
    if repair_contract.get("output_schema_hash") != sha256(schema):
        raise ReviewerExamError("REPAIR_CONTRACT_SCHEMA_HASH_MISMATCH")
    repair_ids = repair_contract.get("repair_exact_set")
    if not isinstance(repair_ids, list) or not repair_ids:
        raise ReviewerExamError("REPAIR_CONTRACT_EXACT_SET_REQUIRED")
    repair_schema = directed_repair_schema(schema, repair_ids)
    if sha256(repair_schema) != repair_contract.get("repair_schema_hash"):
        raise ReviewerExamError("REPAIR_CONTRACT_DYNAMIC_SCHEMA_HASH_MISMATCH")
    repair_audit_errors = sorted(
        Draft202012Validator(repair_schema).iter_errors(repair_response),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )
    if repair_audit_errors:
        raise ReviewerExamError(f"REPAIR_RESPONSE_SCHEMA_INVALID:{len(repair_audit_errors)}")
    repair_rows = repair_response.get("reviews")
    if not isinstance(repair_rows, list):
        raise ReviewerExamError("REPAIR_RESPONSE_ROWS_REQUIRED")
    observed_ids = [row.get("case_id") for row in repair_rows if isinstance(row, Mapping)]
    if observed_ids != repair_ids or len(observed_ids) != len(set(observed_ids)):
        raise ReviewerExamError("REPAIR_CASE_ID_EXACT_SET_MISMATCH")
    target_by_id = {target["case_id"]: target for target in repair_contract.get("repair_targets", [])}
    current_by_id: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in original_response.get("reviews", []):
        if isinstance(row, Mapping) and row.get("case_id") in set(repair_ids):
            current_by_id[row["case_id"]].append(row)
    for case_id in repair_ids:
        target = target_by_id.get(case_id)
        if not isinstance(target, Mapping):
            raise ReviewerExamError(f"REPAIR_TARGET_MISSING:{case_id}")
        if target.get("current_rows_hash") != sha256(current_by_id.get(case_id, [])):
            raise ReviewerExamError(f"REPAIR_TARGET_CURRENT_ROW_HASH_MISMATCH:{case_id}")
    _, expected_ids = form_cases(form)
    replacements = {row["case_id"]: row for row in repair_rows}
    retained: dict[str, Mapping[str, Any]] = {}
    for row in original_response.get("reviews", []):
        if not isinstance(row, Mapping):
            continue
        case_id = row.get("case_id")
        if case_id in set(expected_ids) and case_id not in set(repair_ids) and case_id not in retained:
            retained[case_id] = row
    merged_rows = []
    for case_id in expected_ids:
        if case_id in replacements:
            merged_rows.append(deepcopy(replacements[case_id]))
        elif case_id in retained:
            merged_rows.append(deepcopy(retained[case_id]))
        else:
            raise ReviewerExamError(f"MERGED_CASE_MISSING:{case_id}")
    merged = {"reviews": merged_rows}
    merged_audit = audit_response(form=form, response=merged, schema=schema)
    if merged_audit["status"] != "PASS":
        raise ReviewerExamError(f"POST_REPAIR_FULL_AUDIT_FAILED:{merged_audit['status']}")
    normalized = mechanically_normalize(form=form, response=merged, schema=schema)
    merged = normalized["normalized_response"]
    value = {
        "schema_version": REPAIR_MERGE_SCHEMA_VERSION,
        "status": "PASS",
        "repair_exact_set": list(repair_ids),
        "repair_case_count": len(repair_ids),
        "merged_response": merged,
        "merged_response_hash": sha256(merged),
        "merged_audit": merged_audit,
        "repair_turns": 1,
        "pointer_normalization_count": normalized["pointer_normalization_count"],
    }
    value["merge_sha256"] = sha256(value)
    return value


def interpolate(knots: list[list[float]] | tuple[tuple[float, float], ...], value: float) -> float:
    bounded = max(float(knots[0][0]), min(float(knots[-1][0]), float(value)))
    for left, right in zip(knots, knots[1:]):
        x0, y0 = float(left[0]), float(left[1])
        x1, y1 = float(right[0]), float(right[1])
        if bounded <= x1:
            if x1 == x0:
                return y1
            return y0 + (bounded - x0) * (y1 - y0) / (x1 - x0)
    return float(knots[-1][1])


def evidence_score(observed: list[str], required: list[str], allowed: list[str]) -> tuple[float, float, float]:
    observed_set = set(observed)
    required_set = set(required)
    allowed_set = set(allowed)
    if not required_set:
        return (100.0 if not observed_set else 0.0, 1.0 if not observed_set else 0.0, 1.0 if not observed_set else 0.0)
    recall = len(observed_set & required_set) / len(required_set)
    precision = len(observed_set & allowed_set) / len(observed_set) if observed_set else 0.0
    return (100.0 * (recall + precision) / 2.0, recall, precision)


def score_case(row: Mapping[str, Any], gold: Mapping[str, Any]) -> dict[str, Any]:
    clean = gold.get("expected_verdict") == "PASS"
    components: dict[str, float] = {}
    substantive_false_positive = False
    if clean:
        evidence_recall = 1.0
        evidence_precision = 1.0
        observed_evidence = set(row.get("evidence_refs") or [])
        allowed_evidence = set(gold.get("allowed_evidence_refs") or [])
        clean_evidence_valid = observed_evidence.issubset(allowed_evidence)
        clean_semantics = (
            row.get("verdict") == "PASS"
            and row.get("issue_family") == "none"
            and row.get("severity") == "none"
            and row.get("target_pointer") == ""
            and row.get("status") == "SUPPORTED"
            and row.get("action") == "no_issue"
        )
        clean_integrity = clean_semantics and clean_evidence_valid
        substantive_false_positive = not clean_semantics
        components = {
            "verdict": 60.0 if row.get("verdict") == "PASS" else 0.0,
            "issue_family": 10.0 if row.get("issue_family") == "none" else 0.0,
            "status": 10.0 if row.get("status") == "SUPPORTED" else 0.0,
            "action": 10.0 if row.get("action") == "no_issue" else 0.0,
            "clean_integrity": 10.0 if clean_integrity else 0.0,
        }
        evidence_percent = 100.0 if clean_evidence_valid else 0.0
        target_match = row.get("target_pointer") in gold.get("accepted_target_pointers", [])
    else:
        evidence_percent, evidence_recall, evidence_precision = evidence_score(
            list(row.get("evidence_refs") or []),
            list(gold.get("required_evidence_refs") or []),
            list(gold.get("allowed_evidence_refs") or []),
        )
        target_match = row.get("target_pointer") in gold.get("accepted_target_pointers", [])
        components = {
            "verdict": 15.0 if row.get("verdict") == "FAIL" else 0.0,
            "issue_family": 25.0 if row.get("issue_family") == gold.get("issue_family") else 0.0,
            "status": 15.0 if row.get("status") == gold.get("expected_status") else 0.0,
            "target_pointer": 15.0 if target_match else 0.0,
            "evidence": 20.0 * evidence_percent / 100.0,
            "severity": 5.0 if row.get("severity") == gold.get("severity") else 0.0,
            "action": 5.0 if row.get("action") in gold.get("accepted_actions", []) else 0.0,
        }
    total = round(sum(components.values()), 6)
    classified = (
        clean
        or (
            row.get("issue_family") == gold.get("issue_family")
            and row.get("status") == gold.get("expected_status")
        )
    )
    detected = clean or (row.get("verdict") == "FAIL" and evidence_percent >= 50.0)
    localized = detected and target_match
    return {
        "case_id": gold["case_id"],
        "family": "clean_control" if clean else gold["issue_family"],
        "difficulty": gold["difficulty"],
        "severity": gold["severity"],
        "critical": gold["critical"],
        "clean": clean,
        "score": total,
        "components": {key: round(value, 6) for key, value in components.items()},
        "evidence_percent": round(evidence_percent, 6),
        "target_match": target_match,
        "detected": detected,
        "classified": classified,
        "localized": localized,
        "evidence_recall": round(evidence_recall, 6),
        "evidence_precision": round(evidence_precision, 6),
        "substantive_false_positive": substantive_false_positive,
    }


def score_form(
    *,
    form: Mapping[str, Any],
    final_response: Mapping[str, Any],
    gold: Mapping[str, Any],
    schema: Mapping[str, Any],
    scoring_protocol: Mapping[str, Any],
    repair_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    audit = audit_response(
        form=form,
        response=final_response,
        schema=schema,
        apply_gold_blind_semantic_guards=False,
    )
    if audit["status"] != "PASS":
        raise ReviewerExamError(f"FINAL_RESPONSE_NOT_SCOREABLE:{audit['status']}")
    cases, expected_ids = form_cases(form)
    del cases
    gold_cases = gold.get("cases")
    if not isinstance(gold_cases, list):
        raise ReviewerExamError("GOLD_CASES_REQUIRED")
    gold_by_id = {
        item.get("case_id"): item
        for item in gold_cases
        if isinstance(item, Mapping) and isinstance(item.get("case_id"), str)
    }
    if set(gold_by_id) != set(expected_ids) or len(gold_cases) != len(expected_ids):
        raise ReviewerExamError("GOLD_CASE_ID_EXACT_SET_MISMATCH")
    rows = final_response["reviews"]
    row_by_id = {row["case_id"]: row for row in rows}
    details = [score_case(row_by_id[case_id], gold_by_id[case_id]) for case_id in expected_ids]
    family_scores: dict[str, float] = {}
    for family in (*DEFECT_FAMILIES, "clean_control"):
        values = [item["score"] for item in details if item["family"] == family]
        if len(values) < 10:
            raise ReviewerExamError(f"FAMILY_SAMPLE_QUOTA_BELOW_10:{family}:{len(values)}")
        family_scores[family] = statistics.fmean(values)
    overall = statistics.fmean(item["score"] for item in details)
    hard_values = [item["score"] for item in details if item["difficulty"] in {"hard", "adversarial"}]
    hard_case = statistics.fmean(hard_values)
    worst_family = min(family_scores.values())
    bottom_count = max(1, math.ceil(len(details) * 0.25))
    bottom_quartile = statistics.fmean(sorted(item["score"] for item in details)[:bottom_count])
    clean_rows = [item for item in details if item["clean"]]
    clean_specificity = 100.0 * sum(not item["substantive_false_positive"] for item in clean_rows) / len(clean_rows)
    defect_rows = [item for item in details if not item["clean"]]
    critical_rows = [item for item in defect_rows if item["critical"]]
    stale_lineage_rows = [item for item in defect_rows if item["family"] == "stale_lineage"]
    evidence_localization = statistics.fmean(
        (item["evidence_percent"] + (100.0 if item["target_match"] else 0.0)) / 2.0
        for item in defect_rows
    )
    critical_detection = 100.0 * sum(item["detected"] for item in critical_rows) / len(critical_rows)
    stale_lineage_detection = 100.0 * sum(item["detected"] for item in stale_lineage_rows) / len(stale_lineage_rows)
    metrics = {
        "overall_semantic": overall,
        "hard_case": hard_case,
        "worst_family": worst_family,
        "bottom_quartile": bottom_quartile,
        "clean_specificity": clean_specificity,
        "evidence_localization": evidence_localization,
        "critical_detection": critical_detection,
        "stale_lineage_detection": stale_lineage_detection,
    }
    component_specs = scoring_protocol.get("piecewise_components")
    if not isinstance(component_specs, Mapping):
        raise ReviewerExamError("PIECEWISE_COMPONENTS_REQUIRED")
    component_maximum = sum(float(spec.get("points", 0)) for spec in component_specs.values() if isinstance(spec, Mapping))
    if abs(component_maximum - 100.0) > 1e-9:
        raise ReviewerExamError(f"PIECEWISE_COMPONENT_MAXIMUM_MUST_SUM_TO_100:{component_maximum}")
    component_points: dict[str, float] = {}
    for name, metric in metrics.items():
        spec = component_specs.get(name)
        if not isinstance(spec, Mapping) or not isinstance(spec.get("knots"), list):
            raise ReviewerExamError(f"PIECEWISE_COMPONENT_INVALID:{name}")
        component_points[name] = interpolate(spec["knots"], metric)
    semantic_score = sum(component_points.values())

    repair_metrics = dict(repair_metrics or {
        "form_case_count": len(expected_ids),
        "repair_turns": 0,
        "directed_repair_case_count": 0,
        "all_repairs_succeeded": True,
    })
    if repair_metrics.get("form_case_count") != len(expected_ids):
        raise ReviewerExamError("REPAIR_METRICS_FORM_CASE_COUNT_MISMATCH")
    turns = repair_metrics.get("repair_turns")
    repaired_count = repair_metrics.get("directed_repair_case_count")
    if turns not in {0, 1} or isinstance(repaired_count, bool) or not isinstance(repaired_count, int):
        raise ReviewerExamError("REPAIR_METRICS_INVALID")
    if repaired_count < 0 or repaired_count > len(expected_ids):
        raise ReviewerExamError("REPAIR_CASE_COUNT_OUT_OF_RANGE")
    if turns == 0 and repaired_count != 0:
        raise ReviewerExamError("REPAIR_CASE_COUNT_WITHOUT_TURN")
    repair_spec = scoring_protocol.get("directed_repair")
    if not isinstance(repair_spec, Mapping):
        raise ReviewerExamError("DIRECTED_REPAIR_SCORING_REQUIRED")
    fixed_penalty = float(repair_spec.get("fixed_turn_penalty", 0)) if turns else 0.0
    ratio = repaired_count / len(expected_ids)
    volume_penalty = interpolate(repair_spec.get("volume_penalty_knots", [[0, 0], [1, 0]]), ratio) if turns else 0.0
    repair_penalty = fixed_penalty + volume_penalty
    score = max(0.0, min(100.0, semantic_score - repair_penalty))

    missed_critical = [
        item["case_id"] for item in details
        if item["critical"] and not item["detected"]
    ]
    clean_false_positives = [
        item["case_id"] for item in clean_rows if item["substantive_false_positive"]
    ]
    stale_lineage_missed = [
        item["case_id"] for item in details
        if item["family"] == "stale_lineage" and not item["detected"]
    ]
    critical_mislocalized = [
        item["case_id"] for item in critical_rows if item["detected"] and not item["localized"]
    ]
    critical_misclassified = [
        item["case_id"] for item in critical_rows if item["detected"] and not item["classified"]
    ]
    critical_localization_rate = (
        sum(item["localized"] for item in critical_rows) / len(critical_rows)
        if critical_rows else 1.0
    )
    hard_gate_parameters = scoring_protocol.get("hard_gate_parameters")
    if not isinstance(hard_gate_parameters, Mapping):
        raise ReviewerExamError("HARD_GATE_PARAMETERS_REQUIRED")
    minimum_critical_localization_rate = float(
        hard_gate_parameters.get("minimum_critical_localization_rate", 1.0)
    )
    if not 0.0 <= minimum_critical_localization_rate <= 1.0:
        raise ReviewerExamError("CRITICAL_LOCALIZATION_THRESHOLD_INVALID")
    hard_failures = []
    if missed_critical:
        hard_failures.append("MISS_ANY_CRITICAL_CASE")
    if critical_localization_rate < minimum_critical_localization_rate:
        hard_failures.append("CRITICAL_LOCALIZATION_RATE_BELOW_THRESHOLD")
    if clean_false_positives:
        hard_failures.append("SUBSTANTIVE_FALSE_POSITIVE_ON_ANY_CLEAN_CONTROL")
    if stale_lineage_missed:
        hard_failures.append("STALE_LINEAGE_MISSED")
    value = {
        "schema_version": FORM_SCORE_SCHEMA_VERSION,
        "status": "SCORED",
        "form": form.get("form"),
        "form_id": form.get("form_id"),
        "score": round(score, 6),
        "semantic_score_before_repair_penalty": round(semantic_score, 6),
        "metrics": {key: round(value, 6) for key, value in metrics.items()},
        "piecewise_component_points": {key: round(value, 6) for key, value in component_points.items()},
        "family_scores": {key: round(value, 6) for key, value in family_scores.items()},
        "repair_metrics": repair_metrics,
        "repair_penalties": {
            "fixed_turn": round(fixed_penalty, 6),
            "volume": round(volume_penalty, 6),
            "total": round(repair_penalty, 6),
            "repaired_case_ratio": round(ratio, 9),
        },
        "critical_expected": sum(item["critical"] for item in details),
        "critical_missed": missed_critical,
        "critical_mislocalized": critical_mislocalized,
        "critical_misclassified": critical_misclassified,
        "critical_localization_rate": round(critical_localization_rate, 6),
        "minimum_critical_localization_rate": minimum_critical_localization_rate,
        "clean_control_count": len(clean_rows),
        "clean_false_positive_cases": clean_false_positives,
        "stale_lineage_missed": stale_lineage_missed,
        "hard_failures": hard_failures,
        "hard_gate_verdict": "PASS" if not hard_failures else "FAIL",
        "details": details,
        "final_response_hash": sha256(final_response),
        "gold_hash": sha256(gold),
        "gold_sent_to_subject": False,
        "gold_sent_to_provider": False,
    }
    value["score_sha256"] = sha256(value)
    return value


def _reviewer_exam_wrong_details(
    score: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    details = score.get("details")
    if not isinstance(details, list):
        raise ReviewerExamError("EXAM_SCORE_DETAILS_REQUIRED")
    wrong: list[Mapping[str, Any]] = []
    for index, detail in enumerate(details):
        if not isinstance(detail, Mapping):
            raise ReviewerExamError(
                f"EXAM_SCORE_DETAIL_MAPPING_REQUIRED:{index}"
            )
        value = detail.get("score")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ReviewerExamError(
                f"EXAM_SCORE_DETAIL_VALUE_REQUIRED:{index}"
            )
        if float(value) < 100.0:
            wrong.append(detail)
    return wrong


def _reviewer_exam_issue_metadata(
    detail: Mapping[str, Any],
) -> tuple[list[str], list[str], str]:
    components = detail.get("components")
    if not isinstance(components, Mapping):
        raise ReviewerExamError("EXAM_SCORE_DETAIL_COMPONENTS_REQUIRED")
    if detail.get("clean") is True:
        maxima = {
            "verdict": 60.0,
            "issue_family": 10.0,
            "status": 10.0,
            "action": 10.0,
            "clean_integrity": 10.0,
        }
    else:
        maxima = {
            "verdict": 15.0,
            "issue_family": 25.0,
            "status": 15.0,
            "target_pointer": 15.0,
            "evidence": 20.0,
            "severity": 5.0,
            "action": 5.0,
        }
    issues: set[str] = set()
    failed_fields: set[str] = set()
    for field, maximum in maxima.items():
        value = components.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ReviewerExamError(
                f"EXAM_SCORE_COMPONENT_VALUE_REQUIRED:{field}"
            )
        if float(value) < maximum:
            issues.add(f"{field.upper()}_INCORRECT")
            failed_fields.add(field)
    flag_contract = {
        "target_match": "TARGET_POINTER_MISMATCH",
        "detected": "DEFECT_NOT_DETECTED",
        "classified": "DEFECT_NOT_CLASSIFIED",
        "localized": "DEFECT_NOT_LOCALIZED",
    }
    if detail.get("clean") is not True:
        for field, issue in flag_contract.items():
            if detail.get(field) is False:
                issues.add(issue)
                failed_fields.add(field)
    if detail.get("substantive_false_positive") is True:
        issues.add("SUBSTANTIVE_FALSE_POSITIVE")
        failed_fields.add("clean_semantics")
    if not issues:
        issues.add("CASE_NOT_FULLY_CORRECT")
        failed_fields.add("complete_review_row")
    severity = "critical" if detail.get("critical") is True else "major"
    return sorted(issues), sorted(failed_fields), severity


def _reviewer_exam_repair_schema(
    schema: Mapping[str, Any],
    exact_sets: Mapping[str, list[str]],
) -> dict[str, Any]:
    source_properties = schema.get("properties")
    if not isinstance(source_properties, Mapping):
        raise ReviewerExamError("SCHEMA_PROPERTIES_REQUIRED")
    source_reviews = source_properties.get("reviews")
    if not isinstance(source_reviews, Mapping):
        raise ReviewerExamError("SCHEMA_REVIEWS_REQUIRED")
    source_items = source_reviews.get("items")
    if not isinstance(source_items, Mapping):
        raise ReviewerExamError("SCHEMA_REVIEW_ITEMS_REQUIRED")
    root: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {},
    }
    for form_id in ("A", "B"):
        case_ids = list(exact_sets.get(form_id, []))
        if not case_ids:
            continue
        row_schema = deepcopy(dict(source_items))
        row_properties = row_schema.get("properties")
        if not isinstance(row_properties, dict):
            raise ReviewerExamError(
                "SCHEMA_REVIEW_ITEM_PROPERTIES_REQUIRED"
            )
        row_properties["case_id"] = {
            "type": "string",
            "enum": case_ids,
        }
        form_key = f"form_{form_id}"
        root["required"].append(form_key)
        root["properties"][form_key] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["reviews"],
            "properties": {
                "reviews": {
                    "type": "array",
                    "minItems": len(case_ids),
                    "maxItems": len(case_ids),
                    "items": row_schema,
                }
            },
        }
    if not root["required"]:
        raise ReviewerExamError("NO_EXAM_WRONG_ITEMS")
    return root


def build_exam_directed_repair_package(
    *,
    forms: Mapping[str, Mapping[str, Any]],
    original_responses: Mapping[str, Mapping[str, Any]],
    original_scores: Mapping[str, Mapping[str, Any]],
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one all-and-only Gold-free repair round for the complete exam."""

    exact_sets: dict[str, list[str]] = {}
    repair_targets: dict[str, list[dict[str, Any]]] = {}
    source_form_hashes: dict[str, str] = {}
    source_response_hashes: dict[str, str] = {}
    source_score_hashes: dict[str, str] = {}
    issue_categories: set[str] = set()
    severity_categories: set[str] = set()
    wrong_item_count = 0
    for form_id in ("A", "B"):
        form = forms.get(form_id)
        response = original_responses.get(form_id)
        score = original_scores.get(form_id)
        if not isinstance(form, Mapping):
            raise ReviewerExamError(f"FORM_MAPPING_REQUIRED:{form_id}")
        if not isinstance(response, Mapping):
            raise ReviewerExamError(
                f"ORIGINAL_RESPONSE_MAPPING_REQUIRED:{form_id}"
            )
        if not isinstance(score, Mapping):
            raise ReviewerExamError(
                f"ORIGINAL_SCORE_MAPPING_REQUIRED:{form_id}"
            )
        cases, case_order = form_cases(form)
        case_by_id = {item["case_id"]: item for item in cases}
        rows = response.get("reviews")
        if not isinstance(rows, list):
            raise ReviewerExamError(
                f"RESPONSE_REVIEWS_ARRAY_REQUIRED:{form_id}"
            )
        row_by_id: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                raise ReviewerExamError(
                    f"RESPONSE_REVIEW_MAPPING_REQUIRED:{form_id}"
                )
            case_id = row.get("case_id")
            if not isinstance(case_id, str) or case_id in row_by_id:
                raise ReviewerExamError(
                    f"RESPONSE_REVIEW_ID_INVALID:{form_id}:{case_id}"
                )
            row_by_id[case_id] = row
        wrong_by_id = {
            item.get("case_id"): item
            for item in _reviewer_exam_wrong_details(score)
        }
        unknown = sorted(set(wrong_by_id) - set(case_order))
        if unknown:
            raise ReviewerExamError(
                f"EXAM_WRONG_ITEM_UNKNOWN_CASE:{form_id}:{unknown}"
            )
        exact_set = [
            case_id for case_id in case_order if case_id in wrong_by_id
        ]
        exact_sets[form_id] = exact_set
        wrong_item_count += len(exact_set)
        targets: list[dict[str, Any]] = []
        for case_id in exact_set:
            detail = wrong_by_id[case_id]
            issues, failed_fields, severity = (
                _reviewer_exam_issue_metadata(detail)
            )
            current = row_by_id.get(case_id)
            if current is None:
                raise ReviewerExamError(
                    f"WRONG_ITEM_CURRENT_ROW_MISSING:{form_id}:{case_id}"
                )
            issue_categories.update(issues)
            severity_categories.add(severity)
            targets.append(
                {
                    "case_id": case_id,
                    "severity": severity,
                    "issue_codes": issues,
                    "failed_output_fields": failed_fields,
                    "source_case": deepcopy(dict(case_by_id[case_id])),
                    "source_case_sha256": sha256(case_by_id[case_id]),
                    "current_row": deepcopy(dict(current)),
                    "current_row_sha256": sha256(current),
                    "allowed_actions": ["REPLACE_REVIEW_ROW"],
                    "requires_full_row": True,
                }
            )
        repair_targets[form_id] = targets
        source_form_hashes[form_id] = sha256(form)
        source_response_hashes[form_id] = sha256(response)
        source_score_hashes[form_id] = sha256(score)
    if wrong_item_count == 0:
        raise ReviewerExamError("NO_EXAM_WRONG_ITEMS")
    repair_schema = _reviewer_exam_repair_schema(schema, exact_sets)
    contract: dict[str, Any] = {
        "schema_version": EXAM_REPAIR_CONTRACT_SCHEMA_VERSION,
        "repair_round_limit": 1,
        "repair_after_both_initial_forms_scored": True,
        "whole_exam_regeneration_forbidden": True,
        "wrong_item_definition": (
            "EVERY_CASE_WITH_FROZEN_INITIAL_LOCAL_SCORE_BELOW_100"
        ),
        "wrong_item_exact_set": exact_sets,
        "wrong_item_count": wrong_item_count,
        "repair_artifact_categories": ["reviews"],
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
        "any_remaining_or_new_wrong_item_disqualifies": True,
        "subject_receives_answer_key": False,
        "provider_receives_answer_key": False,
        "penalty_contract": deepcopy(EXAM_REPAIR_PENALTY_CONTRACT),
    }
    contract["contract_sha256"] = sha256(contract)
    prompt_payload = {
        "instruction": (
            "Complete the single directed-repair round for this synthetic "
            "Card Reviewer examination after both Forms have finished. "
            "Replace all and only the listed review rows. Return every listed "
            "case exactly once, in the declared Form and case order, and "
            "return no other case. Use source_case as the only source of "
            "truth, current_row as the starting point, fix every machine "
            "issue_code and failed_output_field, and recheck the complete "
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
        "score",
        "penalty_contract",
    ):
        if forbidden in lowered:
            raise ReviewerExamError(
                f"EXAM_REPAIR_PROMPT_FORBIDDEN_FIELD:{forbidden}"
            )
    package = {
        "schema_version": EXAM_REPAIR_PACKAGE_SCHEMA_VERSION,
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
    """Validate one exact response and merge only the authorised review rows."""

    if not isinstance(repair_contract, Mapping):
        raise ReviewerExamError("EXAM_REPAIR_CONTRACT_MAPPING_REQUIRED")
    observed_hash = repair_contract.get("contract_sha256")
    unsigned = dict(repair_contract)
    unsigned.pop("contract_sha256", None)
    if observed_hash != sha256(unsigned):
        raise ReviewerExamError("EXAM_REPAIR_CONTRACT_HASH_MISMATCH")
    if (
        repair_contract.get("schema_version")
        != EXAM_REPAIR_CONTRACT_SCHEMA_VERSION
    ):
        raise ReviewerExamError(
            "EXAM_REPAIR_CONTRACT_SCHEMA_VERSION_MISMATCH"
        )
    if repair_contract.get("source_schema_sha256") != sha256(schema):
        raise ReviewerExamError("EXAM_REPAIR_SCHEMA_HASH_MISMATCH")
    exact_sets = repair_contract.get("wrong_item_exact_set")
    if not isinstance(exact_sets, Mapping):
        raise ReviewerExamError("EXAM_REPAIR_EXACT_SETS_REQUIRED")
    repair_schema = _reviewer_exam_repair_schema(schema, exact_sets)
    if repair_contract.get("repair_schema_sha256") != sha256(repair_schema):
        raise ReviewerExamError("EXAM_REPAIR_DYNAMIC_SCHEMA_HASH_MISMATCH")
    errors = sorted(
        Draft202012Validator(repair_schema).iter_errors(repair_response),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    if errors:
        raise ReviewerExamError(
            f"EXAM_REPAIR_RESPONSE_SCHEMA_INVALID:{len(errors)}:{errors[0].message}"
        )

    merged_responses: dict[str, dict[str, Any]] = {}
    change_receipts: dict[str, Any] = {}
    for form_id in ("A", "B"):
        form = forms.get(form_id)
        original = original_responses.get(form_id)
        if not isinstance(form, Mapping):
            raise ReviewerExamError(f"FORM_MAPPING_REQUIRED:{form_id}")
        if not isinstance(original, Mapping):
            raise ReviewerExamError(
                f"ORIGINAL_RESPONSE_MAPPING_REQUIRED:{form_id}"
            )
        if repair_contract["source_form_sha256"].get(form_id) != sha256(
            form
        ):
            raise ReviewerExamError(
                f"EXAM_REPAIR_FORM_HASH_MISMATCH:{form_id}"
            )
        if repair_contract["source_response_sha256"].get(
            form_id
        ) != sha256(original):
            raise ReviewerExamError(
                f"EXAM_REPAIR_RESPONSE_HASH_MISMATCH:{form_id}"
            )
        _, case_order = form_cases(form)
        original_rows = original.get("reviews")
        if not isinstance(original_rows, list):
            raise ReviewerExamError(
                f"ORIGINAL_REVIEWS_ARRAY_REQUIRED:{form_id}"
            )
        original_by_id = {
            row.get("case_id"): row
            for row in original_rows
            if isinstance(row, Mapping)
        }
        if set(original_by_id) != set(case_order):
            raise ReviewerExamError(
                f"ORIGINAL_CASE_EXACT_SET_MISMATCH:{form_id}"
            )
        target_ids = list(exact_sets.get(form_id, []))
        replacements: dict[str, Mapping[str, Any]] = {}
        if target_ids:
            repair_rows = repair_response[f"form_{form_id}"]["reviews"]
            observed_ids = [
                row.get("case_id") if isinstance(row, Mapping) else None
                for row in repair_rows
            ]
            if observed_ids != target_ids or len(observed_ids) != len(
                set(observed_ids)
            ):
                raise ReviewerExamError(
                    f"EXAM_REPAIR_CASE_ID_EXACT_SET_MISMATCH:{form_id}"
                )
            replacements = {
                row["case_id"]: row for row in repair_rows
            }
        merged_rows: list[dict[str, Any]] = []
        changed: list[dict[str, str]] = []
        retained: list[dict[str, str]] = []
        for case_id in case_order:
            original_row = original_by_id[case_id]
            if case_id in replacements:
                replacement = replacements[case_id]
                merged_rows.append(deepcopy(dict(replacement)))
                changed.append(
                    {
                        "case_id": case_id,
                        "before_sha256": sha256(original_row),
                        "after_sha256": sha256(replacement),
                    }
                )
            else:
                merged_rows.append(deepcopy(dict(original_row)))
                retained.append(
                    {
                        "case_id": case_id,
                        "sha256": sha256(original_row),
                    }
                )
        merged = deepcopy(dict(original))
        merged["reviews"] = merged_rows
        for receipt in retained:
            merged_row = next(
                row
                for row in merged_rows
                if row["case_id"] == receipt["case_id"]
            )
            if sha256(merged_row) != receipt["sha256"]:
                raise ReviewerExamError(
                    f"EXAM_REPAIR_NON_TARGET_ROW_CHANGED:{form_id}:{receipt['case_id']}"
                )
        audit = audit_response(
            form=form,
            response=merged,
            schema=schema,
            apply_gold_blind_semantic_guards=False,
        )
        if audit["status"] != "PASS":
            raise ReviewerExamError(
                f"EXAM_REPAIR_MERGED_RESPONSE_INVALID:{form_id}:{audit['status']}"
            )
        merged_responses[form_id] = merged
        change_receipts[form_id] = {
            "target_case_ids": target_ids,
            "changed_rows": changed,
            "retained_row_count": len(retained),
            "original_reviews_sha256": sha256(original_rows),
            "merged_reviews_sha256": sha256(merged_rows),
        }
    return {
        "schema_version": EXAM_REPAIR_MERGE_SCHEMA_VERSION,
        "status": "MERGED_EXACT_SET",
        "responses": merged_responses,
        "change_receipts": change_receipts,
        "repair_response_sha256": sha256(repair_response),
    }


def _reviewer_exam_form_penalty(
    *,
    form_case_count: int,
    wrong_case_count: int,
    issue_categories: set[str],
    severity_categories: set[str],
) -> dict[str, Any]:
    ratio = wrong_case_count / form_case_count
    volume = interpolate(EXAM_REPAIR_SCOPE_PENALTY_KNOTS, ratio)
    artifact_penalty = (
        float(EXAM_REPAIR_PENALTY_CONTRACT["artifact_category_points"])
        if wrong_case_count
        else 0.0
    )
    severity_penalty = sum(
        float(
            EXAM_REPAIR_PENALTY_CONTRACT["severity_category_points"][
                item
            ]
        )
        for item in severity_categories
    )
    issue_penalty = min(
        float(
            EXAM_REPAIR_PENALTY_CONTRACT[
                "distinct_issue_category_cap"
            ]
        ),
        len(issue_categories)
        * float(
            EXAM_REPAIR_PENALTY_CONTRACT[
                "distinct_issue_category_points"
            ]
        ),
    )
    total = volume + artifact_penalty + severity_penalty + issue_penalty
    return {
        "wrong_item_count": wrong_case_count,
        "form_case_count": form_case_count,
        "wrong_item_ratio": round(ratio, 9),
        "wrong_item_volume": round(volume, 6),
        "artifact_categories": ["reviews"] if wrong_case_count else [],
        "artifact_category_penalty": round(artifact_penalty, 6),
        "severity_categories": sorted(severity_categories),
        "severity_category_penalty": round(severity_penalty, 6),
        "issue_categories": sorted(issue_categories),
        "issue_categories_penalty": round(issue_penalty, 6),
        "total": round(total, 6),
    }


def score_exam_after_directed_repair(
    *,
    forms: Mapping[str, Mapping[str, Any]],
    golds: Mapping[str, Mapping[str, Any]],
    original_scores: Mapping[str, Mapping[str, Any]],
    merged_responses: Mapping[str, Mapping[str, Any]],
    schema: Mapping[str, Any],
    scoring_protocol: Mapping[str, Any],
    repair_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Rescore both Forms; a complete-but-wrong repair disqualifies the exam."""

    if not isinstance(repair_contract, Mapping):
        raise ReviewerExamError("EXAM_REPAIR_CONTRACT_MAPPING_REQUIRED")
    unsigned = dict(repair_contract)
    observed_hash = unsigned.pop("contract_sha256", None)
    if observed_hash != sha256(unsigned):
        raise ReviewerExamError("EXAM_REPAIR_CONTRACT_HASH_MISMATCH")
    exact_sets = repair_contract.get("wrong_item_exact_set")
    if not isinstance(exact_sets, Mapping):
        raise ReviewerExamError("EXAM_REPAIR_EXACT_SETS_REQUIRED")
    post_scores: dict[str, dict[str, Any]] = {}
    remaining: dict[str, list[str]] = {}
    new_wrong_items: dict[str, list[str]] = {}
    original_wrong_sets = {
        form_id: set(exact_sets.get(form_id, []))
        for form_id in ("A", "B")
    }
    for form_id in ("A", "B"):
        form = forms.get(form_id)
        gold = golds.get(form_id)
        merged = merged_responses.get(form_id)
        original_score = original_scores.get(form_id)
        if not all(
            isinstance(item, Mapping)
            for item in (form, gold, merged, original_score)
        ):
            raise ReviewerExamError(
                f"EXAM_REPAIR_SCORING_INPUT_REQUIRED:{form_id}"
            )
        if repair_contract["source_score_sha256"].get(
            form_id
        ) != sha256(original_score):
            raise ReviewerExamError(
                f"EXAM_REPAIR_ORIGINAL_SCORE_HASH_MISMATCH:{form_id}"
            )
        post = score_form(
            form=form,
            final_response=merged,
            gold=gold,
            schema=schema,
            scoring_protocol=scoring_protocol,
            repair_metrics={
                "form_case_count": len(form_cases(form)[1]),
                "repair_turns": 0,
                "directed_repair_case_count": 0,
                "all_repairs_succeeded": True,
            },
        )
        post_scores[form_id] = post
        wrong_after = {
            item["case_id"]
            for item in _reviewer_exam_wrong_details(post)
        }
        remaining[form_id] = sorted(
            wrong_after & original_wrong_sets[form_id]
        )
        new_wrong_items[form_id] = sorted(
            wrong_after - original_wrong_sets[form_id]
        )
    remaining_count = sum(len(items) for items in remaining.values())
    new_wrong_count = sum(
        len(items) for items in new_wrong_items.values()
    )
    if remaining_count or new_wrong_count:
        return {
            "schema_version": EXAM_REPAIR_RESULT_SCHEMA_VERSION,
            "status": "DIRECTED_REPAIR_FAILED",
            "qualification_verdict": "DISQUALIFIED",
            "failure_reason": (
                "EXAM_WRONG_ITEM_REMAINS_OR_NEW_WRONG_ITEM_CREATED"
            ),
            "remaining_wrong_items": remaining,
            "new_wrong_items": new_wrong_items,
            "remaining_wrong_item_count": (
                remaining_count + new_wrong_count
            ),
            "final_score": float(
                EXAM_REPAIR_PENALTY_CONTRACT[
                    "failed_repair_final_score"
                ]
            ),
            "form_scores_after_repair_before_disqualification": {
                form_id: post_scores[form_id]["score"]
                for form_id in ("A", "B")
            },
            "provider_blind_formula": True,
        }

    final_form_scores: dict[str, float] = {}
    form_penalties: dict[str, dict[str, Any]] = {}
    form_results: dict[str, Any] = {}
    for form_id in ("A", "B"):
        original_details = {
            item["case_id"]: item
            for item in _reviewer_exam_wrong_details(
                original_scores[form_id]
            )
        }
        issue_categories: set[str] = set()
        severity_categories: set[str] = set()
        target_ids = list(exact_sets.get(form_id, []))
        for case_id in target_ids:
            issues, _failed_fields, severity = (
                _reviewer_exam_issue_metadata(
                    original_details[case_id]
                )
            )
            issue_categories.update(issues)
            severity_categories.add(severity)
        penalty = _reviewer_exam_form_penalty(
            form_case_count=len(form_cases(forms[form_id])[1]),
            wrong_case_count=len(target_ids),
            issue_categories=issue_categories,
            severity_categories=severity_categories,
        )
        form_penalties[form_id] = penalty
        base = float(post_scores[form_id]["score"])
        pre_penalty = (
            min(
                base,
                float(
                    EXAM_REPAIR_PENALTY_CONTRACT[
                        "successful_repair_ceiling"
                    ]
                ),
            )
            if target_ids
            else base
        )
        final = max(0.0, pre_penalty - float(penalty["total"]))
        final_form_scores[form_id] = round(final, 6)
        form_results[form_id] = {
            "post_repair_score_before_penalty": round(base, 6),
            "successful_repair_ceiling_applied": (
                bool(target_ids) and pre_penalty < base
            ),
            "repair_penalty": penalty,
            "final_form_score": round(final, 6),
            "hard_gate_verdict": post_scores[form_id][
                "hard_gate_verdict"
            ],
        }
    final_mean = round(
        sum(final_form_scores.values()) / len(final_form_scores),
        6,
    )
    total_penalty = round(
        sum(float(item["total"]) for item in form_penalties.values())
        / len(form_penalties),
        6,
    )
    return {
        "schema_version": EXAM_REPAIR_RESULT_SCHEMA_VERSION,
        "status": "DIRECTED_REPAIR_SUCCEEDED",
        "qualification_verdict": "PASS",
        "repair_rounds": 1,
        "wrong_item_count": repair_contract["wrong_item_count"],
        "remaining_wrong_items": remaining,
        "new_wrong_items": new_wrong_items,
        "remaining_wrong_item_count": 0,
        "post_repair_score_before_penalty": round(
            sum(
                float(item["post_repair_score_before_penalty"])
                for item in form_results.values()
            )
            / len(form_results),
            6,
        ),
        "repair_penalty": {
            "forms": form_penalties,
            "wrong_item_volume": round(
                sum(
                    float(item["wrong_item_volume"])
                    for item in form_penalties.values()
                )
                / len(form_penalties),
                6,
            ),
            "issue_categories": round(
                sum(
                    float(item["issue_categories_penalty"])
                    for item in form_penalties.values()
                )
                / len(form_penalties),
                6,
            ),
            "total_mean": total_penalty,
        },
        "forms": form_results,
        "final_form_scores": final_form_scores,
        "final_score": final_mean,
        "final_min": round(min(final_form_scores.values()), 6),
        "final_gap": round(
            abs(final_form_scores["A"] - final_form_scores["B"]),
            6,
        ),
        "quality_hard_gate": "PASS",
        "provider_blind_formula": True,
        "successful_repair_score_is_final_score": True,
    }


def publish_dual_form_result(
    *,
    provider: str,
    model: str,
    parameters: Mapping[str, Any],
    forms: Mapping[str, Mapping[str, Any]],
    evidence_refs: list[str],
) -> dict[str, Any]:
    values = [forms.get(form, {}) for form in ("A", "B")]
    scored = all(item.get("status") == "SCORED" and isinstance(item.get("score"), (int, float)) for item in values)
    if scored:
        score_a, score_b = float(values[0]["score"]), float(values[1]["score"])
        raw_sum = score_a + score_b
        total = raw_sum / 2.0
        hard_gate = "PASS" if all(item.get("hard_gate_verdict") == "PASS" for item in values) else "FAIL"
        if total < 60:
            recommendation = "REJECT_AS_CARD_REVIEWER"
        elif total < 80:
            recommendation = "NOT_RECOMMENDED_AS_CARD_REVIEWER"
        elif hard_gate == "PASS":
            recommendation = "SUITABLE_AS_CARD_REVIEWER"
        else:
            recommendation = "NOT_RECOMMENDED_SAFETY_GATE_FAIL"
    else:
        score_a = score_b = raw_sum = total = None
        hard_gate = "NOT_ASSESSED"
        statuses = {str(item.get("status")) for item in values}
        if any(status.startswith("INVALID_OUTPUT") for status in statuses):
            recommendation = "REJECT_AS_CARD_REVIEWER_INVALID_OUTPUT"
        else:
            recommendation = "NO_MODEL_CAPABILITY_CONCLUSION"
    value = {
        "schema_version": DUAL_FORM_RESULT_SCHEMA_VERSION,
        "provider": provider,
        "model": model,
        "parameters": dict(parameters),
        "form_A_score": score_a,
        "form_B_score": score_b,
        "raw_sum_out_of_200": round(raw_sum, 6) if raw_sum is not None else None,
        "total_score_out_of_100": round(total, 6) if total is not None else None,
        "form_min": round(min(score_a, score_b), 6) if scored else None,
        "form_gap": round(abs(score_a - score_b), 6) if scored else None,
        "quality_hard_gate_verdict": hard_gate,
        "recommendation_code": recommendation,
        "forms": {form: dict(forms.get(form, {})) for form in ("A", "B")},
        "evidence_refs": list(evidence_refs),
        "gold_scoring_required": True,
        "gold_sent_to_subject": False,
        "gold_sent_to_provider": False,
        "capacity_is_quality_failure": False,
    }
    value["result_sha256"] = sha256(value)
    return value
