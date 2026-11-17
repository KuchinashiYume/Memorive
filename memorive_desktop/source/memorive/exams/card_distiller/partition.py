"""The partition rule of a card projection in the card distiller exam, and its directed patch.

Every source case id of a pack must sit in exactly one of PARTITION_FIELDS. When a card projection
shard fails its audit, QualityCardDistillerExamExecutor._run_shard (executor.py) shows the model
partition_diagnostics with each repair request; in the last round, if cases are missing or doubled, it
asks only for a field per case (partition_decision_targets, partition_patch_schema) and merges the
answer into the current projection (merge_partition_patch).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

PARTITION_FIELDS = (
    "research_question",
    "research_object",
    "method",
    "key_results",
    "author_conclusion",
    "boundary_conditions",
)


def partition_diagnostics(
    current: Mapping[str, Any],
    target_ids: list[str],
    target_source: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Per target pack of a card projection: its source case ids, how many the current projection
    assigns, which are missing, doubled or from elsewhere, and the current partition."""
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
        for field in PARTITION_FIELDS:
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
    """The cases a partition patch must place: each missing case may go to any field, each doubled
    case stays in one of the fields it is in now."""
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
                field for field in PARTITION_FIELDS
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
                        list(PARTITION_FIELDS)
                        if case_id in missing
                        else current_fields
                    ),
                }
            )
    return targets


def partition_patch_schema(
    decision_targets: list[dict[str, Any]],
    target_ids: list[str],
) -> dict[str, Any]:
    """The reply schema of a partition patch: one decision (pack, case, field) per decision target."""
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
                            "enum": list(PARTITION_FIELDS),
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
    """The current projection with each decision applied: cases from outside a pack dropped, each
    decided case removed from every field and added to the field the patch chose."""
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
        for field in PARTITION_FIELDS:
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
            for field in PARTITION_FIELDS:
                row[field]["case_ids"] = [
                    value for value in row[field]["case_ids"]
                    if value != case_id
                ]
            row[selected[(pack_id, case_id)]]["case_ids"].append(case_id)
    return merged
