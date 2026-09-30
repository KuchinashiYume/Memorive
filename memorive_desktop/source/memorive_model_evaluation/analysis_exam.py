"""Deterministic analysis-exam audit, directed repair, and publication helpers.

This module never calls a model and never reads private Gold.  It owns the MODEL_EVALUATION
quality boundary around an MODEL_GATEWAY subject call: exhaustively inventory structural
defects, freeze the complete repair exact-set, validate one directed repair,
and publish a provider-blind dual-form adjudication.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import math
import statistics
from typing import Any

from jsonschema import Draft202012Validator

from .errors import ModelEvaluationAnalysisExamError
from .analysis_exam_protocol import score_response


ANALYSIS_EXAM_AUDIT_SCHEMA_VERSION = "model_evaluation-analysis-exam-artifact-audit-v1"
ANALYSIS_REPAIR_PACKAGE_SCHEMA_VERSION = "model_evaluation-analysis-directed-repair-package-v2"
ANALYSIS_REPAIR_MERGE_SCHEMA_VERSION = "model_evaluation-analysis-directed-repair-merge-v1"
ANALYSIS_NORMALIZATION_SCHEMA_VERSION = "model_evaluation-analysis-exam-normalization-v1"
ANALYSIS_FORM_SCORE_SCHEMA_VERSION = "model_evaluation-analysis-exam-form-score-v1"
ANALYSIS_FORM_SCORE_V2_SCHEMA_VERSION = "model_evaluation-analysis-exam-form-score-v2"
ANALYSIS_EXAM_RESULT_SCHEMA_VERSION = "model_evaluation-analysis-exam-result-v1"
ANALYSIS_EXAM_REPAIR_PACKAGE_SCHEMA_VERSION = (
    "model_evaluation-analysis-exam-directed-repair-package-v3"
)
ANALYSIS_EXAM_REPAIR_CONTRACT_SCHEMA_VERSION = (
    "model_evaluation-analysis-exam-directed-repair-contract-v3"
)
ANALYSIS_EXAM_REPAIR_MERGE_SCHEMA_VERSION = (
    "model_evaluation-analysis-exam-directed-repair-merge-v2"
)
ANALYSIS_EXAM_REPAIR_RESULT_SCHEMA_VERSION = (
    "model_evaluation-analysis-exam-directed-repair-result-v2"
)
ANALYSIS_ARTIFACTS = ("claim_map", "analysis_summary")
ANCHOR_ROLE_FIELDS = (
    "supporting_anchors",
    "contradicting_anchors",
    "qualification_anchors",
    "lineage_invalid_anchors",
    "context_only_anchors",
)
PIECEWISE_KNOTS = {
    "overall_semantic": (
        (0, 0),
        (50, 0),
        (65, 9),
        (75, 18),
        (85, 30),
        (92, 39),
        (96, 43),
        (99, 44.5),
        (100, 45),
    ),
    "hard_case": (
        (0, 0),
        (60, 0),
        (75, 5),
        (85, 10),
        (92, 15),
        (97, 18.5),
        (100, 20),
    ),
    "worst_family": (
        (0, 0),
        (50, 0),
        (65, 2),
        (75, 5),
        (85, 8),
        (92, 10),
        (97, 11.5),
        (100, 12),
    ),
    "bottom_decile": (
        (0, 0),
        (50, 0),
        (65, 1),
        (75, 3),
        (85, 5),
        (92, 6.5),
        (97, 7.5),
        (100, 8),
    ),
}
REPAIR_SCOPE_PENALTY_KNOTS = (
    (0.0, 0.0),
    (0.05, 0.5),
    (0.10, 0.8),
    (0.25, 1.5),
    (0.50, 2.0),
    (1.00, 4.0),
)
ANALYSIS_EXAM_REPAIR_PENALTY_CONTRACT = {
    "schema_version": "model_evaluation-analysis-exam-repair-penalty-v1",
    "volume_knots": [list(item) for item in REPAIR_SCOPE_PENALTY_KNOTS],
    "artifact_category_points": 0.5,
    "severity_category_points": {"major": 0.5, "critical": 1.0},
    "distinct_issue_category_points": 0.1,
    "distinct_issue_category_cap": 1.5,
    "successful_repair_ceiling": 89.99,
    "failed_repair_final_score": 0.0,
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest().upper()


def _verify_self_hash(value: Mapping[str, Any], field: str) -> None:
    observed = value.get(field)
    unsigned = dict(value)
    unsigned.pop(field, None)
    if not isinstance(observed, str) or observed != _sha256(unsigned):
        raise ModelEvaluationAnalysisExamError(f"{field.upper()}_MISMATCH")


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelEvaluationAnalysisExamError(f"{field.upper()}_MAPPING_REQUIRED")
    return value


def _form_cases(form: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[str]]:
    cases = form.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ModelEvaluationAnalysisExamError("FORM_CASES_NONEMPTY_LIST_REQUIRED")
    ids = []
    for index, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise ModelEvaluationAnalysisExamError(f"FORM_CASE_OBJECT_REQUIRED:{index}")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ModelEvaluationAnalysisExamError(f"FORM_CASE_ID_REQUIRED:{index}")
        ids.append(case_id)
    if len(ids) != len(set(ids)):
        raise ModelEvaluationAnalysisExamError("FORM_CASE_IDS_MUST_BE_UNIQUE")
    return cases, ids


def _artifact_item_schema(
    schema: Mapping[str, Any], artifact: str
) -> Mapping[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        raise ModelEvaluationAnalysisExamError("SCHEMA_PROPERTIES_REQUIRED")
    artifact_schema = properties.get(artifact)
    if not isinstance(artifact_schema, Mapping):
        raise ModelEvaluationAnalysisExamError(f"SCHEMA_ARTIFACT_REQUIRED:{artifact}")
    item_schema = artifact_schema.get("items")
    if not isinstance(item_schema, Mapping):
        raise ModelEvaluationAnalysisExamError(f"SCHEMA_ITEM_REQUIRED:{artifact}")
    return item_schema


def _defect(
    code: str,
    *,
    case_id: str | None = None,
    field: str | None = None,
    anchor: str | None = None,
    observed: Any = None,
    expected: Any = None,
    repair_action: str,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "code": code,
        "case_id": case_id,
        "field": field,
        "anchor": anchor,
        "repair_action": repair_action,
    }
    if observed is not None:
        value["observed"] = observed
    if expected is not None:
        value["expected"] = expected
    return value


def _case_anchor_ids(case: Mapping[str, Any]) -> list[str]:
    units = case.get("evidence_units")
    if not isinstance(units, list):
        raise ModelEvaluationAnalysisExamError(
            f"FORM_EVIDENCE_UNITS_LIST_REQUIRED:{case.get('case_id')}"
        )
    anchors = []
    for index, unit in enumerate(units):
        if not isinstance(unit, Mapping):
            raise ModelEvaluationAnalysisExamError(
                f"FORM_EVIDENCE_UNIT_OBJECT_REQUIRED:{case.get('case_id')}:{index}"
            )
        anchor = unit.get("anchor")
        if not isinstance(anchor, str) or not anchor:
            raise ModelEvaluationAnalysisExamError(
                f"FORM_ANCHOR_REQUIRED:{case.get('case_id')}:{index}"
            )
        anchors.append(anchor)
    if len(anchors) != len(set(anchors)):
        raise ModelEvaluationAnalysisExamError(
            f"FORM_ANCHORS_MUST_BE_UNIQUE:{case.get('case_id')}"
        )
    return anchors


def _schema_defects(
    *,
    response: Any,
    schema: Mapping[str, Any],
    artifact: str,
    rows: list[Any],
) -> tuple[list[dict[str, Any]], set[str], bool]:
    """Collect every Draft 2020-12 violation without short-circuiting."""

    try:
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(response),
            key=lambda error: (
                tuple(str(part) for part in error.absolute_path),
                str(error.validator),
                error.message,
            ),
        )
    except Exception as exc:
        raise ModelEvaluationAnalysisExamError(
            f"OUTPUT_SCHEMA_INVALID:{type(exc).__name__}:{exc}"
        ) from exc

    defects: list[dict[str, Any]] = []
    repair_ids: set[str] = set()
    unrecoverable = False
    for error in errors:
        path = list(error.absolute_path)
        # Root extras are separately classified as safe mechanical removal.
        if error.validator == "additionalProperties" and not path:
            continue
        # Row-level required/enum violations receive stable, more specific codes
        # in the explicit inventory below.  Retain nested enum violations here.
        if error.validator == "required" and len(path) == 2 and path[0] == artifact:
            continue
        if error.validator == "enum" and len(path) == 3 and path[0] == artifact:
            continue
        # Array cardinality is explained by the explicit case partition audit.
        if error.validator in ("minItems", "maxItems") and path == [artifact]:
            continue

        case_id: str | None = None
        field: str | None = None
        if len(path) >= 2 and path[0] == artifact and isinstance(path[1], int):
            row_index = path[1]
            if 0 <= row_index < len(rows) and isinstance(rows[row_index], Mapping):
                observed_case_id = rows[row_index].get("case_id")
                if isinstance(observed_case_id, str) and observed_case_id:
                    case_id = observed_case_id
            if len(path) >= 3:
                field = str(path[2])
        elif path:
            field = "/".join(str(part) for part in path)

        if case_id is None:
            unrecoverable = True
            action = "REJECT_ARTIFACT"
        else:
            repair_ids.add(case_id)
            action = "REPLACE_CASE_ROW"
        defects.append(
            _defect(
                f"SCHEMA_{str(error.validator).upper()}",
                case_id=case_id,
                field=field,
                observed={"path": path, "message": error.message},
                expected=error.validator_value,
                repair_action=action,
            )
        )
    return defects, repair_ids, unrecoverable


def audit_analysis_artifact(
    *,
    form: Mapping[str, Any],
    response: Any,
    schema: Mapping[str, Any],
    artifact: str,
) -> dict[str, Any]:
    """Return a non-short-circuit structural audit for one analysis artifact."""

    if artifact not in ANALYSIS_ARTIFACTS:
        raise ModelEvaluationAnalysisExamError(f"UNKNOWN_ANALYSIS_ARTIFACT:{artifact}")
    form = _require_mapping(form, "form")
    schema = _require_mapping(schema, "schema")
    cases, expected_ids = _form_cases(form)
    expected_set = set(expected_ids)
    case_by_id = {case["case_id"]: case for case in cases}
    item_schema = _artifact_item_schema(schema, artifact)
    required = item_schema.get("required")
    required_fields = tuple(required) if isinstance(required, list) else ()
    item_properties = item_schema.get("properties")
    item_properties = item_properties if isinstance(item_properties, Mapping) else {}

    defects: list[dict[str, Any]] = []
    mechanical_actions: list[dict[str, Any]] = []
    repair_ids: set[str] = set()
    unrecoverable = False

    if not isinstance(response, Mapping):
        defects.append(
            _defect(
                "ROOT_OBJECT_REQUIRED",
                observed=type(response).__name__,
                expected="object",
                repair_action="REJECT_ARTIFACT",
            )
        )
        rows: list[Any] = []
        unrecoverable = True
    else:
        unexpected_roots = sorted(str(key) for key in response if key != artifact)
        for field in unexpected_roots:
            defects.append(
                _defect(
                    "UNEXPECTED_ROOT_FIELD",
                    field=field,
                    repair_action="REMOVE_ROOT_FIELD",
                )
            )
            mechanical_actions.append(
                {"code": "REMOVE_UNEXPECTED_ROOT_FIELD", "field": field}
            )
        observed_rows = response.get(artifact)
        if not isinstance(observed_rows, list):
            defects.append(
                _defect(
                    "ARTIFACT_ARRAY_REQUIRED",
                    field=artifact,
                    observed=type(observed_rows).__name__,
                    expected="array",
                    repair_action="REJECT_ARTIFACT",
                )
            )
            rows = []
            unrecoverable = True
        else:
            rows = observed_rows

    rows_by_id: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    unbound_row_count = 0
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            defects.append(
                _defect(
                    "ROW_OBJECT_REQUIRED",
                    observed={"index": index, "type": type(row).__name__},
                    repair_action="REJECT_ARTIFACT",
                )
            )
            unbound_row_count += 1
            unrecoverable = True
            continue
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            defects.append(
                _defect(
                    "ROW_CASE_ID_REQUIRED",
                    observed={"index": index},
                    repair_action="REJECT_ARTIFACT",
                )
            )
            unbound_row_count += 1
            unrecoverable = True
            continue
        rows_by_id[case_id].append(row)

    schema_defects, schema_repair_ids, schema_unrecoverable = _schema_defects(
        response=response,
        schema=schema,
        artifact=artifact,
        rows=rows,
    )
    defects.extend(schema_defects)
    repair_ids.update(case_id for case_id in schema_repair_ids if case_id in expected_set)
    unrecoverable = unrecoverable or schema_unrecoverable

    unknown_ids = sorted(case_id for case_id in rows_by_id if case_id not in expected_set)
    for case_id in unknown_ids:
        defects.append(
            _defect(
                "UNKNOWN_CASE_ROW",
                case_id=case_id,
                observed=len(rows_by_id[case_id]),
                repair_action="REMOVE_UNKNOWN_CASE_ROW",
            )
        )
        mechanical_actions.append(
            {"code": "REMOVE_UNKNOWN_CASE_ROW", "case_id": case_id}
        )

    missing_ids = [case_id for case_id in expected_ids if case_id not in rows_by_id]
    for case_id in missing_ids:
        defects.append(
            _defect(
                "MISSING_CASE_ROW",
                case_id=case_id,
                observed=0,
                expected=1,
                repair_action="REPLACE_CASE_ROW",
            )
        )
        repair_ids.add(case_id)

    identical_duplicates: list[str] = []
    nonidentical_duplicates: list[str] = []
    for case_id in expected_ids:
        grouped = rows_by_id.get(case_id, [])
        if len(grouped) <= 1:
            continue
        canonical = {_canonical_bytes(row) for row in grouped}
        if len(canonical) == 1:
            identical_duplicates.append(case_id)
            defects.append(
                _defect(
                    "IDENTICAL_DUPLICATE_CASE_ROW",
                    case_id=case_id,
                    observed=len(grouped),
                    expected=1,
                    repair_action="DEDUPE_IDENTICAL_ROW",
                )
            )
            mechanical_actions.append(
                {"code": "DEDUPE_IDENTICAL_CASE_ROW", "case_id": case_id}
            )
        else:
            nonidentical_duplicates.append(case_id)
            defects.append(
                _defect(
                    "NONIDENTICAL_DUPLICATE_CASE_ROW",
                    case_id=case_id,
                    observed=len(grouped),
                    expected=1,
                    repair_action="REPLACE_CASE_ROW",
                )
            )
            repair_ids.add(case_id)

    observed_known_order = []
    seen_known = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        case_id = row.get("case_id")
        if case_id in expected_set and case_id not in seen_known:
            observed_known_order.append(case_id)
            seen_known.add(case_id)
    expected_observed_order = [case_id for case_id in expected_ids if case_id in seen_known]
    if observed_known_order != expected_observed_order:
        defects.append(
            _defect(
                "CASE_ORDER_MISMATCH",
                observed=observed_known_order,
                expected=expected_observed_order,
                repair_action="REORDER_CASE_ROWS",
            )
        )
        mechanical_actions.append({"code": "REORDER_CASE_ROWS"})

    for case_id in expected_ids:
        grouped = rows_by_id.get(case_id, [])
        if not grouped:
            continue
        row = grouped[0]
        for field in required_fields:
            if field not in row:
                defects.append(
                    _defect(
                        "REQUIRED_FIELD_MISSING",
                        case_id=case_id,
                        field=str(field),
                        repair_action="REPLACE_CASE_ROW",
                    )
                )
                repair_ids.add(case_id)
        for field, field_schema in item_properties.items():
            if field not in row or not isinstance(field_schema, Mapping):
                continue
            allowed = field_schema.get("enum")
            if isinstance(allowed, list) and row[field] not in allowed:
                defects.append(
                    _defect(
                        "ENUM_VALUE_INVALID",
                        case_id=case_id,
                        field=str(field),
                        observed=row[field],
                        expected=allowed,
                        repair_action="REPLACE_CASE_ROW",
                    )
                )
                repair_ids.add(case_id)

        if artifact != "claim_map":
            continue
        expected_anchors = _case_anchor_ids(case_by_id[case_id])
        expected_anchor_set = set(expected_anchors)
        flattened: list[str] = []
        for field in ANCHOR_ROLE_FIELDS:
            value = row.get(field)
            if not isinstance(value, list):
                defects.append(
                    _defect(
                        "ANCHOR_ROLE_ARRAY_REQUIRED",
                        case_id=case_id,
                        field=field,
                        observed=type(value).__name__,
                        expected="array",
                        repair_action="REPLACE_CASE_ROW",
                    )
                )
                repair_ids.add(case_id)
                continue
            local_counts = Counter(item for item in value if isinstance(item, str))
            for item in value:
                if not isinstance(item, str):
                    defects.append(
                        _defect(
                            "ANCHOR_VALUE_STRING_REQUIRED",
                            case_id=case_id,
                            field=field,
                            observed=type(item).__name__,
                            repair_action="REPLACE_CASE_ROW",
                        )
                    )
                    repair_ids.add(case_id)
            for anchor, count in sorted(local_counts.items()):
                if count > 1:
                    defects.append(
                        _defect(
                            "ANCHOR_DUPLICATE_WITHIN_ROLE",
                            case_id=case_id,
                            field=field,
                            anchor=anchor,
                            observed=count,
                            expected=1,
                            repair_action="REPLACE_CASE_ROW",
                        )
                    )
                    repair_ids.add(case_id)
            flattened.extend(item for item in value if isinstance(item, str))
        counts = Counter(flattened)
        for anchor in expected_anchors:
            if anchor not in counts:
                defects.append(
                    _defect(
                        "ANCHOR_ROLE_UNION_MISSING",
                        case_id=case_id,
                        anchor=anchor,
                        observed=0,
                        expected=1,
                        repair_action="REPLACE_CASE_ROW",
                    )
                )
                repair_ids.add(case_id)
        for anchor in sorted(counts):
            if anchor not in expected_anchor_set:
                defects.append(
                    _defect(
                        "UNKNOWN_ANCHOR",
                        case_id=case_id,
                        anchor=anchor,
                        repair_action="REPLACE_CASE_ROW",
                    )
                )
                repair_ids.add(case_id)
            if counts[anchor] > 1:
                defects.append(
                    _defect(
                        "ANCHOR_ROLE_OVERLAP",
                        case_id=case_id,
                        anchor=anchor,
                        observed=counts[anchor],
                        expected=1,
                        repair_action="REPLACE_CASE_ROW",
                    )
                )
                repair_ids.add(case_id)

    repair_exact_set = [case_id for case_id in expected_ids if case_id in repair_ids]
    if not defects:
        status = "PASS"
    elif unrecoverable:
        status = "FAIL_UNRECOVERABLE"
    elif repair_exact_set:
        status = "REPAIRABLE"
    else:
        status = "MECHANICAL_RECOVERY"
    structure_counts = {
        "strict_invalid_required_artifacts": 0 if status == "PASS" else 1,
        "mechanically_affected_cases": len(repair_exact_set),
        "identical_duplicate_case_rows": sum(
            max(0, len(rows_by_id[case_id]) - 1)
            for case_id in identical_duplicates
        ),
        "out_of_enum_qualification_codes": sum(
            1
            for item in defects
            if item["code"] in ("ENUM_VALUE_INVALID", "SCHEMA_ENUM")
            and item.get("field") == "qualification_codes"
        ),
        "unknown_anchors": sum(
            1 for item in defects if item["code"] == "UNKNOWN_ANCHOR"
        ),
        "recoverable_root_aliases": sum(
            1 for item in defects if item["code"] == "UNEXPECTED_ROOT_FIELD"
        ),
    }
    result = {
        "schema_version": ANALYSIS_EXAM_AUDIT_SCHEMA_VERSION,
        "artifact": artifact,
        "status": status,
        "exhaustive_non_short_circuit": True,
        "form_id": form.get("form"),
        "expected_case_count": len(expected_ids),
        "observed_row_count": len(rows),
        "observed_bound_row_count": len(rows) - unbound_row_count,
        "observed_unique_known_case_count": len(
            [case_id for case_id in rows_by_id if case_id in expected_set]
        ),
        "missing_case_ids": missing_ids,
        "unknown_case_ids": unknown_ids,
        "identical_duplicate_case_ids": identical_duplicates,
        "nonidentical_duplicate_case_ids": nonidentical_duplicates,
        "repair_exact_set": repair_exact_set,
        "mechanical_actions": mechanical_actions,
        "structure_counts": structure_counts,
        "defect_count": len(defects),
        "defects": defects,
        "form_sha256": _sha256(form),
        "response_sha256": _sha256(response),
        "schema_sha256": _sha256(schema),
        "gold_read_or_required": False,
    }
    result["audit_sha256"] = _sha256(result)
    return result


def normalize_analysis_artifact(
    *,
    form: Mapping[str, Any],
    response: Mapping[str, Any],
    schema: Mapping[str, Any],
    artifact: str,
) -> dict[str, Any]:
    """Apply only deterministic, no-model recovery to a mechanically invalid artifact."""

    form = _require_mapping(form, "form")
    response = _require_mapping(response, "response")
    schema = _require_mapping(schema, "schema")
    source_audit = audit_analysis_artifact(
        form=form,
        response=response,
        schema=schema,
        artifact=artifact,
    )
    if source_audit["status"] == "PASS":
        raise ModelEvaluationAnalysisExamError("NORMALIZATION_NOT_REQUIRED")
    if source_audit["status"] != "MECHANICAL_RECOVERY":
        raise ModelEvaluationAnalysisExamError(
            f"NORMALIZATION_NOT_MECHANICAL:{source_audit['status']}"
        )
    rows = response.get(artifact)
    if not isinstance(rows, list):
        raise ModelEvaluationAnalysisExamError("NORMALIZATION_ARTIFACT_ARRAY_REQUIRED")
    _, expected_ids = _form_cases(form)
    expected_set = set(expected_ids)
    first_by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ModelEvaluationAnalysisExamError("NORMALIZATION_ROW_OBJECT_REQUIRED")
        case_id = row.get("case_id")
        if isinstance(case_id, str) and case_id in expected_set:
            first_by_id.setdefault(case_id, row)
    normalized = {
        artifact: [deepcopy(dict(first_by_id[case_id])) for case_id in expected_ids]
    }
    normalized_audit = audit_analysis_artifact(
        form=form,
        response=normalized,
        schema=schema,
        artifact=artifact,
    )
    if normalized_audit["status"] != "PASS":
        raise ModelEvaluationAnalysisExamError(
            f"NORMALIZED_RESPONSE_NOT_VALID:{normalized_audit['status']}"
        )
    result = {
        "schema_version": ANALYSIS_NORMALIZATION_SCHEMA_VERSION,
        "status": "PASS",
        "artifact": artifact,
        "source_audit": source_audit,
        "normalized_response": normalized,
        "normalized_audit": normalized_audit,
        "mechanical_actions": [
            "DROP_UNKNOWN_ROWS",
            "DEDUPE_IDENTICAL_ROWS",
            "REORDER_TO_FORM_ORDER",
            "DROP_UNEXPECTED_ROOT_FIELDS",
        ],
        "structure_counts": deepcopy(dict(source_audit["structure_counts"])),
        "repair_turns": 0,
        "model_api_call_required": False,
    }
    result["normalization_sha256"] = _sha256(result)
    return result


def _subset_schema(
    schema: Mapping[str, Any], artifact: str, count: int
) -> dict[str, Any]:
    value = deepcopy(dict(schema))
    artifact_schema = value["properties"][artifact]
    artifact_schema["minItems"] = count
    artifact_schema["maxItems"] = count
    return value


def build_directed_repair_package(
    *,
    form: Mapping[str, Any],
    original_response: Mapping[str, Any],
    audit: Mapping[str, Any],
    schema: Mapping[str, Any],
    artifact: str,
) -> dict[str, Any]:
    """Freeze a complete repair exact-set and a no-Gold targeted prompt.

    The v2 contract adopts the useful part of the Core atomic repair flow:
    every target carries its current row plus the machine findings that caused
    it to enter the repair whitelist.  The subject still returns the ordinary
    artifact row shape, receives no Gold, and gets only one repair turn.
    """

    if artifact not in ANALYSIS_ARTIFACTS:
        raise ModelEvaluationAnalysisExamError(f"UNKNOWN_ANALYSIS_ARTIFACT:{artifact}")
    form = _require_mapping(form, "form")
    original_response = _require_mapping(original_response, "original_response")
    audit = _require_mapping(audit, "audit")
    schema = _require_mapping(schema, "schema")
    if audit.get("schema_version") != ANALYSIS_EXAM_AUDIT_SCHEMA_VERSION:
        raise ModelEvaluationAnalysisExamError("AUDIT_SCHEMA_VERSION_MISMATCH")
    _verify_self_hash(audit, "audit_sha256")
    if audit.get("artifact") != artifact:
        raise ModelEvaluationAnalysisExamError("AUDIT_ARTIFACT_MISMATCH")
    if audit.get("exhaustive_non_short_circuit") is not True:
        raise ModelEvaluationAnalysisExamError("AUDIT_NOT_EXHAUSTIVE")
    if audit.get("status") != "REPAIRABLE":
        raise ModelEvaluationAnalysisExamError(f"AUDIT_NOT_REPAIRABLE:{audit.get('status')}")
    if audit.get("form_sha256") != _sha256(form):
        raise ModelEvaluationAnalysisExamError("AUDIT_FORM_HASH_MISMATCH")
    if audit.get("schema_sha256") != _sha256(schema):
        raise ModelEvaluationAnalysisExamError("AUDIT_SCHEMA_HASH_MISMATCH")
    if audit.get("response_sha256") != _sha256(original_response):
        raise ModelEvaluationAnalysisExamError("AUDIT_RESPONSE_HASH_MISMATCH")
    if audit.get("gold_read_or_required") is not False:
        raise ModelEvaluationAnalysisExamError("AUDIT_GOLD_BOUNDARY_INVALID")
    exact_set = audit.get("repair_exact_set")
    if not isinstance(exact_set, list) or not exact_set:
        raise ModelEvaluationAnalysisExamError("REPAIR_EXACT_SET_NONEMPTY_REQUIRED")
    cases, expected_ids = _form_cases(form)
    if any(case_id not in expected_ids for case_id in exact_set):
        raise ModelEvaluationAnalysisExamError("REPAIR_EXACT_SET_UNKNOWN_CASE")
    expected_order = [case_id for case_id in expected_ids if case_id in set(exact_set)]
    if exact_set != expected_order or len(exact_set) != len(set(exact_set)):
        raise ModelEvaluationAnalysisExamError("REPAIR_EXACT_SET_ORDER_OR_UNIQUENESS_INVALID")
    exact_set_lookup = set(exact_set)
    original_rows = original_response.get(artifact)
    if not isinstance(original_rows, list):
        raise ModelEvaluationAnalysisExamError("ORIGINAL_ARTIFACT_ARRAY_REQUIRED")
    original_by_id: dict[str, Mapping[str, Any]] = {}
    for row in original_rows:
        if not isinstance(row, Mapping):
            continue
        case_id = row.get("case_id")
        if isinstance(case_id, str) and case_id in expected_ids:
            original_by_id.setdefault(case_id, row)
    case_by_id = {case["case_id"]: case for case in cases}
    findings_by_case: dict[str, list[dict[str, Any]]] = {
        case_id: [] for case_id in exact_set
    }
    for raw_finding in audit.get("defects") or []:
        if not isinstance(raw_finding, Mapping):
            continue
        case_id = raw_finding.get("case_id")
        if case_id not in exact_set_lookup:
            continue
        finding_body = {
            key: deepcopy(raw_finding.get(key))
            for key in (
                "case_id",
                "code",
                "field",
                "anchor",
                "expected",
                "observed",
                "repair_action",
            )
        }
        findings_by_case[case_id].append(
            {
                "finding_id": f"F-{_sha256(finding_body)[:16]}",
                **finding_body,
            }
        )
    if any(not findings_by_case[case_id] for case_id in exact_set):
        raise ModelEvaluationAnalysisExamError("REPAIR_TARGET_FINDINGS_INCOMPLETE")
    repair_targets = []
    for case_id in exact_set:
        current_row = original_by_id.get(case_id)
        repair_targets.append(
            {
                "case_id": case_id,
                "source_case_sha256": _sha256(case_by_id[case_id]),
                "current_row": deepcopy(dict(current_row))
                if current_row is not None
                else None,
                "current_row_sha256": _sha256(current_row)
                if current_row is not None
                else None,
                "findings": findings_by_case[case_id],
                "allowed_actions": ["REPLACE_CASE_ROW"],
                "requires_full_row": True,
            }
        )
    repair_form = deepcopy(dict(form))
    repair_form["cases"] = [
        deepcopy(dict(case)) for case in cases if case["case_id"] in exact_set_lookup
    ]
    repair_schema = _subset_schema(schema, artifact, len(exact_set))
    contract = {
        "schema_version": ANALYSIS_REPAIR_PACKAGE_SCHEMA_VERSION,
        "artifact": artifact,
        "repair_turn_limit": 1,
        "repair_exact_set": list(exact_set),
        "repair_case_count": len(exact_set),
        "source_audit_sha256": audit.get("audit_sha256") or _sha256(audit),
        "source_form_sha256": _sha256(form),
        "source_response_sha256": audit.get("response_sha256"),
        "source_schema_sha256": _sha256(schema),
        "repair_form_sha256": _sha256(repair_form),
        "repair_schema_sha256": _sha256(repair_schema),
        "repair_targets_sha256": _sha256(repair_targets),
        "repair_targets": repair_targets,
        "requires_exact_case_id_order": True,
        "requires_full_post_merge_reaudit": True,
        "subject_receives_answer_key": False,
        "source_structure_counts": deepcopy(dict(audit.get("structure_counts") or {})),
    }
    contract["contract_sha256"] = _sha256(contract)
    prompt = (
        "You are completing one directed repair turn for a synthetic analysis examination. "
        "Return only the requested artifact rows. Replace each listed case in full; do not return any other case. "
        "For each target, use current_row as the starting point and fix every listed machine finding; "
        "then recheck the complete row so that correcting one finding does not create another. "
        "For claim_map, classify every supplied anchor exactly once across the five disjoint role arrays. "
        "Mechanically verify row count, case order, required fields, enum values, anchor union, and role disjointness.\n\n"
        "REPAIR EXACT SET:\n"
        + json.dumps(exact_set, ensure_ascii=False, separators=(",", ":"))
        + "\n\nSTRUCTURED REPAIR TARGETS (machine findings only; no answer key):\n"
        + json.dumps(repair_targets, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n\nSYNTHETIC REPAIR FORM:\n"
        + json.dumps(repair_form, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n\nOUTPUT SCHEMA:\n"
        + json.dumps(repair_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    result = {
        "schema_version": ANALYSIS_REPAIR_PACKAGE_SCHEMA_VERSION,
        "status": "READY",
        "contract": contract,
        "repair_form": repair_form,
        "repair_schema": repair_schema,
        "prompt": prompt,
    }
    result["package_sha256"] = _sha256(result)
    return result


def merge_directed_repair(
    *,
    form: Mapping[str, Any],
    original_response: Mapping[str, Any],
    repair_response: Mapping[str, Any],
    schema: Mapping[str, Any],
    artifact: str,
    repair_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one exact repair and rebuild the complete artifact in form order."""

    form = _require_mapping(form, "form")
    original_response = _require_mapping(original_response, "original_response")
    repair_response = _require_mapping(repair_response, "repair_response")
    schema = _require_mapping(schema, "schema")
    repair_contract = _require_mapping(repair_contract, "repair_contract")
    if repair_contract.get("schema_version") != ANALYSIS_REPAIR_PACKAGE_SCHEMA_VERSION:
        raise ModelEvaluationAnalysisExamError("REPAIR_CONTRACT_SCHEMA_VERSION_MISMATCH")
    _verify_self_hash(repair_contract, "contract_sha256")
    if repair_contract.get("artifact") != artifact:
        raise ModelEvaluationAnalysisExamError("REPAIR_CONTRACT_ARTIFACT_MISMATCH")
    if repair_contract.get("source_form_sha256") != _sha256(form):
        raise ModelEvaluationAnalysisExamError("REPAIR_CONTRACT_FORM_HASH_MISMATCH")
    if repair_contract.get("source_schema_sha256") != _sha256(schema):
        raise ModelEvaluationAnalysisExamError("REPAIR_CONTRACT_SCHEMA_HASH_MISMATCH")
    if repair_contract.get("source_response_sha256") != _sha256(original_response):
        raise ModelEvaluationAnalysisExamError("REPAIR_CONTRACT_RESPONSE_HASH_MISMATCH")
    exact_set = repair_contract.get("repair_exact_set")
    if not isinstance(exact_set, list) or not exact_set:
        raise ModelEvaluationAnalysisExamError("REPAIR_CONTRACT_EXACT_SET_INVALID")
    repair_targets = repair_contract.get("repair_targets")
    if (
        not isinstance(repair_targets, list)
        or [
            target.get("case_id") if isinstance(target, Mapping) else None
            for target in repair_targets
        ]
        != exact_set
        or repair_contract.get("repair_targets_sha256")
        != _sha256(repair_targets)
    ):
        raise ModelEvaluationAnalysisExamError("REPAIR_CONTRACT_TARGETS_INVALID")
    repair_rows = repair_response.get(artifact)
    if not isinstance(repair_rows, list):
        raise ModelEvaluationAnalysisExamError("REPAIR_ARTIFACT_ARRAY_REQUIRED")
    repair_ids = [
        row.get("case_id") if isinstance(row, Mapping) else None
        for row in repair_rows
    ]
    if repair_ids != exact_set:
        raise ModelEvaluationAnalysisExamError(
            f"REPAIR_CASE_ID_EXACT_SET_MISMATCH:{repair_ids!r}!={exact_set!r}"
        )
    cases, expected_ids = _form_cases(form)
    exact_lookup = set(exact_set)
    original_rows = original_response.get(artifact)
    if not isinstance(original_rows, list):
        raise ModelEvaluationAnalysisExamError("ORIGINAL_ARTIFACT_ARRAY_REQUIRED")
    original_by_id: dict[str, Mapping[str, Any]] = {}
    for row in original_rows:
        if not isinstance(row, Mapping):
            continue
        case_id = row.get("case_id")
        if isinstance(case_id, str) and case_id in expected_ids:
            original_by_id.setdefault(case_id, row)
    case_by_id = {case["case_id"]: case for case in cases}
    for target in repair_targets:
        case_id = target["case_id"]
        current_row = original_by_id.get(case_id)
        expected_row = dict(current_row) if current_row is not None else None
        expected_row_sha = _sha256(current_row) if current_row is not None else None
        if (
            target.get("source_case_sha256") != _sha256(case_by_id[case_id])
            or target.get("current_row") != expected_row
            or target.get("current_row_sha256") != expected_row_sha
            or target.get("allowed_actions") != ["REPLACE_CASE_ROW"]
            or target.get("requires_full_row") is not True
            or not isinstance(target.get("findings"), list)
            or not target["findings"]
        ):
            raise ModelEvaluationAnalysisExamError("REPAIR_CONTRACT_TARGET_BINDING_MISMATCH")
    subset_form = deepcopy(dict(form))
    subset_form["cases"] = [
        deepcopy(dict(case)) for case in cases if case["case_id"] in exact_lookup
    ]
    subset_schema = _subset_schema(schema, artifact, len(exact_set))
    if repair_contract.get("repair_form_sha256") != _sha256(subset_form):
        raise ModelEvaluationAnalysisExamError("REPAIR_CONTRACT_REPAIR_FORM_HASH_MISMATCH")
    if repair_contract.get("repair_schema_sha256") != _sha256(subset_schema):
        raise ModelEvaluationAnalysisExamError("REPAIR_CONTRACT_REPAIR_SCHEMA_HASH_MISMATCH")
    repair_audit = audit_analysis_artifact(
        form=subset_form,
        response=repair_response,
        schema=subset_schema,
        artifact=artifact,
    )
    if repair_audit["status"] != "PASS":
        raise ModelEvaluationAnalysisExamError(
            f"REPAIR_RESPONSE_NOT_VALID:{repair_audit['status']}:{repair_audit['defect_count']}"
        )

    repair_by_id = {row["case_id"]: row for row in repair_rows}
    merged_rows = []
    for case_id in expected_ids:
        if case_id in exact_lookup:
            row = repair_by_id.get(case_id)
        else:
            row = original_by_id.get(case_id)
        if row is None:
            raise ModelEvaluationAnalysisExamError(f"MERGE_CASE_ROW_MISSING:{case_id}")
        merged_rows.append(deepcopy(dict(row)))
    merged_response = {artifact: merged_rows}
    merged_audit = audit_analysis_artifact(
        form=form,
        response=merged_response,
        schema=schema,
        artifact=artifact,
    )
    if merged_audit["status"] != "PASS":
        raise ModelEvaluationAnalysisExamError(
            f"MERGED_RESPONSE_NOT_VALID:{merged_audit['status']}:{merged_audit['defect_count']}"
        )
    result = {
        "schema_version": ANALYSIS_REPAIR_MERGE_SCHEMA_VERSION,
        "status": "PASS",
        "artifact": artifact,
        "repair_exact_set": list(exact_set),
        "repair_audit": repair_audit,
        "merged_response": merged_response,
        "merged_audit": merged_audit,
        "mechanical_actions": [
            "DROP_UNKNOWN_ROWS",
            "DEDUPE_IDENTICAL_ROWS",
            "REORDER_TO_FORM_ORDER",
        ],
        "structure_counts": deepcopy(
            dict(repair_contract.get("source_structure_counts") or {})
        ),
        "repair_turns": 1,
    }
    result["merge_sha256"] = _sha256(result)
    return result


def _interpolate(
    value: float, knots: tuple[tuple[float, float], ...]
) -> float:
    if value <= knots[0][0]:
        return float(knots[0][1])
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if value <= x1:
            return float(y0) + (value - x0) * (float(y1) - float(y0)) / (
                x1 - x0
            )
    return float(knots[-1][1])


def score_analysis_form(
    *,
    raw_scoring: Mapping[str, Any],
    structure_counts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the frozen provider-blind piecewise analysis-exam score."""

    raw_scoring = _require_mapping(raw_scoring, "raw_scoring")
    structure = dict(structure_counts or {})
    details = raw_scoring.get("details")
    family_scores = raw_scoring.get("family_scores")
    source_score = raw_scoring.get("form_score")
    if not isinstance(details, list) or not details:
        raise ModelEvaluationAnalysisExamError("RAW_SCORING_DETAILS_REQUIRED")
    if not isinstance(family_scores, Mapping) or not family_scores:
        raise ModelEvaluationAnalysisExamError("RAW_SCORING_FAMILY_SCORES_REQUIRED")
    if isinstance(source_score, bool) or not isinstance(source_score, (int, float)):
        raise ModelEvaluationAnalysisExamError("RAW_SCORING_FORM_SCORE_REQUIRED")

    case_scores: list[float] = []
    hard_scores: list[float] = []
    for index, item in enumerate(details):
        if not isinstance(item, Mapping):
            raise ModelEvaluationAnalysisExamError(f"RAW_SCORING_DETAIL_OBJECT_REQUIRED:{index}")
        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ModelEvaluationAnalysisExamError(f"RAW_SCORING_DETAIL_SCORE_REQUIRED:{index}")
        score = float(score)
        if not 0 <= score <= 100:
            raise ModelEvaluationAnalysisExamError(f"RAW_SCORING_DETAIL_SCORE_RANGE:{index}")
        case_scores.append(score)
        if item.get("difficulty") == "hard":
            hard_scores.append(score)
    if not hard_scores:
        raise ModelEvaluationAnalysisExamError("RAW_SCORING_HARD_CASES_REQUIRED")
    normalized_family_scores: list[float] = []
    for family, value in family_scores.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ModelEvaluationAnalysisExamError(f"RAW_SCORING_FAMILY_SCORE_REQUIRED:{family}")
        numeric = float(value)
        if not 0 <= numeric <= 100:
            raise ModelEvaluationAnalysisExamError(f"RAW_SCORING_FAMILY_SCORE_RANGE:{family}")
        normalized_family_scores.append(numeric)
    source_score = float(source_score)
    if not 0 <= source_score <= 100:
        raise ModelEvaluationAnalysisExamError("RAW_SCORING_FORM_SCORE_RANGE")

    count_fields = (
        "strict_invalid_required_artifacts",
        "mechanically_affected_cases",
        "identical_duplicate_case_rows",
        "out_of_enum_qualification_codes",
        "unknown_anchors",
        "recoverable_root_aliases",
    )
    counts: dict[str, int] = {}
    for field in count_fields:
        value = structure.get(field, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ModelEvaluationAnalysisExamError(f"STRUCTURE_COUNT_INVALID:{field}")
        counts[field] = value
    critical = bool(raw_scoring.get("critical_any", False))
    bottom_n = max(1, math.ceil(len(case_scores) * 0.10))
    signals = {
        "overall_semantic": source_score,
        "hard_case": statistics.mean(hard_scores),
        "worst_family": min(normalized_family_scores),
        "bottom_decile": statistics.mean(sorted(case_scores)[:bottom_n]),
    }
    structure_base = {0: 15.0, 1: 6.0, 2: 2.0}.get(
        counts["strict_invalid_required_artifacts"], 0.0
    )
    deductions = {
        "mechanically_affected_cases": min(
            4.0, 0.5 * counts["mechanically_affected_cases"]
        ),
        "identical_duplicate_case_rows": min(
            4.0, 1.0 * counts["identical_duplicate_case_rows"]
        ),
        "out_of_enum_qualification_codes": min(
            6.0, 2.0 * counts["out_of_enum_qualification_codes"]
        ),
        "unknown_anchors": min(5.0, 1.0 * counts["unknown_anchors"]),
        "recoverable_root_aliases": min(
            4.0, 1.0 * counts["recoverable_root_aliases"]
        ),
    }
    components = {
        key: _interpolate(value, PIECEWISE_KNOTS[key])
        for key, value in signals.items()
    }
    components["strict_structure"] = max(
        0.0, structure_base - sum(deductions.values())
    )
    recovery_used = any(counts.values())
    frontier_eligible = (
        not recovery_used
        and not critical
        and signals["overall_semantic"] >= 95
        and signals["hard_case"] >= 95
        and signals["worst_family"] >= 90
        and signals["bottom_decile"] >= 85
    )
    score_before_caps = sum(components.values())
    score = score_before_caps
    caps: list[str] = []
    if critical or recovery_used:
        score = min(score, 59.0)
        caps.append("CRITICAL_OR_RECOVERY_CAP_59")
    if not frontier_eligible:
        score = min(score, 89.0)
        caps.append("NOT_FRONTIER_ELIGIBLE_CAP_89")
    result = {
        "schema_version": ANALYSIS_FORM_SCORE_SCHEMA_VERSION,
        "status": "SCORED",
        "score": round(score, 2),
        "source_form_score": round(source_score, 4),
        "signals": {key: round(value, 4) for key, value in signals.items()},
        "components": {key: round(value, 4) for key, value in components.items()},
        "bottom_decile_case_count": bottom_n,
        "structure_counts": counts,
        "structure_base": structure_base,
        "structure_deductions": {
            key: round(value, 4) for key, value in deductions.items()
        },
        "recovery_used": recovery_used,
        "critical_scientific_failure": critical,
        "frontier_90_eligible": frontier_eligible,
        "score_before_caps": round(score_before_caps, 4),
        "applied_caps": caps,
        "provider_blind_formula": True,
    }
    result["form_score_sha256"] = _sha256(result)
    return result


def score_analysis_form_v2(
    *,
    raw_scoring: Mapping[str, Any],
    structure_counts: Mapping[str, Any] | None = None,
    repair_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score successful directed repair by volume and severity, never by a blanket cap.

    The v1 result remains reproducible evidence.  V2 reuses its semantic signals and
    replaces only the structure/recovery treatment.  Scientific critical failures
    remain capped at 59; any successful repair remains ineligible for 90+.
    """

    legacy = score_analysis_form(
        raw_scoring=raw_scoring,
        structure_counts=structure_counts,
    )
    counts = dict(legacy["structure_counts"])
    structural_recovery_observed = any(counts.values())
    metric_fields = {
        "form_case_count",
        "directed_repair_artifact_count",
        "directed_repair_case_count",
        "mechanical_recovery_artifact_count",
        "mechanical_recovery_case_count",
        "repair_turns",
        "all_repairs_succeeded",
    }
    if repair_metrics is None:
        if structural_recovery_observed:
            raise ModelEvaluationAnalysisExamError("REPAIR_METRICS_REQUIRED_FOR_RECOVERY")
        metrics = {
            "form_case_count": len(raw_scoring["details"]),
            "directed_repair_artifact_count": 0,
            "directed_repair_case_count": 0,
            "mechanical_recovery_artifact_count": 0,
            "mechanical_recovery_case_count": 0,
            "repair_turns": 0,
            "all_repairs_succeeded": True,
        }
    else:
        metrics = dict(_require_mapping(repair_metrics, "repair_metrics"))
        missing = sorted(metric_fields - set(metrics))
        unknown = sorted(set(metrics) - metric_fields)
        if missing or unknown:
            raise ModelEvaluationAnalysisExamError(
                f"REPAIR_METRICS_FIELDS_INVALID:missing={missing}:unknown={unknown}"
            )
        for field in metric_fields - {"all_repairs_succeeded"}:
            value = metrics[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ModelEvaluationAnalysisExamError(f"REPAIR_METRIC_NONNEGATIVE_INT_REQUIRED:{field}")
        if metrics["form_case_count"] <= 0:
            raise ModelEvaluationAnalysisExamError("REPAIR_METRIC_FORM_CASE_COUNT_POSITIVE")
        if not isinstance(metrics["all_repairs_succeeded"], bool):
            raise ModelEvaluationAnalysisExamError("REPAIR_METRIC_SUCCESS_BOOL_REQUIRED")
        if not metrics["all_repairs_succeeded"]:
            raise ModelEvaluationAnalysisExamError("UNSUCCESSFUL_REPAIR_MUST_NOT_USE_V2_SCORED_PATH")
        if metrics["directed_repair_artifact_count"] > len(ANALYSIS_ARTIFACTS):
            raise ModelEvaluationAnalysisExamError("REPAIR_ARTIFACT_COUNT_EXCEEDS_TOPOLOGY")
        if metrics["mechanical_recovery_artifact_count"] > len(ANALYSIS_ARTIFACTS):
            raise ModelEvaluationAnalysisExamError("MECHANICAL_ARTIFACT_COUNT_EXCEEDS_TOPOLOGY")
        if metrics["repair_turns"] != metrics["directed_repair_artifact_count"]:
            raise ModelEvaluationAnalysisExamError("REPAIR_TURNS_ARTIFACT_COUNT_MISMATCH")
        for field in (
            "directed_repair_case_count",
            "mechanical_recovery_case_count",
        ):
            if metrics[field] > metrics["form_case_count"]:
                raise ModelEvaluationAnalysisExamError(f"REPAIR_CASE_COUNT_EXCEEDS_FORM:{field}")

    recovery_used = structural_recovery_observed or any(
        metrics[field]
        for field in (
            "directed_repair_artifact_count",
            "directed_repair_case_count",
            "mechanical_recovery_artifact_count",
            "mechanical_recovery_case_count",
            "repair_turns",
        )
    )
    if recovery_used and repair_metrics is None:
        raise ModelEvaluationAnalysisExamError("REPAIR_METRICS_REQUIRED_FOR_RECOVERY")
    affected_cases = max(
        metrics["directed_repair_case_count"],
        metrics["mechanical_recovery_case_count"],
        min(metrics["form_case_count"], counts["mechanically_affected_cases"]),
    )
    affected_ratio = affected_cases / metrics["form_case_count"]
    penalties = {
        "directed_repair_artifacts": min(
            3.0, 1.5 * metrics["directed_repair_artifact_count"]
        ),
        "mechanical_recovery_artifacts": min(
            1.5, 0.75 * metrics["mechanical_recovery_artifact_count"]
        ),
        "affected_case_scope": _interpolate(
            affected_ratio, REPAIR_SCOPE_PENALTY_KNOTS
        ),
        "strict_invalid_required_artifacts": min(
            3.0, 1.0 * counts["strict_invalid_required_artifacts"]
        ),
        "identical_duplicate_case_rows": min(
            2.0, 0.5 * counts["identical_duplicate_case_rows"]
        ),
        "out_of_enum_qualification_codes": min(
            3.0, 1.0 * counts["out_of_enum_qualification_codes"]
        ),
        "unknown_anchors": min(3.0, 0.5 * counts["unknown_anchors"]),
        "recoverable_root_aliases": min(
            2.0, 0.75 * counts["recoverable_root_aliases"]
        ),
    }
    semantic_components = {
        key: float(value)
        for key, value in legacy["components"].items()
        if key != "strict_structure"
    }
    components = {
        **semantic_components,
        "strict_structure": max(0.0, 15.0 - sum(penalties.values())),
    }
    signals = dict(legacy["signals"])
    critical = bool(legacy["critical_scientific_failure"])
    frontier_eligible = (
        not recovery_used
        and not critical
        and signals["overall_semantic"] >= 95
        and signals["hard_case"] >= 95
        and signals["worst_family"] >= 90
        and signals["bottom_decile"] >= 85
    )
    score_before_caps = sum(components.values())
    score = score_before_caps
    caps: list[str] = []
    if critical:
        score = min(score, 59.0)
        caps.append("CRITICAL_SCIENTIFIC_FAILURE_CAP_59")
    if not frontier_eligible:
        score = min(score, 89.0)
        caps.append("NOT_FRONTIER_ELIGIBLE_CAP_89")
    result = {
        "schema_version": ANALYSIS_FORM_SCORE_V2_SCHEMA_VERSION,
        "status": "SCORED",
        "score": round(score, 2),
        "source_form_score": legacy["source_form_score"],
        "signals": signals,
        "components": {key: round(value, 4) for key, value in components.items()},
        "bottom_decile_case_count": legacy["bottom_decile_case_count"],
        "structure_counts": counts,
        "structure_base": 15.0,
        "repair_metrics": metrics,
        "affected_case_count": affected_cases,
        "affected_case_ratio": round(affected_ratio, 6),
        "repair_penalties": {
            key: round(value, 4) for key, value in penalties.items()
        },
        "recovery_used": recovery_used,
        "critical_scientific_failure": critical,
        "frontier_90_eligible": frontier_eligible,
        "score_before_caps": round(score_before_caps, 4),
        "applied_caps": caps,
        "legacy_v1_score": legacy["score"],
        "score_change_from_v1": round(score - float(legacy["score"]), 2),
        "provider_blind_formula": True,
        "successful_repair_is_never_blanket_capped_59": True,
        "unsuccessful_repair_score_path": "ZERO_OUTSIDE_THIS_FUNCTION",
    }
    result["form_score_sha256"] = _sha256(result)
    return result


def _analysis_exam_wrong_details(
    score: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    details = score.get("details")
    if not isinstance(details, list):
        raise ModelEvaluationAnalysisExamError("EXAM_SCORE_DETAILS_REQUIRED")
    wrong: list[Mapping[str, Any]] = []
    for index, detail in enumerate(details):
        if not isinstance(detail, Mapping):
            raise ModelEvaluationAnalysisExamError(
                f"EXAM_SCORE_DETAIL_MAPPING_REQUIRED:{index}"
            )
        value = detail.get("score")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ModelEvaluationAnalysisExamError(
                f"EXAM_SCORE_DETAIL_VALUE_REQUIRED:{index}"
            )
        if float(value) < 100.0:
            wrong.append(detail)
    return wrong


def _analysis_exam_issue_metadata(
    detail: Mapping[str, Any],
) -> tuple[list[str], list[str], list[str], str]:
    axes = detail.get("axes")
    if not isinstance(axes, Mapping):
        raise ModelEvaluationAnalysisExamError("EXAM_SCORE_DETAIL_AXES_REQUIRED")
    axis_contract = {
        "literal_fidelity": (
            20.0,
            "LITERAL_FIDELITY",
            ("claim_map",),
        ),
        "evidence_scope_coverage": (
            25.0,
            "ANCHOR_ROLE_ASSIGNMENT",
            ("claim_map",),
        ),
        "cross_artifact_consistency": (
            20.0,
            "CROSS_ARTIFACT_OR_SCOPE_CONSISTENCY",
            ("claim_map", "analysis_summary"),
        ),
        "scientific_plausibility": (
            25.0,
            "SCIENTIFIC_PLAUSIBILITY_OR_QUALIFICATION",
            ("claim_map",),
        ),
        "lineage_freshness": (
            10.0,
            "LINEAGE_FRESHNESS",
            ("claim_map",),
        ),
    }
    issue_codes: set[str] = set()
    failed_fields: set[str] = set()
    artifact_categories: set[str] = set()
    for axis, (maximum, issue_code, artifacts) in axis_contract.items():
        observed = axes.get(axis)
        if isinstance(observed, bool) or not isinstance(
            observed, (int, float)
        ):
            raise ModelEvaluationAnalysisExamError(
                f"EXAM_SCORE_DETAIL_AXIS_VALUE_REQUIRED:{axis}"
            )
        if float(observed) < maximum:
            issue_codes.add(issue_code)
            failed_fields.add(axis)
            artifact_categories.update(artifacts)
    critical_reasons = {
        item
        for item in (detail.get("critical_reasons") or [])
        if isinstance(item, str) and item
    }
    issue_codes.update(critical_reasons)
    if not issue_codes:
        issue_codes.add("CASE_NOT_FULLY_CORRECT")
        failed_fields.add("complete_case_row")
        artifact_categories.update(ANALYSIS_ARTIFACTS)
    severity = "critical" if critical_reasons else "major"
    return (
        sorted(issue_codes),
        sorted(failed_fields),
        sorted(artifact_categories),
        severity,
    )


def _analysis_exam_repair_schema(
    schema: Mapping[str, Any],
    exact_sets: Mapping[str, list[str]],
) -> dict[str, Any]:
    source_properties = _require_mapping(
        schema.get("properties"), "schema.properties"
    )
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
        form_key = f"form_{form_id}"
        form_properties: dict[str, Any] = {}
        for artifact in ANALYSIS_ARTIFACTS:
            source_artifact = _require_mapping(
                source_properties.get(artifact),
                f"schema.properties.{artifact}",
            )
            row_schema = deepcopy(
                dict(
                    _require_mapping(
                        source_artifact.get("items"),
                        f"schema.properties.{artifact}.items",
                    )
                )
            )
            row_properties = row_schema.get("properties")
            if not isinstance(row_properties, dict):
                raise ModelEvaluationAnalysisExamError(
                    f"SCHEMA_ROW_PROPERTIES_REQUIRED:{artifact}"
                )
            row_properties["case_id"] = {
                "type": "string",
                "enum": case_ids,
            }
            form_properties[artifact] = {
                "type": "array",
                "minItems": len(case_ids),
                "maxItems": len(case_ids),
                "items": row_schema,
            }
        root["required"].append(form_key)
        root["properties"][form_key] = {
            "type": "object",
            "additionalProperties": False,
            "required": list(ANALYSIS_ARTIFACTS),
            "properties": form_properties,
        }
    if not root["required"]:
        raise ModelEvaluationAnalysisExamError("NO_EXAM_WRONG_ITEMS")
    return root


def build_exam_directed_repair_package(
    *,
    forms: Mapping[str, Mapping[str, Any]],
    original_responses: Mapping[str, Mapping[str, Any]],
    original_scores: Mapping[str, Mapping[str, Any]],
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze one all-and-only, Gold-free repair round after both Forms score."""

    schema = _require_mapping(schema, "schema")
    exact_sets: dict[str, list[str]] = {}
    repair_targets: dict[str, list[dict[str, Any]]] = {}
    source_form_hashes: dict[str, str] = {}
    source_response_hashes: dict[str, str] = {}
    source_score_hashes: dict[str, str] = {}
    issue_categories: set[str] = set()
    severity_categories: set[str] = set()
    artifact_categories: set[str] = set()
    wrong_item_count = 0

    for form_id in ("A", "B"):
        form = _require_mapping(forms.get(form_id), f"forms.{form_id}")
        response = _require_mapping(
            original_responses.get(form_id),
            f"original_responses.{form_id}",
        )
        score = _require_mapping(
            original_scores.get(form_id),
            f"original_scores.{form_id}",
        )
        cases, case_order = _form_cases(form)
        case_by_id = {item["case_id"]: item for item in cases}
        response_rows: dict[str, dict[str, Mapping[str, Any]]] = {}
        for artifact in ANALYSIS_ARTIFACTS:
            rows = response.get(artifact)
            if not isinstance(rows, list):
                raise ModelEvaluationAnalysisExamError(
                    f"RESPONSE_ARTIFACT_ARRAY_REQUIRED:{form_id}:{artifact}"
                )
            by_id: dict[str, Mapping[str, Any]] = {}
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ModelEvaluationAnalysisExamError(
                        f"RESPONSE_ROW_MAPPING_REQUIRED:{form_id}:{artifact}"
                    )
                case_id = row.get("case_id")
                if not isinstance(case_id, str) or case_id in by_id:
                    raise ModelEvaluationAnalysisExamError(
                        f"RESPONSE_ROW_ID_INVALID:{form_id}:{artifact}:{case_id}"
                    )
                by_id[case_id] = row
            response_rows[artifact] = by_id
        wrong_by_id = {
            item.get("case_id"): item
            for item in _analysis_exam_wrong_details(score)
        }
        unknown = sorted(set(wrong_by_id) - set(case_order))
        if unknown:
            raise ModelEvaluationAnalysisExamError(
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
            (
                issue_codes,
                failed_fields,
                target_artifacts,
                severity,
            ) = _analysis_exam_issue_metadata(detail)
            current_rows: dict[str, Any] = {}
            current_row_hashes: dict[str, str] = {}
            for artifact in ANALYSIS_ARTIFACTS:
                row = response_rows[artifact].get(case_id)
                if row is None:
                    raise ModelEvaluationAnalysisExamError(
                        f"WRONG_ITEM_CURRENT_ROW_MISSING:{form_id}:{artifact}:{case_id}"
                    )
                current_rows[artifact] = deepcopy(dict(row))
                current_row_hashes[artifact] = _sha256(row)
            issue_categories.update(issue_codes)
            severity_categories.add(severity)
            artifact_categories.update(target_artifacts)
            targets.append(
                {
                    "case_id": case_id,
                    "severity": severity,
                    "issue_codes": issue_codes,
                    "failed_output_fields": failed_fields,
                    "artifact_categories": target_artifacts,
                    "source_case": deepcopy(dict(case_by_id[case_id])),
                    "source_case_sha256": _sha256(case_by_id[case_id]),
                    "current_rows": current_rows,
                    "current_rows_sha256": current_row_hashes,
                    "allowed_actions": [
                        "REPLACE_CLAIM_MAP_ROW",
                        "REPLACE_ANALYSIS_SUMMARY_ROW",
                    ],
                    "requires_full_rows_for_both_artifacts": True,
                }
            )
        repair_targets[form_id] = targets
        source_form_hashes[form_id] = _sha256(form)
        source_response_hashes[form_id] = _sha256(response)
        source_score_hashes[form_id] = _sha256(score)

    if wrong_item_count == 0:
        raise ModelEvaluationAnalysisExamError("NO_EXAM_WRONG_ITEMS")
    repair_schema = _analysis_exam_repair_schema(schema, exact_sets)
    contract: dict[str, Any] = {
        "schema_version": ANALYSIS_EXAM_REPAIR_CONTRACT_SCHEMA_VERSION,
        "repair_round_limit": 1,
        "repair_after_both_initial_forms_scored": True,
        "whole_exam_regeneration_forbidden": True,
        "wrong_item_definition": (
            "EVERY_CASE_WITH_FROZEN_INITIAL_LOCAL_SCORE_BELOW_100"
        ),
        "wrong_item_exact_set": exact_sets,
        "wrong_item_count": wrong_item_count,
        "repair_artifact_categories": sorted(artifact_categories),
        "severity_categories": sorted(severity_categories),
        "issue_categories": sorted(issue_categories),
        "source_form_sha256": source_form_hashes,
        "source_response_sha256": source_response_hashes,
        "source_score_sha256": source_score_hashes,
        "source_schema_sha256": _sha256(schema),
        "repair_targets_sha256": _sha256(repair_targets),
        "repair_schema_sha256": _sha256(repair_schema),
        "requires_exact_case_id_order": True,
        "requires_full_post_merge_rescore": True,
        "any_remaining_or_new_wrong_item_disqualifies": True,
        "subject_receives_answer_key": False,
        "provider_receives_answer_key": False,
        "penalty_contract": deepcopy(
            ANALYSIS_EXAM_REPAIR_PENALTY_CONTRACT
        ),
    }
    contract["contract_sha256"] = _sha256(contract)
    prompt_payload = {
        "instruction": (
            "Complete the single directed-repair round for this synthetic "
            "Analysis examination after both Forms have finished. Replace all "
            "and only the listed cases. Return both complete artifact rows for "
            "every listed case exactly once, in the declared Form and case "
            "order, and return no other case. Use source_case as the only "
            "source of truth, current_rows as the starting point, fix every "
            "machine issue_code and failed_output_field, and recheck each "
            "complete case. Do not regenerate either whole Form."
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
            raise ModelEvaluationAnalysisExamError(
                f"EXAM_REPAIR_PROMPT_FORBIDDEN_FIELD:{forbidden}"
            )
    package = {
        "schema_version": ANALYSIS_EXAM_REPAIR_PACKAGE_SCHEMA_VERSION,
        "status": "READY",
        "contract": contract,
        "repair_schema": repair_schema,
        "repair_targets": repair_targets,
        "prompt": prompt,
        "gold_sent_to_subject": False,
        "gold_sent_to_provider": False,
    }
    package["package_sha256"] = _sha256(package)
    return package


def merge_exam_directed_repair(
    *,
    forms: Mapping[str, Mapping[str, Any]],
    original_responses: Mapping[str, Mapping[str, Any]],
    repair_response: Mapping[str, Any],
    schema: Mapping[str, Any],
    repair_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one exact repair response and merge only authorised case rows."""

    repair_contract = _require_mapping(repair_contract, "repair_contract")
    _verify_self_hash(repair_contract, "contract_sha256")
    if (
        repair_contract.get("schema_version")
        != ANALYSIS_EXAM_REPAIR_CONTRACT_SCHEMA_VERSION
    ):
        raise ModelEvaluationAnalysisExamError(
            "EXAM_REPAIR_CONTRACT_SCHEMA_VERSION_MISMATCH"
        )
    if repair_contract.get("source_schema_sha256") != _sha256(schema):
        raise ModelEvaluationAnalysisExamError("EXAM_REPAIR_SCHEMA_HASH_MISMATCH")
    exact_sets = _require_mapping(
        repair_contract.get("wrong_item_exact_set"),
        "repair_contract.wrong_item_exact_set",
    )
    repair_schema = _analysis_exam_repair_schema(schema, exact_sets)
    if repair_contract.get("repair_schema_sha256") != _sha256(repair_schema):
        raise ModelEvaluationAnalysisExamError("EXAM_REPAIR_DYNAMIC_SCHEMA_HASH_MISMATCH")
    errors = sorted(
        Draft202012Validator(repair_schema).iter_errors(repair_response),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    if errors:
        raise ModelEvaluationAnalysisExamError(
            f"EXAM_REPAIR_RESPONSE_SCHEMA_INVALID:{len(errors)}:{errors[0].message}"
        )

    merged_responses: dict[str, dict[str, Any]] = {}
    change_receipts: dict[str, Any] = {}
    for form_id in ("A", "B"):
        form = _require_mapping(forms.get(form_id), f"forms.{form_id}")
        original = _require_mapping(
            original_responses.get(form_id),
            f"original_responses.{form_id}",
        )
        if repair_contract["source_form_sha256"].get(form_id) != _sha256(
            form
        ):
            raise ModelEvaluationAnalysisExamError(
                f"EXAM_REPAIR_FORM_HASH_MISMATCH:{form_id}"
            )
        if repair_contract["source_response_sha256"].get(
            form_id
        ) != _sha256(original):
            raise ModelEvaluationAnalysisExamError(
                f"EXAM_REPAIR_RESPONSE_HASH_MISMATCH:{form_id}"
            )
        _, case_order = _form_cases(form)
        target_ids = list(exact_sets.get(form_id, []))
        merged = deepcopy(dict(original))
        artifact_receipts: dict[str, Any] = {}
        for artifact in ANALYSIS_ARTIFACTS:
            original_rows = original.get(artifact)
            if not isinstance(original_rows, list):
                raise ModelEvaluationAnalysisExamError(
                    f"ORIGINAL_ARTIFACT_ARRAY_REQUIRED:{form_id}:{artifact}"
                )
            original_by_id = {
                row.get("case_id"): row
                for row in original_rows
                if isinstance(row, Mapping)
            }
            if set(original_by_id) != set(case_order):
                raise ModelEvaluationAnalysisExamError(
                    f"ORIGINAL_CASE_EXACT_SET_MISMATCH:{form_id}:{artifact}"
                )
            replacements: dict[str, Mapping[str, Any]] = {}
            if target_ids:
                repair_rows = repair_response[f"form_{form_id}"][artifact]
                observed_ids = [
                    row.get("case_id")
                    if isinstance(row, Mapping)
                    else None
                    for row in repair_rows
                ]
                if observed_ids != target_ids or len(observed_ids) != len(
                    set(observed_ids)
                ):
                    raise ModelEvaluationAnalysisExamError(
                        f"EXAM_REPAIR_CASE_ID_EXACT_SET_MISMATCH:{form_id}:{artifact}"
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
                            "before_sha256": _sha256(original_row),
                            "after_sha256": _sha256(replacement),
                        }
                    )
                else:
                    merged_rows.append(deepcopy(dict(original_row)))
                    retained.append(
                        {
                            "case_id": case_id,
                            "sha256": _sha256(original_row),
                        }
                    )
            merged[artifact] = merged_rows
            for receipt in retained:
                merged_row = next(
                    row
                    for row in merged_rows
                    if row["case_id"] == receipt["case_id"]
                )
                if _sha256(merged_row) != receipt["sha256"]:
                    raise ModelEvaluationAnalysisExamError(
                        f"EXAM_REPAIR_NON_TARGET_ROW_CHANGED:{form_id}:{artifact}:{receipt['case_id']}"
                    )
            artifact_receipts[artifact] = {
                "target_case_ids": target_ids,
                "changed_rows": changed,
                "retained_row_count": len(retained),
                "original_artifact_sha256": _sha256(original_rows),
                "merged_artifact_sha256": _sha256(merged_rows),
            }
        merged_responses[form_id] = merged
        change_receipts[form_id] = artifact_receipts
    return {
        "schema_version": ANALYSIS_EXAM_REPAIR_MERGE_SCHEMA_VERSION,
        "status": "MERGED_EXACT_SET",
        "responses": merged_responses,
        "change_receipts": change_receipts,
        "repair_response_sha256": _sha256(repair_response),
    }


def _analysis_exam_form_penalty(
    *,
    form_case_count: int,
    wrong_case_count: int,
    issue_categories: set[str],
    severity_categories: set[str],
    artifact_categories: set[str],
) -> dict[str, Any]:
    ratio = wrong_case_count / form_case_count
    volume = _interpolate(ratio, REPAIR_SCOPE_PENALTY_KNOTS)
    artifact_penalty = len(artifact_categories) * float(
        ANALYSIS_EXAM_REPAIR_PENALTY_CONTRACT[
            "artifact_category_points"
        ]
    )
    severity_penalty = sum(
        float(
            ANALYSIS_EXAM_REPAIR_PENALTY_CONTRACT[
                "severity_category_points"
            ][item]
        )
        for item in severity_categories
    )
    issue_penalty = min(
        float(
            ANALYSIS_EXAM_REPAIR_PENALTY_CONTRACT[
                "distinct_issue_category_cap"
            ]
        ),
        len(issue_categories)
        * float(
            ANALYSIS_EXAM_REPAIR_PENALTY_CONTRACT[
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
        "artifact_categories": sorted(artifact_categories),
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
    repair_contract: Mapping[str, Any],
    base_scores_by_form: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Rescore the complete exam; any remaining or new wrong item is fatal."""

    repair_contract = _require_mapping(repair_contract, "repair_contract")
    _verify_self_hash(repair_contract, "contract_sha256")
    exact_sets = _require_mapping(
        repair_contract.get("wrong_item_exact_set"),
        "repair_contract.wrong_item_exact_set",
    )
    post_scores: dict[str, dict[str, Any]] = {}
    remaining: dict[str, list[str]] = {}
    new_wrong_items: dict[str, list[str]] = {}
    original_wrong_sets = {
        form_id: set(exact_sets.get(form_id, []))
        for form_id in ("A", "B")
    }
    for form_id in ("A", "B"):
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
        if repair_contract["source_score_sha256"].get(
            form_id
        ) != _sha256(original_score):
            raise ModelEvaluationAnalysisExamError(
                f"EXAM_REPAIR_ORIGINAL_SCORE_HASH_MISMATCH:{form_id}"
            )
        post = score_response(dict(merged), dict(gold), dict(schema))
        post_scores[form_id] = post
        wrong_after = {
            item["case_id"]
            for item in _analysis_exam_wrong_details(post)
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
            "schema_version": ANALYSIS_EXAM_REPAIR_RESULT_SCHEMA_VERSION,
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
                ANALYSIS_EXAM_REPAIR_PENALTY_CONTRACT[
                    "failed_repair_final_score"
                ]
            ),
            "form_scores_after_repair_before_disqualification": {
                form_id: post_scores[form_id]["form_score"]
                for form_id in ("A", "B")
            },
            "provider_blind_formula": True,
        }

    final_form_scores: dict[str, float] = {}
    form_penalties: dict[str, dict[str, Any]] = {}
    form_results: dict[str, Any] = {}
    for form_id in ("A", "B"):
        original_detail_by_id = {
            item["case_id"]: item
            for item in _analysis_exam_wrong_details(
                original_scores[form_id]
            )
        }
        issue_categories: set[str] = set()
        severity_categories: set[str] = set()
        artifact_categories: set[str] = set()
        target_ids = list(exact_sets.get(form_id, []))
        for case_id in target_ids:
            (
                issues,
                _failed_fields,
                artifacts,
                severity,
            ) = _analysis_exam_issue_metadata(
                original_detail_by_id[case_id]
            )
            issue_categories.update(issues)
            severity_categories.add(severity)
            artifact_categories.update(artifacts)
        _, case_order = _form_cases(forms[form_id])
        penalty = _analysis_exam_form_penalty(
            form_case_count=len(case_order),
            wrong_case_count=len(target_ids),
            issue_categories=issue_categories,
            severity_categories=severity_categories,
            artifact_categories=artifact_categories,
        )
        form_penalties[form_id] = penalty
        if base_scores_by_form is None:
            base_score = float(
                score_analysis_form_v2(
                    raw_scoring=post_scores[form_id]
                )["score"]
            )
        else:
            supplied = base_scores_by_form.get(form_id)
            if isinstance(supplied, Mapping):
                supplied = supplied.get("score")
            if isinstance(supplied, bool) or not isinstance(
                supplied, (int, float)
            ):
                raise ModelEvaluationAnalysisExamError(
                    f"EXAM_REPAIR_BASE_SCORE_REQUIRED:{form_id}"
                )
            base_score = float(supplied)
        pre_penalty = (
            min(
                base_score,
                float(
                    ANALYSIS_EXAM_REPAIR_PENALTY_CONTRACT[
                        "successful_repair_ceiling"
                    ]
                ),
            )
            if target_ids
            else base_score
        )
        final = max(0.0, pre_penalty - float(penalty["total"]))
        final_form_scores[form_id] = round(final, 2)
        form_results[form_id] = {
            "post_repair_score_before_penalty": round(base_score, 2),
            "successful_repair_ceiling_applied": (
                bool(target_ids) and pre_penalty < base_score
            ),
            "repair_penalty": penalty,
            "final_form_score": round(final, 2),
            "quality_hard_gate": post_scores[form_id][
                "quality_hard_gate"
            ],
            "critical_count": post_scores[form_id]["critical_count"],
        }
    final_mean = round(
        sum(final_form_scores.values()) / len(final_form_scores),
        2,
    )
    total_penalty = round(
        sum(float(item["total"]) for item in form_penalties.values())
        / len(form_penalties),
        6,
    )
    return {
        "schema_version": ANALYSIS_EXAM_REPAIR_RESULT_SCHEMA_VERSION,
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
            2,
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
        "final_min": round(min(final_form_scores.values()), 2),
        "final_gap": round(
            abs(final_form_scores["A"] - final_form_scores["B"]),
            2,
        ),
        "quality_hard_gate": "PASS",
        "provider_blind_formula": True,
        "successful_repair_score_is_final_score": True,
    }


def _recommendation(score: float) -> tuple[str, str]:
    if score < 60:
        return "REJECT_AS_ANALYSIS_MODEL", "60分以下：拒绝作为分析模型"
    if score < 80:
        return "NOT_RECOMMENDED_AS_ANALYSIS_MODEL", "60至80分以下：不建议作为分析模型"
    return "SUITABLE_AS_ANALYSIS_MODEL", "80分以上：适合作为分析模型"


def publish_analysis_exam_result(
    *,
    provider: str,
    model: str,
    parameters: Mapping[str, Any],
    forms: Mapping[str, Mapping[str, Any]],
    evidence_refs: Sequence[str],
) -> dict[str, Any]:
    """Build the only provider-blind dual-form result allowed by MODEL_EVALUATION."""

    if not isinstance(provider, str) or not provider:
        raise ModelEvaluationAnalysisExamError("PROVIDER_REQUIRED")
    if not isinstance(model, str) or not model:
        raise ModelEvaluationAnalysisExamError("MODEL_REQUIRED")
    parameters = _require_mapping(parameters, "parameters")
    forms = _require_mapping(forms, "forms")
    if set(forms) != {"A", "B"}:
        raise ModelEvaluationAnalysisExamError("FORM_A_AND_B_REQUIRED")
    if not isinstance(evidence_refs, Sequence) or isinstance(evidence_refs, (str, bytes)):
        raise ModelEvaluationAnalysisExamError("EVIDENCE_REFS_LIST_REQUIRED")
    refs = list(evidence_refs)
    if not refs or any(not isinstance(item, str) or not item for item in refs):
        raise ModelEvaluationAnalysisExamError("EVIDENCE_REFS_NONEMPTY_TEXT_REQUIRED")

    normalized_forms = {key: deepcopy(dict(forms[key])) for key in ("A", "B")}
    statuses = [normalized_forms[key].get("status") for key in ("A", "B")]
    executor_invalidated = any(
        isinstance(status, str) and status.startswith("INVALIDATED_EXECUTOR_")
        for status in statuses
    )
    non_delivery = any(status == "FAIL_NONDELIVERY_AFTER_RETRY" for status in statuses)
    scored = statuses == ["SCORED", "SCORED"]

    raw_sum: float | None = None
    total: float | None = None
    form_min: float | None = None
    form_gap: float | None = None
    if executor_invalidated:
        status = "INVALIDATED_EXECUTOR_DEFECT"
        code = "NO_MODEL_CAPABILITY_CONCLUSION_EXECUTOR_DEFECT"
        label = "执行器缺陷使本次考试不可用于模型能力结论"
    elif non_delivery:
        status = "FAIL_NONDELIVERY_AFTER_RETRY"
        code = "REJECT_AS_ANALYSIS_MODEL_NONDELIVERY"
        label = "一次自动重试后仍未交付必需答卷，拒绝作为分析模型"
    elif scored:
        scores = []
        for form_id in ("A", "B"):
            score = normalized_forms[form_id].get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise ModelEvaluationAnalysisExamError(f"FORM_SCORE_REQUIRED:{form_id}")
            score = float(score)
            if not 0 <= score <= 100:
                raise ModelEvaluationAnalysisExamError(f"FORM_SCORE_OUT_OF_RANGE:{form_id}")
            scores.append(score)
        raw_sum = round(sum(scores), 2)
        total = round(raw_sum / 2, 2)
        form_min = round(min(scores), 2)
        form_gap = round(abs(scores[0] - scores[1]), 2)
        status = "SCORED"
        code, label = _recommendation(total)
    else:
        status = "NOT_ASSESSED"
        code = "NO_RECOMMENDATION_TECHNICALLY_UNASSESSED"
        label = "两次考试未形成完整可评估分数"

    result = {
        "schema_version": ANALYSIS_EXAM_RESULT_SCHEMA_VERSION,
        "status": status,
        "provider": provider,
        "model": model,
        "parameters": deepcopy(dict(parameters)),
        "forms": normalized_forms,
        "raw_sum_out_of_200": raw_sum,
        "total_score_out_of_100": total,
        "form_min": form_min,
        "form_gap": form_gap,
        "recommendation_code": code,
        "recommendation_zh": label,
        "recommendation_bands": {
            "reject": "0 <= score < 60",
            "not_recommended": "60 <= score < 80",
            "suitable": "80 <= score <= 100",
        },
        "evidence_refs": refs,
        "provider_blind_formula": True,
        "gold_scoring_required": True,
        "gold_sent_to_subject": False,
        "publisher_reads_private_gold": False,
        "publisher_provider_requests": 0,
        "gold_access_boundary": "MODEL_EVALUATION_LOCAL_SCORING_ONLY",
    }
    result["result_sha256"] = _sha256(result)
    return result
