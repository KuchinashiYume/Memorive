"""Schemas for three logical ledgers and their shared run-terminal event.

One annual JSONL may contain both ``ledger_record`` and
``run_terminal_event`` records.  Only the former has ``ledger_type`` and
feeds the Attempt, Paper Outcome and Publication read-only business views.
Run lifecycle and acceptance verdict remain orthogonal support-event facts.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .errors import SchemaValidationError

SCHEMA_VERSION = "2.0"
LEGACY_SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_VERSIONS = (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION)
RECORD_TYPES = ("ledger_record", "run_terminal_event")
LEDGER_TYPES = ("attempt", "paper_outcome", "publication")
RECORD_KINDS = ("terminal", "amendment")
LIFECYCLE_STATUSES = ("completed", "aborted", "invalidated")
ACCEPTANCE_VERDICTS = ("PASS", "FAIL", "NOT_ASSESSED")
ATTEMPT_RESULTS = (
    "success",
    "failure",
    "incomplete",
    "error",
    "cancelled",
    "not_assessed",
)
PAPER_OUTCOMES = ("success", "failure", "partial", "not_run")
PUBLICATION_STATUSES = (
    "not_requested",
    "not_published",
    "published",
    "publication_failed",
)
ATTEMPT_AMENDABLE_FIELDS = frozenset(
    {"accounting", "provider_identity", "token_usage", "cost"}
)


def canonical_json(record: dict[str, Any]) -> str:
    """Return the stable representation used for idempotency comparisons."""
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def record_type_of(record: dict[str, Any]) -> str | None:
    """Return the physical-stream type, mapping legacy v1 rows read-only."""
    if record.get("schema_version") == LEGACY_SCHEMA_VERSION:
        return record.get("record_type", "ledger_record")
    return record.get("record_type")


def record_year(record: dict[str, Any]) -> int:
    """Return the timezone-aware creation year after validating the timestamp."""
    field = "terminal_at" if record_type_of(record) == "run_terminal_event" else "created_at"
    return _parse_datetime(record.get(field), field).year


def validate_record(record: dict[str, Any]) -> None:
    """Validate one record without coercion or inference."""
    if not isinstance(record, dict):
        raise SchemaValidationError("record must be an object")
    _require_enum(record, "schema_version", SUPPORTED_SCHEMA_VERSIONS)
    record_type = record_type_of(record)
    if record_type not in RECORD_TYPES:
        raise SchemaValidationError(f"record_type must be one of {RECORD_TYPES}")
    if record_type == "run_terminal_event":
        if record["schema_version"] != SCHEMA_VERSION:
            raise SchemaValidationError("run_terminal_event requires schema_version '2.0'")
        _validate_run_terminal_event(record)
        return

    _validate_ledger_record(record)


def _validate_ledger_record(record: dict[str, Any]) -> None:
    if record["schema_version"] == SCHEMA_VERSION:
        _require_exact(record, "record_type", "ledger_record")
    elif record.get("record_type", "ledger_record") != "ledger_record":
        raise SchemaValidationError("legacy v1 record_type must be ledger_record")
    _require_enum(record, "ledger_type", LEDGER_TYPES)
    _require_enum(record, "record_kind", RECORD_KINDS)
    for field in ("record_id", "run_id"):
        _require_text(record, field)
    _parse_datetime(record.get("created_at"), "created_at")
    _require_text_list(record, "source_evidence_refs", allow_empty=False)

    ledger_type = record["ledger_type"]
    if ledger_type == "attempt":
        _validate_attempt(record)
    elif ledger_type == "paper_outcome":
        _validate_paper_outcome(record)
    else:
        _validate_publication(record)


def _validate_run_terminal_event(record: dict[str, Any]) -> None:
    _require_exact(record, "record_type", "run_terminal_event")
    for field in ("record_id", "run_id"):
        _require_text(record, field)
    if record.get("ledger_type") is not None:
        raise SchemaValidationError(
            "run_terminal_event must not occupy a fourth ledger_type"
        )
    _require_enum(record, "lifecycle_status", LIFECYCLE_STATUSES)
    _require_enum(record, "acceptance_verdict", ACCEPTANCE_VERDICTS)
    _parse_datetime(record.get("terminal_at"), "terminal_at")
    _require_text_list(record, "source_evidence_refs", allow_empty=False)
    _require_optional_text(record, "supersedes_record_id")
    if record.get("supersedes_record_id") == record["record_id"]:
        raise SchemaValidationError("run terminal event cannot supersede itself")
    if (
        record["lifecycle_status"] in {"aborted", "invalidated"}
        and record["acceptance_verdict"] != "NOT_ASSESSED"
    ):
        raise SchemaValidationError(
            "aborted/invalidated runs must use acceptance NOT_ASSESSED"
        )


def _validate_attempt(record: dict[str, Any]) -> None:
    for field in ("attempt_id", "logical_operation_id", "task_id", "stage"):
        _require_text(record, field)
    _require_optional_text(record, "paper_id")
    _require_positive_int(record, "attempt_no")
    _require_optional_text(record, "retry_of_attempt_id")
    _require_optional_text(record, "business_repair_of")
    _require_optional_text(record, "route_snapshot_id")
    _require_optional_text(record, "execution_config_hash")
    if not record.get("route_snapshot_id") and not record.get("execution_config_hash"):
        raise SchemaValidationError(
            "attempt requires route_snapshot_id or execution_config_hash"
        )
    if record.get("retry_of_attempt_id") and record.get("business_repair_of"):
        raise SchemaValidationError(
            "technical retry and business repair are orthogonal; choose one relationship"
        )

    if record["record_kind"] == "terminal":
        if record["record_id"] != record["attempt_id"]:
            raise SchemaValidationError("terminal attempt record_id must equal attempt_id")
        _parse_datetime(record.get("started_at"), "started_at")
        _parse_datetime(record.get("finished_at"), "finished_at")
        _require_enum(record, "result_category", ATTEMPT_RESULTS)
        _require_optional_text(record, "error_code")
    else:
        _require_text(record, "amends_attempt_id")
        if record["attempt_id"] != record["amends_attempt_id"]:
            raise SchemaValidationError(
                "amendment attempt_id must identify the amended attempt"
            )
        fields = record.get("amendment_fields")
        if not isinstance(fields, dict) or not fields:
            raise SchemaValidationError("attempt amendment_fields must be a non-empty object")
        forbidden = set(fields) - ATTEMPT_AMENDABLE_FIELDS
        if forbidden:
            raise SchemaValidationError(
                "sealed attempt fields cannot be amended: " + ", ".join(sorted(forbidden))
            )


def _validate_paper_outcome(record: dict[str, Any]) -> None:
    for field in ("outcome_id", "paper_id", "outcome_code"):
        _require_text(record, field)
    if record["record_id"] != record["outcome_id"]:
        raise SchemaValidationError("paper outcome record_id must equal outcome_id")
    _require_enum(record, "outcome_category", PAPER_OUTCOMES)
    _require_text_list(record, "reason_codes", allow_empty=False)
    _require_text_list(record, "final_artifact_refs", allow_empty=True)
    _require_optional_text(record, "supersedes_record_id")
    _require_optional_text(record, "reprocesses_run_id")
    if record["record_kind"] == "amendment" and not record.get("supersedes_record_id"):
        raise SchemaValidationError(
            "paper outcome amendment requires supersedes_record_id"
        )
    if record["outcome_category"] == "not_run" and not record["reason_codes"]:
        raise SchemaValidationError("not_run requires an explicit reason code")


def _validate_publication(record: dict[str, Any]) -> None:
    _require_text(record, "publication_event_id")
    if record["record_id"] != record["publication_event_id"]:
        raise SchemaValidationError(
            "publication record_id must equal publication_event_id"
        )
    _require_enum(record, "publication_status", PUBLICATION_STATUSES)
    _require_optional_text(record, "manifest_ref")
    _require_text_list(record, "subject_artifact_refs", allow_empty=True)
    _require_optional_text(record, "receipt_ref")
    _require_optional_text(record, "supersedes_record_id")
    if record["schema_version"] == SCHEMA_VERSION:
        _require_optional_text(record, "absence_reason")
    if record["record_kind"] == "amendment" and not record.get("supersedes_record_id"):
        raise SchemaValidationError(
            "publication amendment requires supersedes_record_id"
        )
    if record["publication_status"] == "published":
        if not record.get("manifest_ref") or not record.get("receipt_ref"):
            raise SchemaValidationError(
                "published requires a publication manifest and publication receipt"
            )
        if not record["subject_artifact_refs"]:
            raise SchemaValidationError("published requires subject_artifact_refs")
    status = record["publication_status"]
    if status == "not_requested":
        if record.get("manifest_ref") is not None or record.get("receipt_ref") is not None:
            raise SchemaValidationError(
                "not_requested requires manifest_ref:null and receipt_ref:null"
            )
        if (
            record["schema_version"] == SCHEMA_VERSION
            and record.get("absence_reason") != "NO_PUBLICATION_REQUEST"
        ):
            raise SchemaValidationError(
                "not_requested requires absence_reason NO_PUBLICATION_REQUEST"
            )
    elif status == "not_published":
        if not record.get("manifest_ref") or record.get("receipt_ref") is not None:
            raise SchemaValidationError(
                "not_published requires manifest_ref and receipt_ref:null"
            )
    elif status == "publication_failed" and not record.get("manifest_ref"):
        raise SchemaValidationError("publication_failed requires manifest_ref")
    if status != "not_requested" and record.get("absence_reason") is not None:
        raise SchemaValidationError("absence_reason is reserved for not_requested")


def _require_exact(record: dict[str, Any], field: str, expected: Any) -> None:
    if record.get(field) != expected:
        raise SchemaValidationError(f"{field} must equal {expected!r}")


def _require_enum(record: dict[str, Any], field: str, allowed: tuple[str, ...]) -> None:
    if record.get(field) not in allowed:
        raise SchemaValidationError(f"{field} must be one of {allowed}")


def _require_text(record: dict[str, Any], field: str) -> None:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{field} must be a non-empty string")


def _require_optional_text(record: dict[str, Any], field: str) -> None:
    value = record.get(field)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise SchemaValidationError(f"{field} must be null or a non-empty string")


def _require_text_list(
    record: dict[str, Any], field: str, *, allow_empty: bool
) -> None:
    value = record.get(field)
    if not isinstance(value, list):
        raise SchemaValidationError(f"{field} must be a list")
    if not allow_empty and not value:
        raise SchemaValidationError(f"{field} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise SchemaValidationError(f"{field} entries must be non-empty strings")


def _require_positive_int(record: dict[str, Any], field: str) -> None:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SchemaValidationError(f"{field} must be a positive integer")


def _parse_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{field} must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SchemaValidationError(f"{field} is not valid ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SchemaValidationError(f"{field} must include a timezone")
    return parsed
