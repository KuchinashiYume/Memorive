"""The repair prompt of the card reviewer exam: which output fields failed and the prompt that asks for
a repair of them.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping
from memorive.exams.common import _sha256
from memorive.exams.card_reviewer.policy import CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION


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
