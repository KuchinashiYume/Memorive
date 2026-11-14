"""Schema contract for the Memorive DECISION_LOG Design Decision Log (DDL)."""

from __future__ import annotations

import copy
import re
from datetime import date, datetime
from typing import Any, Mapping


SCHEMA_VERSION = "1.0"
RECORD_KIND = "design_decision"
DECISION_ID_PATTERN = re.compile(r"^DDL-\d{4}-\d{2}-\d{2}-\d{3}$")

DECISION_TYPES: tuple[str, ...] = (
    "general_design",
    "architecture_selection",
    "terminology",
    "process_governance",
    "sandbox_isolation",
    "execution_config_candidate_admission",
    "quality_quarantine",
    "pilot_enablement",
    "rollback",
    "embedding_new_index",
    "ocr_production_readiness",
    "canonical_pointer_move",
    "lineage_exception",
    "publication_withdrawal",
)

DECISION_STATUSES: tuple[str, ...] = (
    "Proposed",
    "Accepted",
    "Superseded",
    "Deprecated",
)

MODULE_IDS: tuple[str, ...] = tuple(f"M{index:02d}" for index in range(1, 18)) + (
    "CROSS",
    "SHARED",
)

REQUIRED_FIELDS: tuple[str, ...] = (
    "schema_version",
    "record_kind",
    "decision_id",
    "decision_type",
    "title",
    "background",
    "alternatives",
    "selected_alternative_id",
    "final_decision",
    "rejection_reasons",
    "affected_modules",
    "external_feedback",
    "status",
    "review_date",
    "created_at",
    "source_references",
    "version",
    "previous_version",
)

FORBIDDEN_WHY_BOUNDARY_FIELDS: tuple[str, ...] = (
    "event_id",
    "event_type",
    "attempt_id",
    "paper_id",
    "artifact_id",
    "artifact_path",
    "canonical_pointer",
    "runtime_log",
)


class DecisionValidationError(ValueError):
    """Raised when a DDL record violates the frozen Initialization contract."""


def _nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DecisionValidationError(f"{field} must be non-empty text")
    return value.strip()


def _validate_iso_date(value: Any, field: str) -> str:
    text = _nonempty_text(value, field)
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise DecisionValidationError(f"{field} must be an ISO date") from exc
    return text


def _validate_iso_datetime(value: Any, field: str) -> str:
    text = _nonempty_text(value, field)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DecisionValidationError(f"{field} must be an ISO datetime") from exc
    if parsed.tzinfo is None:
        raise DecisionValidationError(f"{field} must include a timezone")
    return text


def validate_decision(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a defensive copy of one versioned DDL record."""

    if not isinstance(record, Mapping):
        raise DecisionValidationError("decision record must be an object")

    keys = set(record)
    missing = set(REQUIRED_FIELDS) - keys
    extras = keys - set(REQUIRED_FIELDS)
    if missing:
        raise DecisionValidationError(f"missing required fields: {sorted(missing)}")
    if extras:
        boundary_hits = sorted(extras & set(FORBIDDEN_WHY_BOUNDARY_FIELDS))
        if boundary_hits:
            raise DecisionValidationError(
                f"DDL stores why, not RUNTIME_LOG events or ARTIFACT_REGISTRY artifacts: {boundary_hits}"
            )
        raise DecisionValidationError(f"unknown fields are forbidden: {sorted(extras)}")

    validated = copy.deepcopy(dict(record))
    if validated["schema_version"] != SCHEMA_VERSION:
        raise DecisionValidationError("unsupported schema_version")
    if validated["record_kind"] != RECORD_KIND:
        raise DecisionValidationError("record_kind must be design_decision")

    decision_id = _nonempty_text(validated["decision_id"], "decision_id")
    if not DECISION_ID_PATTERN.fullmatch(decision_id):
        raise DecisionValidationError("decision_id must match DDL-YYYY-MM-DD-NNN")
    if validated["decision_type"] not in DECISION_TYPES:
        raise DecisionValidationError("illegal decision_type")
    if validated["status"] not in DECISION_STATUSES:
        raise DecisionValidationError("illegal status")

    validated["title"] = _nonempty_text(validated["title"], "title")
    validated["background"] = _nonempty_text(validated["background"], "background")
    validated["final_decision"] = _nonempty_text(
        validated["final_decision"], "final_decision"
    )
    validated["review_date"] = _validate_iso_date(
        validated["review_date"], "review_date"
    )
    validated["created_at"] = _validate_iso_datetime(
        validated["created_at"], "created_at"
    )

    alternatives = validated["alternatives"]
    if not isinstance(alternatives, list) or len(alternatives) < 2:
        raise DecisionValidationError("alternatives must contain at least two choices")
    alternative_ids: set[str] = set()
    for alternative in alternatives:
        if not isinstance(alternative, Mapping) or set(alternative) != {
            "alternative_id",
            "summary",
        }:
            raise DecisionValidationError(
                "each alternative must contain only alternative_id and summary"
            )
        alternative_id = _nonempty_text(
            alternative["alternative_id"], "alternatives[].alternative_id"
        )
        _nonempty_text(alternative["summary"], "alternatives[].summary")
        if alternative_id in alternative_ids:
            raise DecisionValidationError(f"duplicate alternative_id: {alternative_id}")
        alternative_ids.add(alternative_id)

    selected = _nonempty_text(
        validated["selected_alternative_id"], "selected_alternative_id"
    )
    if selected not in alternative_ids:
        raise DecisionValidationError("selected_alternative_id must name an alternative")

    rejection_reasons = validated["rejection_reasons"]
    if not isinstance(rejection_reasons, list) or not rejection_reasons:
        raise DecisionValidationError("rejection_reasons must be non-empty")
    rejected_ids: set[str] = set()
    for rejection in rejection_reasons:
        if not isinstance(rejection, Mapping) or set(rejection) != {
            "alternative_id",
            "reason",
        }:
            raise DecisionValidationError(
                "each rejection reason must contain only alternative_id and reason"
            )
        rejected_id = _nonempty_text(
            rejection["alternative_id"], "rejection_reasons[].alternative_id"
        )
        _nonempty_text(rejection["reason"], "rejection_reasons[].reason")
        if rejected_id not in alternative_ids:
            raise DecisionValidationError(
                f"rejection reason names unknown alternative: {rejected_id}"
            )
        if rejected_id == selected:
            raise DecisionValidationError("selected alternative cannot be rejected")
        if rejected_id in rejected_ids:
            raise DecisionValidationError(f"duplicate rejection reason: {rejected_id}")
        rejected_ids.add(rejected_id)
    expected_rejections = alternative_ids - {selected}
    if rejected_ids != expected_rejections:
        raise DecisionValidationError(
            "every non-selected alternative needs exactly one rejection reason"
        )

    modules = validated["affected_modules"]
    if not isinstance(modules, list) or not modules:
        raise DecisionValidationError("affected_modules must be non-empty")
    if len(set(modules)) != len(modules) or any(module not in MODULE_IDS for module in modules):
        raise DecisionValidationError("affected_modules contains duplicates or illegal IDs")

    feedback = validated["external_feedback"]
    if not isinstance(feedback, list) or not feedback:
        raise DecisionValidationError(
            "external_feedback must be non-empty; record explicit unknown when unavailable"
        )
    for item in feedback:
        _nonempty_text(item, "external_feedback[]")

    references = validated["source_references"]
    if not isinstance(references, list) or not references:
        raise DecisionValidationError("source_references must be non-empty")
    for item in references:
        _nonempty_text(item, "source_references[]")

    version = validated["version"]
    previous_version = validated["previous_version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise DecisionValidationError("version must be a positive integer")
    if version == 1:
        if previous_version is not None:
            raise DecisionValidationError("version 1 must have previous_version=null")
    elif previous_version != version - 1:
        raise DecisionValidationError("previous_version must be version - 1")

    return validated


